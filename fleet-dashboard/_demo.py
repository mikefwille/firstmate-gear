#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13.7"]
# ///
"""Dev harness: render a synthetic all-three-colors fleet to eyeball the design."""
import importlib.util
import sys
from pathlib import Path

from rich.console import Console

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("fm_status", HERE / "fm_status.py")
fm = importlib.util.module_from_spec(spec)
sys.modules["fm_status"] = fm
spec.loader.exec_module(fm)

fleet = fm.Fleet(projects=2, watcher_age=40)
fleet.jobs = [
    # green: cranking
    fm.Job(jid="mo-searchable-p1", tab="fm-mo-searchable-p1", win_index=0,
           project="accrete-alpha", kind="ship", state="working", source="run-step",
           purpose="P1: make model output first-class searchable (schema+ingest)",
           note="validating (running)"),
    # red: an open PR is up for review (crew-state 'monitoring' -> status-log)
    fm.Job(jid="mo-backfill-p1-1", tab="fm-mo-backfill-p1-1", win_index=1,
           project="accrete-alpha", kind="ship", state="done", source="status-log",
           intent="done", surfaced="checks green",
           purpose="P1.1 fast-follow: backfill historical tool_result stored_text",
           link="https://github.com/accrete-labs/accrete/pull/40"),
    # red: HOLDING - failed/cancelled run but a done: message; awaiting my local test
    fm.Job(jid="tts-reader-t1", tab="fm-tts-reader-t1", win_index=2,
           project="lavish-axi", kind="ship", state="failed", source="run-step",
           intent="done", surfaced="pnpm check green; committed, NOT pushed",
           purpose="Read-aloud: top-bar play + word-synced highlight (port ElevenLabs)"),
    # red: a real failure
    fm.Job(jid="ingest-wire-i2", tab="fm-ingest-wire-i2", win_index=3,
           project="accrete-alpha", kind="ship", state="failed", source="run-step",
           intent="failed", surfaced="migration 0011 failed: column already exists",
           purpose="Wire the ingest endpoint"),
    # yellow: stopped, nothing waiting on me yet (no PR, not pushed)
    fm.Job(jid="settings-scaffold-s1", tab="fm-settings-scaffold-s1", win_index=4,
           project="accrete-alpha", kind="ship", state="done", source="status-log",
           intent="done", surfaced="ready to validate",
           purpose="Scaffold the settings page"),
]
# synthetic done tail: (jid, text, tag, repo), same shape as load_done()
fleet.done = [
    ("mo-schema-p0", "P0: model output schema + ingest path", "merged", "accrete-alpha"),
    ("health-check-s1", "SB1 /api/health endpoint", "merged", "accrete-alpha"),
    ("ci-audit-a1", "Deep audit: CI + validation gate coverage", "reported", "accrete-alpha"),
    ("read-aloud-r1", "Read-aloud playback spike", "shipped", "lavish-axi"),
]
fleet.queued = [
    fm.RoadmapItem(jid="analyzer-engine-a3", title="Analyzer A3: build the scoring engine",
                   repo="accrete-alpha", kind="ship", status="queued"),
    fm.RoadmapItem(jid="library-shelf-l1", title="Library: shelf view + filters",
                   repo="accrete-alpha", kind="ship", status="queued"),
]

console = Console(record=True, width=int(sys.argv[1]) if len(sys.argv) > 1 else 118)
console.print(fm.render(fleet, console, show_done=True))
console.save_svg(str(HERE / "_shot.svg"), title="fm-status  ·  demo")
print("wrote _shot.svg")
