#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13.7"]
# ///
"""
fm-status - the firstmate fleet board.

One calm, single-column view of every in-flight crew job, reconciled live from
firstmate's on-disk state. Run it in a spare pane and leave it open.

Each job is a card with a 3-color semaphore that answers "how much of my attention
does this want?":
  🟢 green   cranking - actively working; nothing for me. Shows what it's doing.
  🟡 yellow  stopped, no ask yet - it paused but hasn't asked; hopefully self-
             resolving (e.g. crew finished its turn, validation still to run).
  🔴 red     needs me - a real ask (decide / approve / merge / read) OR it broke.
             Shows the action AND the link to get there.

  fm-status                live, scrollable board (jk/↑↓ · space/b page · g/G · q)
  fm-status --snapshot     one-shot render (semaphore cards), then exit
  fm-status --watch 2      live board, redraw every 2s (default 5)
  fm-status --table        dense one-row-per-job table instead of cards
  fm-status --roadmap      per-project timeline: done ✓ · active ●/●/● · queued ☐
  fm-status --roadmap accrete   just the projects matching "accrete"
  fm-status --no-done      hide the "recently done" tail
  FM_HOME=/path fm-status  point at a different firstmate home

When stdout is not a terminal (piped, redirected, CI), fm-status snapshots
once instead of watching, so it never hangs a pipe.

The semaphore maps color to the captain's action, not to internal state;
reconciled truth over log echo (fm-crew-state is authority); quiet when
healthy, clear when not.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.segment import Segment, Segments
from rich.style import Style
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WATCHER_HEALTHY_S = 180          # <= this since last beat = healthy
DONE_DEFAULT = 5                 # recently-done rows shown by default
WATCH_INTERVAL_S = 5.0           # live-board reload cadence unless --watch SECS says otherwise

STYLE = {
    "green": "bright_green",
    "red": "bright_red",
    "amber": "yellow",
    "yellow": "yellow",
    "ink": "default",
    "dim": "grey58",
    "muted": "grey42",
    "accent": "cyan",
    "rule": "grey30",
}

# Per-repo identity tints: a SECOND color channel, deliberately disjoint from the
# semaphore's red/green/yellow so state stays unambiguous. Each repo gets a stable
# slight tint (purple/cyan/gold/blue/…), so you can tell at a glance what a line is from.
REPO_TINTS = ["#b48ead", "#88c0d0", "#d3a95c", "#81a1c1",
              "#c98ab0", "#6fb0a8", "#a3a0d0", "#ca9178"]


def repo_tint(repo: str) -> str:
    if not repo:
        return STYLE["muted"]
    return REPO_TINTS[int(hashlib.md5(repo.encode()).hexdigest(), 16) % len(REPO_TINTS)]


# Failure imperatives (the rest are decided inline in Job.verdict).
ACTION = {
    "failed": "Run broke - take a look",
    "cancelled": "Run cancelled - take a look",
    "canceled": "Run cancelled - take a look",
    "error": "Errored - take a look",
}

# What green jobs are doing, by task kind (the crew's verb).
ACTIVITY_VERB = {"ship": "coding", "scout": "researching"}

# color -> semaphore emoji, shared by the board and the roadmap.
EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def clean_headline(purpose: str, fallback: str = "") -> str:
    """A readable title: strip leading priority codes (P1:/H2:/R1.) and a trailing
    parenthetical, then sentence-case it."""
    h = re.sub(r"^[A-Za-z]{1,2}\d[\d.]*[.:]?\s+", "", purpose)   # P1: / H2: / P1.1
    h = re.sub(r"\s*\([^)]*\)\s*$", "", h).strip()
    return (h[:1].upper() + h[1:]) if h else fallback


@dataclass
class Job:
    jid: str
    tab: str
    win_index: int | None = None
    project: str = ""
    kind: str = ""
    mode: str = ""
    model: str = ""
    effort: str = ""
    yolo: str = "off"
    purpose: str = ""
    state: str = "unknown"       # reconciled word from fm-crew-state
    source: str = "none"         # run-step | pane | status-log | none (see reconcile_state)
    note: str = ""               # transient reconciled note
    since: str = ""
    surfaced: str = ""           # last message firstmate surfaced (the ask, context)
    intent: str = ""             # the surfaced message's prefix: done|needs-decision|blocked|failed
    link: str = ""               # PR url / lavish board / report path to act on

    # --- semaphore ---
    # Color answers "how much of my attention does this want?":
    #   green  cranking                      - actively working, nothing for me
    #   yellow stopped, nothing waiting on me - paused mid-flight, or state unclear
    #   red    forward progress waits on me   - merge / decide / held test / read a report / it broke
    #
    # crew-state answers "is it still working?"; once STOPPED, the captain's action is
    # decided by the surfaced INTENT (firstmate's own words) + a couple markers, NOT by
    # crew-state's coarse state - a run cancelled while holding reconciles to 'failed'
    # but is really a 'done:' hand-off. line_kind drives rendering: activity/waiting/action.
    @cached_property
    def verdict(self) -> tuple[str, str, str]:  # (color, line_kind, text)
        s = self.state.lower()
        if s in ("working", "running"):
            return "green", "activity", self.activity

        # explicit asks - straight to red
        if s == "parked":
            return "red", "action", "Approval needed"
        if s in ("blocked", "needs-decision") or self.intent in ("blocked", "needs-decision"):
            return "red", "action", "Decision needed"

        # done-family: the crew reported it finished, even if crew-state calls a
        # cancelled run 'failed'. Red whenever forward progress is waiting on me.
        if self.intent == "done" or s == "done":
            if self.has_open_pr:                          # an open PR is up for review
                return "red", "action", "Ready - review & merge"
            if self.kind == "scout":                      # a report is mine to read + act on
                return "red", "action", "Read the report"
            if self.is_holding:                           # committed, NOT pushed - awaiting my test
                return "red", "action", "Local test, then it'll push"
            if s == "done" and self.source == "run-step":  # CI actually passed
                return "red", "action", "Ready - review & merge"
            # done, but nothing handed to me yet (no PR, not pushed) - watch, don't act
            return "yellow", "waiting", "stopped · no ask yet"

        # a genuine failure: crew-state failed with no 'done:' hand-off
        if s in ("failed", "cancelled", "error"):
            return "red", "action", ACTION.get(s, "Run broke - take a look")

        return "yellow", "waiting", "state unclear · watching"   # unknown

    @property
    def has_open_pr(self) -> bool:
        """The crew's link points at an open PR / MR waiting for review."""
        return bool(_PR_LINK.search(self.link))

    @property
    def is_holding(self) -> bool:
        """The crew finished but deliberately parked, awaiting my local test / go-ahead."""
        return bool(_HOLDING.search(self.surfaced) or _HOLDING.search(self.note))

    @property
    def color(self) -> str:
        return self.verdict[0]

    @property
    def is_yours(self) -> bool:
        """True when the ball is in the captain's court (red - it needs me)."""
        return self.color == "red"

    @property
    def headline(self) -> str:
        """The bold 'what even is this' line: purpose, minus priority codes/parens."""
        return clean_headline(self.purpose, self.jid)

    @property
    def activity(self) -> str:
        """The green 'what it's doing' line."""
        verb = ACTIVITY_VERB.get(self.kind, "working")
        detail = re.sub(r"\s*\(running\)\s*$", "", self.note).strip()
        return f"{verb} · {detail}" if detail else verb

    # --- kept for the legacy --table view ---
    @property
    def bucket(self) -> str:
        return {"green": "green", "yellow": "amber", "red": "red"}[self.color]

    @property
    def needs_attention(self) -> bool:
        return self.is_yours

    @property
    def glyph(self) -> str:
        return {"green": "●" if self.state != "done" else "✓",
                "red": "✕", "amber": "▲"}[self.bucket]

    @property
    def label(self) -> str:
        return {"needs-decision": "NEEDS DECISION",
                "needs_decision": "NEEDS DECISION"}.get(
            self.state.lower(), self.state.upper())


# ---------------------------------------------------------------------------
# Data layer - read firstmate state, reconcile, order by tmux
# ---------------------------------------------------------------------------

def _run(args: list[str], cwd: Path, timeout: float = 10.0) -> str:
    try:
        out = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return out.stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def parse_meta(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip()
    except OSError:
        pass
    return data


_BACKLOG_ROW = re.compile(r"^-\s*(?:\[.\]\s*)?(\S+)\s*-\s*(.*)$")


def iter_backlog_rows(home: Path) -> list[tuple[str, str, str]]:
    """(section, jid, rest) for every item line in data/backlog.md, read once.
    section is 'flight' | 'queued' | 'done' | '' (any other/prose section).
    The three backlog consumers filter by section, so '' rows are ignored."""
    try:
        lines = (home / "data" / "backlog.md").read_text().splitlines()
    except OSError:
        return []
    section = ""
    rows: list[tuple[str, str, str]] = []
    for line in lines:
        if line.startswith("## "):
            low = line.strip().lower()
            section = ("flight" if low.startswith("## in flight")
                       else "queued" if low.startswith("## queued")
                       else "done" if low == "## done" else "")
            continue
        m = _BACKLOG_ROW.match(line)
        if m:
            rows.append((section, m.group(1), m.group(2)))
    return rows


def backlog_purpose(rest: str) -> tuple[str, str]:
    """(purpose, since) from an in-flight row's text."""
    sm = re.search(r"\(since ([^)]+)\)", rest)
    purpose = re.sub(r"\s*\((?:repo|kind|since|mode)[:\s][^)]*\)", "", rest).strip()
    return purpose, (sm.group(1) if sm else "")


def done_text(rest: str) -> tuple[str, str, str]:
    """(text, tag, repo) from a done row's text - link + metadata parens stripped."""
    tm = re.search(r"\((merged|reported|shipped) ([^)]+)\)", rest)
    rm = re.search(r"\(repo:\s*([^)]+)\)", rest)
    text = re.sub(r"\s*(https?://\S+)", "", rest)
    text = re.sub(r"\s*\((?:repo|kind|since|mode|merged|reported|shipped)[^)]*\)", "", text)
    text = re.sub(r"\s*data/\S+", "", text).strip()
    return text, (tm.group(1) if tm else ""), (rm.group(1).strip() if rm else "")


def parse_projects(home: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    md = home / "data" / "projects.md"
    try:
        for line in md.read_text().splitlines():
            m = re.match(r"^-\s+(\S+)\s*(?:\[[^\]]*\])?\s*-\s*(.*)$", line)
            if m:
                out[m.group(1)] = re.sub(r"\s*\(added [^)]*\)", "", m.group(2)).strip()
    except OSError:
        pass
    return out


def reconcile_state(home: Path, jid: str) -> tuple[str, str, str]:
    """(state, source, note) from bin/fm-crew-state.sh - the authority.

    Line shape: 'state: <s> · source: <run-step|pane|status-log|none> · <detail>'.
    The SOURCE matters: a 'done' from run-step is CI-verified (really merge-ready),
    but a 'done' from status-log is the crewmate's own claim with validation still
    pending - so we keep source to avoid crying 'ready to merge' too early.
    """
    raw = _run(["bin/fm-crew-state.sh", jid], home).splitlines()
    if not raw:
        return "unknown", "none", ""
    parts = [p.strip() for p in raw[0].split("·")]
    state = "unknown"
    m = re.match(r"state:\s*([a-z-]+)", parts[0])
    if m:
        state = m.group(1)
    source = "none"
    if len(parts) > 1:
        sm = re.match(r"source:\s*(\S+)", parts[1])
        if sm:
            source = sm.group(1)
    note = " · ".join(parts[2:]).strip()
    return state, source, note


_LINK_RE = re.compile(r"(https?://\S+|127\.0\.0\.1[:/]\S+|localhost[:/]\S+)")
_PATH_RE = re.compile(r"((?:data|docs)/\S+\.(?:md|html))")
_PR_LINK = re.compile(r"/(?:pull|pulls|merge_requests)/\d")   # an open PR/MR to review
_HOLDING = re.compile(r"not pushed|holding|awaiting your", re.I)  # done but parked for me


def extract_link(text: str, home: Path | None = None, jid: str = "",
                 kind: str = "") -> str:
    """First actionable link in a fragment: a URL, else a doc/report path, else
    (for a scout with home given) the report on disk. Empty string if none."""
    m = _LINK_RE.search(text)
    if m:
        return m.group(1).rstrip(".,);")
    p = _PATH_RE.search(text)
    if p:
        return p.group(1)
    if kind == "scout" and home is not None and (home / "data" / jid / "report.md").exists():
        return f"data/{jid}/report.md"
    return ""


def parse_surfaced(home: Path, jid: str, kind: str) -> tuple[str, str, str]:
    """(intent, message, link) from state/.hb-surfaced-<id> - firstmate's last surfaced ask.

    The file's first line is like 'done: PR https://... ready for review'. The prefix
    ('done'/'needs-decision'/'blocked'/'failed') is firstmate's own intent marker; we
    keep it, plus the message and the first actionable link.
    """
    f = home / "state" / f".hb-surfaced-{jid}"
    try:
        text = f.read_text().strip()
    except OSError:
        text = ""
    first = text.splitlines()[0] if text else ""
    m = re.match(r"^([a-z][a-z-]*):\s*(.*)$", first)
    intent, msg = (m.group(1), m.group(2)) if m else ("", first)
    link = extract_link(msg, home, jid, kind)
    # drop the raw url from the prose so the message reads clean above the link
    if link:
        msg = msg.replace(link, "").replace("()", "")
        msg = re.sub(r"\s{2,}", " ", msg).strip(" -·")
    return intent, msg.strip(), link


def tmux_order(home: Path, session: str) -> list[str]:
    txt = _run(["tmux", "list-windows", "-t", session,
                "-F", "#{window_index} #{window_name}"], home, timeout=4)
    order: list[tuple[int, str]] = []
    for line in txt.splitlines():
        try:
            idx, name = line.split(maxsplit=1)
        except ValueError:
            continue
        if name.startswith("fm-"):
            order.append((int(idx), name[3:]))
    order.sort()
    return [jid for _, jid in order]


@dataclass
class Fleet:
    jobs: list[Job] = field(default_factory=list)
    projects: int = 0
    watcher_age: int | None = None       # seconds since last beat; None = down
    done: list[tuple[str, str, str, str]] = field(default_factory=list)  # (id, text, tag, repo)
    queued: list["RoadmapItem"] = field(default_factory=list)       # what's teed up next

    @property
    def attention(self) -> int:
        return sum(j.needs_attention for j in self.jobs)

    @property
    def watcher(self) -> tuple[str, str]:
        if self.watcher_age is None:
            return "down", "red"
        if self.watcher_age <= WATCHER_HEALTHY_S:
            return "healthy", "green"
        return f"stale {self.watcher_age}s", "amber"


# Board order: what needs the captain floats to the top. Red (act) first,
# yellow (watch) second, green (cranking) last; within a band, tmux tab order
# so rows stay put, then any stragglers alphabetically.
_URGENCY = {"red": 0, "yellow": 1, "green": 2}


def job_sort_key(j: Job) -> tuple:
    return (_URGENCY.get(j.verdict[0], 1),
            j.win_index is None,
            j.win_index if j.win_index is not None else 0,
            j.jid)


def load_fleet(home: Path, done_n: int) -> Fleet:
    fleet = Fleet()
    metas = sorted((home / "state").glob("*.meta"))
    rows = iter_backlog_rows(home)   # backlog.md read once; in-flight + done derived from it
    backlog = {jid: backlog_purpose(rest)
               for section, jid, rest in rows if section == "flight"}
    fleet.projects = len(parse_projects(home))

    # session name comes from any meta's window=<session>:<tab>
    session = ""
    parsed: dict[str, dict[str, str]] = {}
    for m in metas:
        d = parse_meta(m)
        parsed[m.stem] = d
        if not session and ":" in d.get("window", ""):
            session = d["window"].split(":", 1)[0]

    order = tmux_order(home, session) if session else []
    order_index = {jid: i for i, jid in enumerate(order)}

    # each fm-crew-state.sh call is an independent subprocess - run them concurrently
    # rather than serially, so a fleet of N jobs costs one round-trip, not N.
    job_ids = list(parsed)
    if job_ids:
        with ThreadPoolExecutor(max_workers=min(8, len(job_ids))) as ex:
            states = dict(zip(job_ids, ex.map(lambda j: reconcile_state(home, j), job_ids)))
    else:
        states = {}

    for jid, d in parsed.items():
        state, source, note = states[jid]
        purpose, since = backlog.get(jid, ("", ""))
        kind = d.get("kind", "")
        intent, surfaced, link = parse_surfaced(home, jid, kind)
        fleet.jobs.append(Job(
            jid=jid,
            tab=f"fm-{jid}",
            win_index=order_index.get(jid),
            project=os.path.basename(d.get("project", "")),
            kind=kind,
            mode=d.get("mode", ""),
            model=d.get("model", ""),
            effort=d.get("effort", ""),
            yolo=d.get("yolo", "off"),
            purpose=purpose or jid,
            state=state,
            source=source,
            note=note,
            since=since,
            surfaced=surfaced,
            intent=intent,
            link=link,
        ))

    fleet.jobs.sort(key=job_sort_key)

    beat = home / "state" / ".last-watcher-beat"
    if beat.exists():
        fleet.watcher_age = int(time.time() - beat.stat().st_mtime)

    fleet.done = [(jid, *done_text(rest))
                  for section, jid, rest in rows if section == "done"][:done_n]
    fleet.queued = [roadmap_item("queued", jid, rest)
                    for section, jid, rest in rows if section == "queued"]
    return fleet


def load_done(home: Path, n: int) -> list[tuple[str, str, str, str]]:
    """Up to n recently-done rows: (jid, text, tag)."""
    out: list[tuple[str, str, str]] = []
    for section, jid, rest in iter_backlog_rows(home):
        if section != "done":
            continue
        out.append((jid, *done_text(rest)))
        if len(out) >= n:
            break
    return out


@dataclass
class RoadmapItem:
    jid: str
    title: str
    repo: str
    kind: str
    status: str       # done | flight | queued
    link: str = ""


def roadmap_item(section: str, jid: str, rest: str) -> RoadmapItem:
    repo_m = re.search(r"\(repo:\s*([^)]+)\)", rest)
    kind_m = re.search(r"\(kind:\s*([^)]+)\)", rest)
    title = re.sub(r"https?://\S+", "", rest)
    title = re.sub(r"\s*(?:data|docs)/\S+\.(?:md|html)\b", "", title)  # report links only
    title = re.sub(
        r"\s*\((?:repo|kind|since|mode|merged|reported|shipped)[:\s][^)]*\)",
        "", title).strip()
    return RoadmapItem(
        jid=jid, title=clean_headline(title, jid),
        repo=repo_m.group(1).strip() if repo_m else "",
        kind=kind_m.group(1).strip() if kind_m else "",
        status=section, link=extract_link(rest))


def parse_roadmap_items(home: Path) -> list[RoadmapItem]:
    """Every backlog item across In-flight / Queued / Done, tagged by project."""
    return [roadmap_item(section, jid, rest)
            for section, jid, rest in iter_backlog_rows(home) if section]


# ---------------------------------------------------------------------------
# Render layer - calm captain's bridge
# ---------------------------------------------------------------------------

def render_header(fleet: Fleet, width: int) -> Text:
    wtext, wcolor = fleet.watcher
    tally = Counter(j.color for j in fleet.jobs)   # one pass; verdict is cached per job
    compact = width < 72   # a thin side pane: drop the words + the clock, keep counts

    info = Text()          # a wrapping Text, so it never overflows a narrow pane
    info.append("  FLEET   ", style="bold")
    for emoji, color, word in (
        ("🟢", "green", "cranking"),
        ("🟡", "yellow", "paused"),
        ("🔴", "red", "need you"),
    ):
        n = tally[color]
        active = n > 0
        info.append(f"{emoji} ")
        info.append(str(n), style=("bold " + STYLE[color]) if active else STYLE["muted"])
        if not compact:
            info.append(f" {word}", style=STYLE[color] if active else STYLE["muted"])
        info.append("   " if compact else "    ", style=STYLE["rule"])
    # queue depth - so a drying queue is obvious at a glance
    nq = len(fleet.queued)
    if nq:
        info.append(f"{nq} queued", style=STYLE["muted"])
    else:
        info.append("queue dry", style=STYLE["amber"])
    info.append("   " if compact else "    ", style=STYLE["rule"])
    info.append("watcher ", style=STYLE["muted"])
    info.append("● ", style=STYLE[wcolor])
    info.append(wtext, style=STYLE[wcolor])
    if not compact:
        info.append("     ")
        info.append(time.strftime("%H:%M:%S %Z"), style=STYLE["muted"])
    return info


def link_label(url: str) -> str:
    """A short human label for a link so it fits one line (no wrap = stays clickable)."""
    m = re.search(r"/(?:pull|pulls|merge_requests)/(\d+)", url)
    if m:
        return f"PR #{m.group(1)}"
    if url.endswith((".md", ".html")):
        return url.rsplit("/", 1)[-1]        # the report filename
    if "127.0.0.1" in url or "localhost" in url:
        return "open board"
    return url


def compact_ask(msg: str, max_chars: int = 320) -> str:
    """Shrink a verbatim crewmate ask to a board-sized blurb so cards stay glanceable and
    the whole board fits (the alt-screen Live crops overflow, so a too-tall card hides the
    ones below it). Drop the 'Run parked ... awaiting respond' tail (redundant with the red
    state), then if still long keep the lead plus the 'Options:' clause - the actual decision
    - eliding the middle rationale. The full ask always lives in the crewmate's own pane."""
    if not msg:
        return msg
    # '... Run [<id>] parked at <gate> awaiting respond.' - redundant with the red state.
    msg = re.sub(r"\s*Run\b[^.]*?\bparked\b[^.]*\.?\s*$", "", msg).strip()
    if len(msg) <= max_chars:
        return msg
    om = re.search(r"(Options:.*)$", msg, re.S)
    if om:
        opts = om.group(1).strip()
        lead = msg[:om.start()].strip()
        budget = max(0, max_chars - len(opts) - 2)
        if len(lead) > budget:
            lead = lead[:budget].rstrip() + " …"
        return f"{lead} {opts}".strip() if lead else opts
    return msg[:max_chars].rstrip() + " …"


def job_block(job: Job) -> Group:
    """One single-column card: semaphore + id, bold headline, then either what it's
    doing (green), why it paused (yellow), or your move + the link (red). The status
    prose wraps with a hanging indent, but a long ask is compacted (see compact_ask)
    so one card can't push the rest of the board past the crop line."""
    color_name, line_kind, text = job.verdict
    color = STYLE[color_name]

    def indented(t: Text, left: int) -> Padding:
        # pad all lines (incl. wrapped) left, and 1 on the right for margin
        return Padding(t, (0, 1, 0, left))

    idline = Text()
    idline.append(f"{EMOJI[color_name]}  ")
    idline.append(job.tab, style=f"bold {color}")
    if job.project:
        idline.append("  ·  ", style=STYLE["rule"])
        idline.append(job.project, style=repo_tint(job.project))

    head = Text(job.headline, style=f"bold {STYLE['ink']}")
    lines: list = [idline, indented(head, 3), Text("")]

    if line_kind == "action":
        act = Text()
        act.append("→ ", style=f"bold {color}")
        act.append(text, style=f"bold {color}")
        lines.append(indented(act, 3))
        if job.surfaced:
            lines.append(indented(Text(compact_ask(job.surfaced), style=STYLE["dim"]), 5))
        if job.link:
            # OSC 8 hyperlink on a compact label: clickable to the FULL url even though
            # the visible text is short, and never wraps (a wrapped url isn't clickable).
            link = Text(f"{link_label(job.link)}  ↗",
                        style=f"{STYLE['accent']} underline link {job.link}",
                        no_wrap=True, overflow="ellipsis")
            lines.append(indented(link, 5))
    else:
        # green activity / yellow waiting: one status line, plus context when paused
        lines.append(indented(Text(text, style=color), 3))
        if line_kind == "waiting" and job.surfaced:
            lines.append(indented(Text(compact_ask(job.surfaced), style=STYLE["dim"]), 5))
    return Group(*lines)


def render_column(fleet: Fleet, width: int) -> Group:
    if not fleet.jobs:
        return Group(Text("   no jobs in flight", style=STYLE["muted"]))
    divider = Text("─" * width, style=STYLE["rule"])
    blocks: list = []
    for i, job in enumerate(fleet.jobs):
        if i:
            blocks += [Text(""), divider, Text("")]
        blocks.append(job_block(job))
    return Group(*blocks)


def job_cell(job: Job) -> Group:
    head = Text(job.tab, style="bold", no_wrap=True, overflow="ellipsis")
    purpose = Text(job.purpose, style=STYLE["dim"], no_wrap=True, overflow="ellipsis")
    lines = [head, purpose]
    if job.note:
        live = Text(no_wrap=True, overflow="ellipsis")
        live.append("live ", style=f"bold {STYLE['accent']}")
        live.append(job.note, style=STYLE["muted"])
        lines.append(live)
    return Group(*lines)


def render_table(fleet: Fleet) -> Table:
    table = Table(
        box=None,
        show_header=False,
        show_edge=False,
        expand=True,
        pad_edge=False,
        padding=(0, 2, 1, 0),          # bottom pad = breathing room between jobs
    )
    table.add_column(width=2, no_wrap=True)                        # state dot
    table.add_column(width=15, no_wrap=True)                       # state label
    table.add_column(ratio=1, min_width=28)                        # job
    table.add_column(width=16, no_wrap=True, overflow="ellipsis")  # project
    table.add_column(width=6, no_wrap=True)                        # kind
    table.add_column(width=11, no_wrap=True, overflow="ellipsis")  # model

    if not fleet.jobs:
        table.add_row("", Text("no jobs in flight", style=STYLE["muted"]), "", "", "", "")
        return table

    for job in fleet.jobs:
        color = STYLE[job.bucket]
        dot = Text(job.glyph, style=color)
        state = Text(job.label, style=f"bold {color}", no_wrap=True, overflow="ellipsis")
        model = job.model + (f"/{job.effort}" if job.effort and job.effort != "default" else "")
        if job.yolo == "on":
            model += " ⚡"
        kind = Text(job.kind, style=STYLE["muted"])
        table.add_row(
            dot, state, job_cell(job),
            Text(job.project, style=repo_tint(job.project), no_wrap=True, overflow="ellipsis"),
            kind,
            Text(model, style=STYLE["muted"], no_wrap=True, overflow="ellipsis"),
        )
    return table


def render_up_next(fleet: Fleet) -> Group:
    """What's queued next - visible even while jobs run, so a drying queue shows."""
    if not fleet.queued:
        empty = Text("  queue is empty - nothing teed up", style=STYLE["amber"])
        return Group(Text("  up next", style=f"bold {STYLE['muted']}"), empty)
    lines: list = [Text("  up next", style=f"bold {STYLE['muted']}")]
    for it in fleet.queued:
        row = Text()
        row.append("☐ ", style=STYLE["muted"])
        row.append(it.title, style=STYLE["dim"])
        if it.repo:
            row.append("  · ", style=STYLE["rule"])
            row.append(it.repo, style=repo_tint(it.repo))
        lines.append(Padding(row, (0, 1, 0, 2)))
    return Group(*lines)


def render_done(fleet: Fleet) -> Group | None:
    if not fleet.done:
        return None
    lines: list = [Text("  recently done", style=f"bold {STYLE['muted']}")]
    for _jid, text, tag, repo in fleet.done:
        row = Text()
        row.append("✓ ", style=STYLE["green"])
        if repo:
            row.append(f"{repo}  ", style=repo_tint(repo))   # per-repo identity tint
        row.append(text, style=STYLE["dim"])
        if tag:
            row.append(f"  {tag}", style=STYLE["muted"])
        lines.append(Padding(row, (0, 1, 0, 2)))   # wrap with a hanging indent
    return Group(*lines)


def render(fleet: Fleet, console: Console, show_done: bool,
           view: str = "column") -> Group:
    width = console.width
    rule = Text("─" * width, style=STYLE["rule"])
    body = render_table(fleet) if view == "table" else render_column(fleet, width)
    blocks: list = [
        rule,
        render_header(fleet, width),
        rule,
        Text(""),
        body,
        Text(""), rule, Text(""),
        render_up_next(fleet),
    ]
    if show_done:
        done = render_done(fleet)
        if done is not None:
            blocks += [Text(""), rule, Text(""), done]
    return Group(*blocks)


def roadmap_row(item: RoadmapItem, color_map: dict[str, str]) -> tuple[Text, Text]:
    """(glyph, title) for one roadmap row.
    done ✓ (settled, dim) · active semaphore (now, bold) · queued ☐ (ahead, dim)."""
    if item.status == "done":
        return Text("✓", style=STYLE["green"]), Text(item.title, style=STYLE["dim"])
    if item.status == "flight":
        color = color_map.get(item.jid, "yellow")
        return (Text(EMOJI[color]),
                Text(item.title, style=f"bold {STYLE['ink']}"))
    return Text("☐", style=STYLE["muted"]), Text(item.title, style=STYLE["muted"])


def render_roadmap(fleet: Fleet, home: Path, width: int,
                   only: str | None = None) -> Group:
    items = parse_roadmap_items(home)
    color_map = {j.jid: j.color for j in fleet.jobs}

    order = list(parse_projects(home).keys())     # projects.md order
    for it in items:                              # then any repo only seen in backlog
        if it.repo and it.repo not in order:
            order.append(it.repo)
    if only:
        order = [p for p in order if only.lower() in p.lower()]

    rule = Text("─" * width, style=STYLE["rule"])
    head = Table.grid(expand=True)
    head.add_column(justify="left")
    head.add_column(justify="right")
    head.add_row(Text("  ROADMAP", style="bold"),
                 Text(time.strftime("%H:%M %Z") + "  ", style=STYLE["muted"]))
    blocks: list = [rule, head, rule]

    any_project = False
    for proj in order:
        pit = [it for it in items if it.repo == proj]
        if not pit:
            continue
        any_project = True
        done = [it for it in pit if it.status == "done"][::-1]   # oldest first = journey
        flight = [it for it in pit if it.status == "flight"]
        queued = [it for it in pit if it.status == "queued"]

        title = Text()
        title.append(f"  {proj}", style="bold")
        title.append(
            f"      {len(done)} done · {len(flight)} active · {len(queued)} queued",
            style=STYLE["muted"])
        blocks += [Text(""), title]

        grid = Table.grid(expand=True, padding=(0, 1, 0, 0))
        grid.add_column(width=2, no_wrap=True)   # glyph
        grid.add_column(ratio=1)                 # title (wraps under itself)
        for it in done + flight + queued:
            grid.add_row(*roadmap_row(it, color_map))
        blocks.append(Padding(grid, (0, 0, 0, 2)))

    if not any_project:
        blocks.append(Text("\n  no roadmap items", style=STYLE["muted"]))
    return Group(*blocks)


# ---------------------------------------------------------------------------
# Scrolling watch loop
# ---------------------------------------------------------------------------

# Terminal escape sequences → logical keys (arrows / page / home-end).
_ESC_KEYS = {
    "\x1b[A": "UP",   "\x1b[B": "DOWN",
    "\x1b[5~": "PGUP", "\x1b[6~": "PGDN",
    "\x1b[H": "HOME", "\x1b[F": "END",
    "\x1bOH": "HOME", "\x1bOF": "END",
}


def _read_key(stream) -> str:
    """One keypress from a cbreak-mode stream. Collapses an escape sequence
    (arrow/page/home-end) into a logical name; returns the raw char otherwise."""
    import select as _select
    ch = stream.read(1)
    if ch != "\x1b":
        return ch
    seq = ch
    while True:
        ready, _, _ = _select.select([stream], [], [], 0.02)
        if not ready:
            break
        seq += stream.read(1)
        if seq[-1].isalpha() or seq[-1] == "~":   # sequence terminator
            break
    return _ESC_KEYS.get(seq, seq)


def _window(console: Console, renderable, top: int) -> tuple[Segments, int, int]:
    """Render `renderable` to full-width lines and return a screen-height slice
    starting at `top` (clamped), plus the clamped top and total line count. When
    the content overflows, the last screen row is a persistent scroll indicator."""
    width, height = console.size
    lines = console.render_lines(
        renderable, console.options.update(width=width, height=None), pad=True)
    total = len(lines)
    overflow = total > height
    avail = height - 1 if overflow else height
    top = max(0, min(top, max(0, total - avail)))
    rows: list[list[Segment]] = list(lines[top:top + avail])
    blank = [Segment(" " * width)]
    while len(rows) < avail:
        rows.append(list(blank))
    if overflow:
        last = min(top + avail, total)
        up, dn = ("▲" if top > 0 else " "), ("▼" if last < total else " ")
        hint = (f" {up}{dn}  {top + 1}-{last}/{total}   "
                f"jk/↑↓ · space/b page · g/G ends · q quit")
        rows.append([Segment(hint.ljust(width)[:width],
                             Style.parse("grey42 reverse"))])
    segs: list[Segment] = []
    for i, row in enumerate(rows):
        if i:
            segs.append(Segment("\n"))
        segs.extend(row)
    return Segments(segs), top, total


def run_watch(console: Console, frame, interval: float) -> int:
    """Live board with a scrollable viewport. Data reloads every `interval`s;
    scroll keys respond instantly without waiting on (or resetting) that clock.
    Falls back to a plain cropped auto-refresh when stdin isn't a terminal."""
    if not sys.stdin.isatty():
        with Live(frame(), console=console, screen=True, auto_refresh=True,
                  refresh_per_second=4, vertical_overflow="crop") as live:
            while True:
                time.sleep(max(0.5, interval))
                live.update(frame())

    import termios
    import tty
    import select as _select
    fd = sys.stdin.fileno()
    old_attr = termios.tcgetattr(fd)
    top = 0
    current = frame()
    try:
        tty.setcbreak(fd)
        with Live(console=console, screen=True, auto_refresh=False,
                  vertical_overflow="crop") as live:
            due = time.monotonic() + interval
            while True:
                view, top, total = _window(console, current, top)
                live.update(view, refresh=True)
                page = max(1, console.size[1] - 3)
                ready, _, _ = _select.select(
                    [sys.stdin], [], [], max(0.0, due - time.monotonic()))
                if ready:
                    key = _read_key(sys.stdin)
                    if key in ("q", "Q"):
                        break
                    top += {"j": 1, "DOWN": 1, "k": -1, "UP": -1,
                            " ": page, "f": page, "PGDN": page,
                            "b": -page, "PGUP": -page}.get(key, 0)
                    if key in ("g", "HOME"):
                        top = 0
                    elif key in ("G", "END"):
                        top = total          # clamped in _window
                else:
                    current = frame()         # data refresh on the interval
                    due = time.monotonic() + interval
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attr)
    return 0


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def find_home() -> Path | None:
    """Walk up from cwd looking for a firstmate home (AGENTS.md + the crew-state
    script the board relies on). Lets `cd your-firstmate && fm-status` just work,
    including from a subdirectory or a secondmate home."""
    d = Path.cwd()
    for cand in (d, *d.parents):
        if (cand / "bin" / "fm-crew-state.sh").is_file() and (cand / "AGENTS.md").exists():
            return cand
    return None


def resolve_home(cli_home: Path | None, prog: str) -> Path | None:
    """--home > $FM_HOME > walk-up from cwd. None (with a message) if nothing works."""
    env = os.environ.get("FM_HOME")
    home = cli_home or (Path(env) if env else find_home())
    if home is None:
        print(f"{prog}: not inside a firstmate home - cd into one, "
              f"set $FM_HOME, or pass --home /path/to/firstmate", file=sys.stderr)
    return home


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="fm-status", description="firstmate fleet board")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--watch", nargs="?", const=WATCH_INTERVAL_S, type=float, default=None,
                      metavar="SECS", help="live scrollable board (the default in a terminal), "
                      f"reload every SECS (default {WATCH_INTERVAL_S:g}); "
                      "scroll jk/↑↓ · space/b · g/G · q to quit")
    mode.add_argument("--snapshot", action="store_true",
                      help="render once and exit (automatic when stdout isn't a terminal)")
    ap.add_argument("--home", type=Path, default=None,
                    help="firstmate home (default: $FM_HOME, else auto-detected "
                    "from the current directory)")
    ap.add_argument("--table", action="store_true",
                    help="dense one-row-per-job table instead of the default cards")
    ap.add_argument("--roadmap", action="store_true",
                    help="per-project roadmap: done ✓ · active ● · queued ☐")
    ap.add_argument("project", nargs="?", default=None,
                    help="with --roadmap, show only this project")
    ap.add_argument("--no-done", action="store_true", help="hide recently-done tail")
    ap.add_argument("--done", type=int, default=DONE_DEFAULT,
                    metavar="N", help=f"recently-done rows (default {DONE_DEFAULT})")
    args = ap.parse_args()

    home = resolve_home(args.home, "fm-status")
    if home is None:
        return 2
    if not (home / "state").is_dir():
        print(f"fm-status: no firstmate home at {home} "
              f"(set FM_HOME or pass --home)", file=sys.stderr)
        return 2

    console = Console()
    show_done = not args.no_done
    view = "roadmap" if args.roadmap else ("table" if args.table else "column")

    def frame() -> Group:
        fleet = load_fleet(home, args.done)
        if view == "roadmap":
            return render_roadmap(fleet, home, console.width, args.project)
        return render(fleet, console, show_done, view)

    # Watch is the default, but only into a real terminal: piped/redirected output
    # (fm-status | grep, CI) snapshots once so it never hangs a pipe - even when
    # --watch was passed explicitly.
    if args.snapshot or not sys.stdout.isatty():
        console.print(frame())
        return 0

    # Alternate screen (like top/htop): the whole pane is repainted every refresh,
    # so a terminal RESIZE redraws cleanly. run_watch adds a scrollable viewport on
    # top so a tall fleet stays fully reachable instead of cropping off the bottom.
    return run_watch(console, frame,
                     args.watch if args.watch is not None else WATCH_INTERVAL_S)


if __name__ == "__main__":
    raise SystemExit(main())
