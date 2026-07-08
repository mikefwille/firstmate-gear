"""CLI mode selection: watch is the default in a terminal; --snapshot (or a
non-tty stdout - a pipe, a redirect, CI) renders once and exits; --watch still
accepts an interval and degrades to a snapshot off-terminal instead of hanging."""
import sys

import pytest

import fm_status


class _TtyStdout:
    """Wrap the captured stdout, overriding only isatty()."""

    def __init__(self, wrapped, tty: bool):
        self._wrapped = wrapped
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


@pytest.fixture
def cli(fake_home, monkeypatch, capsys):
    """main() against the fake home with run_watch stubbed out; returns
    (exit_code, snapshot_output, watch_intervals_called)."""
    intervals: list[float] = []

    def fake_watch(console, frame, interval):
        intervals.append(interval)
        return 0

    monkeypatch.setattr(fm_status, "run_watch", fake_watch)

    def run(*argv: str, tty: bool):
        monkeypatch.setattr(sys, "stdout", _TtyStdout(sys.stdout, tty))
        monkeypatch.setattr(sys, "argv", ["fm-status", "--home", str(fake_home), *argv])
        code = fm_status.main()
        return code, capsys.readouterr().out, intervals

    return run


def test_bare_run_defaults_to_watch(cli):
    code, out, watched = cli(tty=True)
    assert code == 0
    assert watched == [fm_status.WATCH_INTERVAL_S]
    assert not out                                    # the live board, not a dump


def test_watch_interval_still_overrides(cli):
    code, _out, watched = cli("--watch", "2", tty=True)
    assert code == 0
    assert watched == [2.0]


def test_snapshot_renders_once_and_exits(cli):
    code, out, watched = cli("--snapshot", tty=True)
    assert code == 0
    assert watched == []
    assert "FLEET" in out
    assert "mo-searchable" in out.replace("\n", "")


def test_non_tty_falls_back_to_snapshot(cli):
    """fm-status | grep must never hang a pipe: no tty means one-shot render."""
    code, out, watched = cli(tty=False)
    assert code == 0
    assert watched == []
    assert "FLEET" in out


def test_explicit_watch_degrades_off_terminal(cli):
    code, out, watched = cli("--watch", "2", tty=False)
    assert code == 0
    assert watched == []
    assert "FLEET" in out


def test_snapshot_and_watch_are_mutually_exclusive(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli("--watch", "--snapshot", tty=True)
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_other_flags_work_in_snapshot_mode(cli):
    code, out, _ = cli("--snapshot", "--table", "--no-done", tty=True)
    assert code == 0
    assert "FLEET" in out
    assert "recently done" not in out

    code, out, _ = cli("--snapshot", "--roadmap", tty=True)
    assert code == 0
    assert "ROADMAP" in out
