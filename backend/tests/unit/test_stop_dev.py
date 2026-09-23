"""Project-scoped development server shutdown."""

import importlib.util
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "stop_dev", Path(__file__).resolve().parents[3] / "tools" / "stop_dev.py"
)
stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("node /Users/me/NLP Web App/frontend/node_modules/.bin/vite --port 5175", True),
        (
            "/Users/me/NLP Web App/.venv/bin/python /Users/me/NLP Web App/.venv/bin/uvicorn sentiment_prep.api.app:app --reload",
            True,
        ),
        ("python3 -m uvicorn sentiment_prep.api.app:app --reload", True),
        ("uvicorn other.app:app --port 8000", False),
        ("/bin/sh -c node /repo/frontend/node_modules/.bin/vite", False),
        ("node /repo/vite-project/server.js", False),
        ("/usr/bin/python3 worker.py", False),
    ],
)
def test_server_commands(command, expected):
    assert stop.is_server(command) is expected


def test_selects_checkout_servers_and_children_only(tmp_path, monkeypatch):
    root = tmp_path / "NLP Web App"
    snapshot = {
        101: stop.Process(101, 1, "S", "node /repo/vite"),
        102: stop.Process(102, 1, "S", "node /other/vite"),
        103: stop.Process(103, 101, "S", "esbuild service"),
        104: stop.Process(104, 103, "S", "worker"),
        105: stop.Process(105, 1, "S", "postgres"),
        106: stop.Process(106, 1, "Z", "node /repo/vite"),
    }

    def lsof(args, **kwargs):
        assert args[3] == "101,102"
        return SimpleNamespace(
            returncode=0, stdout=f"p101\nn{root}/frontend\np102\nn{tmp_path}/other/frontend\n"
        )

    monkeypatch.setattr(stop.subprocess, "run", lsof)
    assert set(stop.find_servers(root, snapshot)) == {101, 103, 104}


def test_does_not_signal_reused_pids_or_zombies(monkeypatch):
    targets = {101: stop.Process(101, 1, "S", "vite"), 102: stop.Process(102, 1, "S", "vite")}
    monkeypatch.setattr(
        stop,
        "processes",
        lambda: {
            101: stop.Process(101, 1, "S", "unrelated"),
            102: stop.Process(102, 1, "Z", "vite"),
        },
    )
    monkeypatch.setattr(stop.os, "kill", lambda *args: pytest.fail("must not signal"))
    stop.send(targets, signal.SIGTERM)


def test_resumes_suspended_jobs_and_force_stops_stubborn_jobs(tmp_path, monkeypatch):
    targets = {101: stop.Process(101, 1, "T", "vite")}
    signals = []
    monkeypatch.setattr(stop, "processes", lambda: targets.copy())
    monkeypatch.setattr(stop, "find_servers", lambda *args: targets.copy())
    monkeypatch.setattr(stop.time, "monotonic", iter([0, 4]).__next__)
    monkeypatch.setattr(stop.time, "sleep", lambda _: None)

    def kill(pid, sig):
        signals.append(sig)
        if sig == signal.SIGKILL:
            targets.pop(pid)

    monkeypatch.setattr(stop.os, "kill", kill)
    assert stop.stop_servers(tmp_path) == 0
    assert signals == [signal.SIGTERM, signal.SIGCONT, signal.SIGKILL]
    assert stop.stop_servers(tmp_path) == 0
