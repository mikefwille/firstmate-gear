"""The semaphore truth table: Job.verdict is the product's core logic.
Green = working only. Yellow = paused with nothing waiting on the captain.
Red = the only needs-you color."""
from conftest import make_job


def color(job) -> str:
    return job.verdict[0]


def text(job) -> str:
    return job.verdict[2]


# --- green: working only -----------------------------------------------------

def test_working_is_green():
    assert color(make_job(state="working", kind="ship")) == "green"


def test_running_is_green():
    assert color(make_job(state="running", kind="scout")) == "green"


# --- red: every real ask -----------------------------------------------------

def test_parked_needs_approval():
    j = make_job(state="parked")
    assert (color(j), text(j)) == ("red", "Approval needed")


def test_blocked_and_needs_decision_are_red():
    assert color(make_job(state="blocked")) == "red"
    assert color(make_job(state="needs-decision")) == "red"
    assert color(make_job(state="done", intent="needs-decision")) == "red"


def test_done_with_open_pr_is_merge_ready():
    j = make_job(state="done", intent="done",
                 link="https://github.com/o/r/pull/42")
    assert (color(j), text(j)) == ("red", "Ready - review & merge")


def test_done_scout_report_is_red():
    j = make_job(state="done", intent="done", kind="scout")
    assert color(j) == "red"
    assert "report" in text(j).lower()


def test_holding_for_local_test_is_red():
    # crew-state may reconcile a cancelled-while-holding run to 'failed',
    # but the 'done:' hand-off + NOT-pushed marker means it awaits the captain
    j = make_job(state="failed", intent="done",
                 surfaced="pnpm check green; committed, NOT pushed")
    assert color(j) == "red"


def test_ci_verified_done_is_merge_ready():
    j = make_job(state="done", intent="done", source="run-step")
    assert (color(j), text(j)) == ("red", "Ready - review & merge")


def test_genuine_failure_is_red():
    j = make_job(state="failed")
    assert (color(j), text(j)) == ("red", "Run broke - take a look")


# --- yellow: paused, nothing waiting on the captain --------------------------

def test_unverified_done_stays_yellow():
    # the crew said done, but no PR, not pushed, not CI-verified: watch, don't act
    j = make_job(state="done", intent="done", source="status-log")
    assert color(j) == "yellow"


def test_unknown_state_is_yellow():
    assert color(make_job(state="unknown")) == "yellow"


# --- done is never a semaphore color ------------------------------------------

def test_needs_attention_is_exactly_red():
    assert make_job(state="parked").needs_attention
    assert not make_job(state="working").needs_attention
    assert not make_job(state="unknown").needs_attention
