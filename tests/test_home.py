"""Home resolution: --home > $FM_HOME > walk up from cwd, helpful error otherwise."""
from pathlib import Path

import fm_status


def test_find_home_from_home_root(fake_home, no_fm_home, monkeypatch):
    monkeypatch.chdir(fake_home)
    assert fm_status.find_home() == fake_home


def test_find_home_from_subdirectory(fake_home, no_fm_home, monkeypatch):
    monkeypatch.chdir(fake_home / "data")
    assert fm_status.find_home() == fake_home


def test_find_home_outside_a_home(tmp_path, no_fm_home, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert fm_status.find_home() is None


def test_resolve_prefers_cli_over_env(fake_home, monkeypatch, tmp_path):
    monkeypatch.setenv("FM_HOME", str(tmp_path))
    assert fm_status.resolve_home(fake_home, "fm-status") == fake_home


def test_resolve_prefers_env_over_cwd(fake_home, monkeypatch, tmp_path):
    monkeypatch.chdir(fake_home)
    monkeypatch.setenv("FM_HOME", str(tmp_path))
    assert fm_status.resolve_home(None, "fm-status") == Path(tmp_path)


def test_resolve_none_prints_guidance(no_fm_home, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert fm_status.resolve_home(None, "fm-status") is None
    err = capsys.readouterr().err
    assert "FM_HOME" in err and "--home" in err


def test_fm_read_find_home_matches(fake_home, no_fm_home, monkeypatch):
    import fm_read
    monkeypatch.chdir(fake_home / "bin")
    assert fm_read.find_home() == fake_home
