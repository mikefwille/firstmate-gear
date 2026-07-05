"""Render smoke + a full load_fleet integration against the synthetic home.
Rendering must never crash, must degrade color-free, and nothing may be color-only."""
from rich.console import Console

import fm_status
from conftest import make_job


def synthetic_fleet() -> fm_status.Fleet:
    fleet = fm_status.Fleet(projects=1, watcher_age=40)
    fleet.jobs = [
        make_job(jid="a", tab="fm-a", state="working", kind="ship",
                 project="proj", purpose="Green: cranking"),
        make_job(jid="b", tab="fm-b", state="done", intent="done",
                 source="status-log", project="proj", purpose="Yellow: no ask"),
        make_job(jid="c", tab="fm-c", state="failed", intent="failed",
                 project="proj", purpose="Red: it broke",
                 surfaced="failed: migration exploded"),
    ]
    fleet.done = [("d1", "Shipped earlier", "merged", "proj")]
    fleet.queued = [fm_status.RoadmapItem(
        jid="q1", title="Next up", repo="proj", kind="ship", status="queued")]
    return fleet


def render_to_text(fleet, width=100) -> str:
    console = Console(record=True, width=width, force_terminal=False)
    console.print(fm_status.render(fleet, console, show_done=True))
    return console.export_text()


def test_render_shows_all_three_colors_without_color():
    out = render_to_text(synthetic_fleet())
    # NO_COLOR-safe: the emoji + spelled-out action survive, nothing is color-only
    assert "🟢" in out and "🟡" in out and "🔴" in out
    assert "Green: cranking" in out
    assert "Run broke" in out
    assert "recently done" in out


def test_render_survives_narrow_pane():
    assert render_to_text(synthetic_fleet(), width=40)


def test_header_counts_by_color():
    out = render_to_text(synthetic_fleet())
    assert "FLEET" in out and "1 cranking" in out and "1 need you" in out


def test_roadmap_renders_past_present_future(fake_home):
    fleet = synthetic_fleet()
    console = Console(record=True, width=100, force_terminal=False)
    console.print(fm_status.render_roadmap(fleet, fake_home, 100, None))
    out = console.export_text()
    assert "health-s1" not in out          # roadmap shows titles, not raw ids
    assert "/api/health endpoint" in out   # 'SB1' priority code is stripped by design
    assert "Analyzer A3: scoring engine" in out


def test_board_orders_by_urgency_then_tab():
    jobs = [
        make_job(jid="g", tab="fm-g", win_index=0, state="working"),          # green
        make_job(jid="y", tab="fm-y", win_index=1, state="unknown"),          # yellow
        make_job(jid="r2", tab="fm-r2", win_index=3, state="failed"),         # red, later tab
        make_job(jid="r1", tab="fm-r1", win_index=2, state="parked"),         # red, earlier tab
        make_job(jid="s", tab="fm-s", win_index=None, state="working"),       # green straggler
    ]
    ordered = [j.jid for j in sorted(jobs, key=fm_status.job_sort_key)]
    assert ordered == ["r1", "r2", "y", "g", "s"]   # red > yellow > green; tab order within


def test_load_fleet_integration(fake_home):
    """The full pipeline against the fake home: meta + fake crew-state script."""
    fleet = fm_status.load_fleet(fake_home, done_n=5)
    assert [j.jid for j in fleet.jobs] == ["mo-searchable-p1"]
    job = fleet.jobs[0]
    assert job.state == "working" and job.source == "run-step"
    assert job.verdict[0] == "green"
    assert job.purpose.startswith("P1: make model output searchable")
    assert job.project == "accrete-alpha"
    assert fleet.watcher[0] == "healthy"         # fresh beat file
    assert len(fleet.queued) == 2
    assert fleet.done[0][0] == "health-s1"
    # end-to-end render of the loaded fleet
    console = Console(record=True, width=100, force_terminal=False)
    console.print(fm_status.render(fleet, console, show_done=True))
    assert "mo-searchable" in console.export_text().lower().replace("\n", "")
