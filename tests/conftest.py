"""Test bootstrap: make the two single-file tools importable, and provide a
synthetic firstmate home that exercises the real on-disk contract."""
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "fleet-dashboard"))
sys.path.insert(0, str(ROOT / "reading-room"))


BACKLOG = """\
# Backlog

## In flight
- [ ] mo-searchable-p1 - P1: make model output searchable (repo: accrete-alpha) (kind: ship) (since 2026-07-01)

## Queued
- [ ] analyzer-a3 - Analyzer A3: scoring engine (repo: accrete-alpha) (kind: ship)
- [ ] library-l1 - Library: shelf view (repo: lavish) (kind: ship)

## Done
- [x] health-s1 - SB1 /api/health endpoint (repo: accrete-alpha) (merged 2026-07-02) https://github.com/o/r/pull/12
"""

PROJECTS = "- accrete-alpha - The accrete project (added 2026-06-01)\n"

# Mimics bin/fm-crew-state.sh's one-line contract:
#   state: <s> · source: <run-step|pane|status-log|none> · <detail>
CREW_STATE = """\
#!/bin/sh
echo "state: working · source: run-step · coding"
"""


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "firstmate"
    (home / "bin").mkdir(parents=True)
    (home / "data").mkdir()
    (home / "state").mkdir()
    (home / "AGENTS.md").write_text("# agent manual\n")
    script = home / "bin" / "fm-crew-state.sh"
    script.write_text(CREW_STATE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    (home / "data" / "backlog.md").write_text(BACKLOG)
    (home / "data" / "projects.md").write_text(PROJECTS)
    task = home / "data" / "mo-searchable-p1"
    task.mkdir()
    (task / "brief.md").write_text("# Brief\nMake model output searchable.\n")
    (home / "state" / "mo-searchable-p1.meta").write_text(
        "window=work:fm-mo-searchable-p1\n"
        "project=projects/accrete-alpha\n"
        "kind=ship\nmode=no-mistakes\nmodel=opus\neffort=high\n"
    )
    (home / "state" / ".last-watcher-beat").write_text("")
    return home


@pytest.fixture
def no_fm_home(monkeypatch):
    """Tests that exercise cwd detection must not inherit the runner's $FM_HOME."""
    monkeypatch.delenv("FM_HOME", raising=False)
    return None


@pytest.fixture(autouse=True)
def _no_color(monkeypatch):
    """Deterministic render output regardless of the runner's terminal."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def make_job(**kw):
    import fm_status
    defaults = dict(jid="j1", tab="fm-j1")
    defaults.update(kw)
    return fm_status.Job(**defaults)
