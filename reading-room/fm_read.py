#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual>=0.80"]
# ///
"""
fm-read - the firstmate reading room.

A terminal reading room for everything the crew has written: briefs, scout
reports, and the fleet's own docs (backlog, captain, digest, decisions).
The fleet board (../fleet-dashboard) shows what's happening now; this is where
you sit down and read what the crew produced.

  fm-read                 open the reading room
  fm-read <query>         open with the first doc matching <query> selected
  FM_HOME=/path fm-read   read a different firstmate home

Keys:  ↑/↓ move · enter open · t table-of-contents · r reload · q quit

Design (calm captain's bridge, see ../DESIGN.md): dark and restrained; the
sidebar is the map, the page is the focus. Color carries meaning only - a job's
fleet state (● in flight, ✓ done) and nothing decorative.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, MarkdownViewer, Tree
from textual.widgets.tree import TreeNode

def find_home() -> Path | None:
    """Walk up from cwd looking for a firstmate home (AGENTS.md + the crew-state
    script). Lets `cd your-firstmate && fm-read` just work, including from a
    subdirectory or a secondmate home."""
    d = Path.cwd()
    for cand in (d, *d.parents):
        if (cand / "bin" / "fm-crew-state.sh").is_file() and (cand / "AGENTS.md").exists():
            return cand
    return None


# Fleet orientation docs, in the order a captain wants them, if present.
FLEET_DOCS = [
    ("backlog.md", "backlog"),
    ("morning-digest.md", "morning digest"),
    ("captain.md", "captain"),
    ("projects.md", "projects"),
    ("accrete-context.md", "accrete context"),
    ("accrete-modeloutput-decision.md", "model-output decision"),
]

# How a doc filename reads in the sidebar, and its type glyph.
DOC_KINDS = {
    "brief.md": ("brief", "›"),
    "report.md": ("report", "◆"),
    "local-test.md": ("local test", "·"),
}


@dataclass
class Doc:
    path: Path
    label: str
    glyph: str


def humanize(name: str) -> str:
    stem = re.sub(r"^direction-", "", name.removesuffix(".md"))
    return stem.replace("-", " ")


def classify_doc(path: Path) -> Doc:
    name = path.name
    if name in DOC_KINDS:
        label, glyph = DOC_KINDS[name]
    elif name.startswith("direction-"):
        label, glyph = humanize(name), "»"
    else:
        label, glyph = humanize(name), "·"
    return Doc(path, label, glyph)


def backlog_states(home: Path) -> dict[str, str]:
    """task id -> 'flight' | 'done', parsed from data/backlog.md sections."""
    states: dict[str, str] = {}
    md = home / "data" / "backlog.md"
    try:
        lines = md.read_text().splitlines()
    except OSError:
        return states
    section = ""
    for line in lines:
        if line.startswith("## "):
            low = line.strip().lower()
            section = "flight" if low.startswith("## in flight") else (
                "done" if low == "## done" else "")
            continue
        if not section:
            continue
        m = re.match(r"^-\s*(?:\[.\]\s*)?(\S+)\s*-", line)
        if m:
            states.setdefault(m.group(1), section)
    return states


@dataclass
class Task:
    tid: str
    state: str            # flight | done | unknown
    docs: list[Doc]

    @property
    def glyph_markup(self) -> tuple[str, str]:
        return {"flight": ("●", "state-flight"),
                "done": ("✓", "state-done")}.get(self.state, ("•", "state-idle"))


def scan(home: Path) -> tuple[list[Doc], list[Task]]:
    data = home / "data"
    states = backlog_states(home)

    fleet: list[Doc] = []
    for fname, label in FLEET_DOCS:
        p = data / fname
        if p.exists():
            fleet.append(Doc(p, label, "▸"))

    tasks: list[Task] = []
    for d in sorted(p for p in data.iterdir() if p.is_dir()):
        docs = [classify_doc(f) for f in sorted(d.glob("*.md"))]
        if not docs:
            continue
        # brief first, report second, then the rest as-is
        order = {"brief": 0, "report": 1}
        docs.sort(key=lambda x: order.get(x.label, 2))
        tasks.append(Task(d.name, states.get(d.name, "unknown"), docs))

    # in-flight tasks first, then done, then unknown; alpha within each
    rank = {"flight": 0, "done": 1, "unknown": 2}
    tasks.sort(key=lambda t: (rank.get(t.state, 3), t.tid))
    return fleet, tasks


class ReadingRoom(App):
    CSS = """
    Screen { background: $background; }

    #sidebar {
        width: 38;
        border-right: solid $panel-lighten-2;
        background: $surface;
        padding: 0 1;
    }
    #sidebar:focus-within { border-right: solid $accent; }

    Tree {
        background: $surface;
        padding: 1 0;
    }
    Tree > .tree--guides { color: $surface-lighten-2; }
    Tree > .tree--guides-hover { color: $surface-lighten-2; }
    Tree > .tree--cursor { background: $accent 25%; color: $text; }

    MarkdownViewer { background: $background; }
    Markdown { background: $background; padding: 1 3 2 3; }
    MarkdownH1 { color: $text; text-style: bold; }
    .state-flight { color: $success; }
    .state-done   { color: $success-darken-1; }
    .state-idle   { color: $text-muted; }

    Footer { background: $surface; }
    """

    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("t", "toggle_toc", "contents"),
        Binding("r", "reload", "reload"),
        Binding("j,down", "cursor_down", "", show=False),
        Binding("k,up", "cursor_up", "", show=False),
    ]

    def __init__(self, home: Path, query: str | None = None):
        super().__init__()
        self.home = home
        self.query_str = query
        self.fleet: list[Doc] = []
        self.tasks: list[Task] = []
        self._first: TreeNode | None = None

    def compose(self) -> ComposeResult:
        tree: Tree[Doc] = Tree("firstmate", id="tree")
        tree.show_root = False
        tree.guide_depth = 3
        yield Header(show_clock=False)
        with Horizontal():
            yield tree_container(tree)
            yield MarkdownViewer(show_table_of_contents=False, id="viewer")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "tokyo-night"
        self.title = "firstmate"
        self.sub_title = "reading room"
        self.fleet, self.tasks = scan(self.home)
        self._build_tree()

    def _build_tree(self) -> None:
        tree = self.query_one("#tree", Tree)
        tree.clear()
        root = tree.root
        self._first = None   # tree.clear() invalidated any node from a prior build

        target = (self.query_str or "").lower()

        fleet_node = root.add(Text("FLEET", style="bold"), expand=True)
        for doc in self.fleet:
            self._add_doc(fleet_node, doc, target)

        flight = [t for t in self.tasks if t.state == "flight"]
        rest = [t for t in self.tasks if t.state != "flight"]

        if flight:
            hdr = root.add(Text("IN FLIGHT", style="bold"), expand=True)
            for task in flight:
                self._add_task(hdr, task, target)
        if rest:
            hdr = root.add(Text("REPORTS & DONE", style="bold"), expand=True)
            for task in rest:
                self._add_task(hdr, task, target)

        # select the query match, else the first fleet doc (morning digest-ish)
        first = self._first or (fleet_node.children[0] if fleet_node.children else None)
        if first is not None:
            parent = first.parent
            while parent is not None:          # reveal a match inside a collapsed task
                parent.expand()
                parent = parent.parent
            tree.select_node(first)
            tree.move_cursor(first)
            self._open(first.data)
        tree.focus()

    def _add_task(self, parent: TreeNode, task: Task, target: str) -> None:
        glyph, style = task.glyph_markup
        label = Text.assemble((f"{glyph} ", style), (task.tid, "bold"))
        node = parent.add(label, expand=(task.state == "flight"))
        for doc in task.docs:
            self._add_doc(node, doc, target, prefix=task.tid)

    def _add_doc(self, parent: TreeNode, doc: Doc, target: str, prefix: str = "") -> None:
        label = Text.assemble((f"{doc.glyph} ", "dim"), doc.label)
        node = parent.add_leaf(label, data=doc)
        if target and self._first is None:
            hay = f"{prefix} {doc.label} {doc.path.name}".lower()
            if all(word in hay for word in target.split()):
                self._first = node

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        self._open(event.node.data)

    def _open(self, doc: object) -> None:
        if not isinstance(doc, Doc):
            return
        viewer = self.query_one("#viewer", MarkdownViewer)
        self.sub_title = doc.path.name
        self.run_worker(viewer.document.load(doc.path), exclusive=True)

    # --- actions ---
    def action_toggle_toc(self) -> None:
        viewer = self.query_one("#viewer", MarkdownViewer)
        viewer.show_table_of_contents = not viewer.show_table_of_contents

    def action_reload(self) -> None:
        self.fleet, self.tasks = scan(self.home)
        self._build_tree()

    def action_cursor_down(self) -> None:
        self.query_one("#tree", Tree).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#tree", Tree).action_cursor_up()


def tree_container(tree: Tree):
    from textual.containers import VerticalScroll
    box = VerticalScroll(tree, id="sidebar")
    return box


def main() -> int:
    ap = argparse.ArgumentParser(prog="fm-read", description="firstmate reading room")
    ap.add_argument("--home", type=Path, default=None,
                    help="firstmate home (default: $FM_HOME, else auto-detected "
                    "from the current directory)")
    ap.add_argument("query", nargs="*",
                    help="open the first doc matching these words")
    args = ap.parse_args()

    env = os.environ.get("FM_HOME")
    home = args.home or (Path(env) if env else find_home())
    if home is None:
        print("fm-read: not inside a firstmate home - cd into one, "
              "set $FM_HOME, or pass --home /path/to/firstmate", file=sys.stderr)
        return 2
    query = " ".join(args.query) or None

    if not (home / "data").is_dir():
        print(f"fm-read: no firstmate data at {home}/data "
              f"(set FM_HOME or pass --home)", file=sys.stderr)
        return 2

    ReadingRoom(home, query).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
