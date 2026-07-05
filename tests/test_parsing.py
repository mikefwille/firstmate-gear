"""On-disk contract parsing: backlog sections, done rows, watcher freshness."""
import fm_status


def test_backlog_sections(fake_home):
    rows = fm_status.iter_backlog_rows(fake_home)
    by_section = {}
    for section, jid, _rest in rows:
        by_section.setdefault(section, []).append(jid)
    assert by_section["flight"] == ["mo-searchable-p1"]
    assert by_section["queued"] == ["analyzer-a3", "library-l1"]
    assert by_section["done"] == ["health-s1"]


def test_backlog_purpose_strips_metadata():
    purpose, since = fm_status.backlog_purpose(
        "P1: make model output searchable (repo: accrete-alpha) (kind: ship) (since 2026-07-01)")
    assert purpose == "P1: make model output searchable"
    assert since == "2026-07-01"


def test_done_text_extracts_tag_repo_and_strips_link():
    text, tag, repo = fm_status.done_text(
        "SB1 /api/health endpoint (repo: accrete-alpha) (merged 2026-07-02) "
        "https://github.com/o/r/pull/12")
    assert text == "SB1 /api/health endpoint"
    assert tag == "merged"
    assert repo == "accrete-alpha"
    assert "http" not in text


def test_clean_headline_strips_priority_codes():
    assert fm_status.clean_headline("P1: fix the thing (repo: x)") == "Fix the thing"
    assert fm_status.clean_headline("", "fallback") == "fallback"


def test_missing_backlog_is_empty_not_crash(tmp_path):
    assert fm_status.iter_backlog_rows(tmp_path) == []


def test_watcher_freshness():
    assert fm_status.Fleet(watcher_age=None).watcher == ("down", "red")
    assert fm_status.Fleet(watcher_age=60).watcher == ("healthy", "green")
    state, color = fm_status.Fleet(watcher_age=999).watcher
    assert state.startswith("stale") and color == "amber"
