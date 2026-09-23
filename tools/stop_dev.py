"""Stop this checkout's Vite/Uvicorn processes and their children, regardless of port.

Uses ps/lsof on macOS or Linux. Matching the working directory as well as the server command
keeps unrelated applications and other checkouts safe. No credentials or dependencies needed.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import NamedTuple


class Process(NamedTuple):
    """Process identity used to avoid signalling a PID reused by a different command."""

    pid: int
    parent: int
    state: str
    command: str


def processes() -> dict[int, Process]:
    """Read process metadata without printing command lines, which may contain secrets."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,stat=,command="], capture_output=True, text=True, check=True
    )
    found = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) == 4:
            pid, parent, state, command = fields
            found[int(pid)] = Process(int(pid), int(parent), state, command)
    return found


def is_server(command: str) -> bool:
    """Match server executables, excluding shell wrappers containing server commands."""
    # ps does not quote paths containing spaces. Allow those paths, but do not consume
    # shell options (such as `sh -c ...`) as part of an executable path.
    path = r"(?:/(?:(?!\s-)[^\n])*?/)?"
    match = re.match(
        rf"^(?:{path}(?:python[\d.]*|node)(?:\s+-m)?\s+)?"
        rf"{path}(uvicorn|vite(?:\.js)?)(?:\s|$)",
        command,
    )
    if match is None:
        return False
    name = match.group(1)
    return name in {"vite", "vite.js"} or (
        name == "uvicorn" and "sentiment_prep.api.app:app" in command.split()
    )


def find_servers(root: Path, snapshot: dict[int, Process]) -> dict[int, Process]:
    """Identify this project's server launchers, then include their reload/helper children."""
    candidates = [
        p.pid
        for p in snapshot.values()
        if is_server(p.command) and p.pid != os.getpid() and "Z" not in p.state
    ]
    if not candidates:
        return {}
    result = subprocess.run(
        ["lsof", "-a", "-p", ",".join(map(str, candidates)), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        check=False,  # A candidate may exit between ps and lsof.
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("Could not inspect development server working directories")
    allowed = {root.resolve(), (root / "backend").resolve(), (root / "frontend").resolve()}
    selected: dict[int, Process] = {}
    pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid in snapshot and Path(line[1:]).resolve() in allowed:
            selected[pid] = snapshot[pid]
    while True:
        children = {
            p.pid: p for p in snapshot.values() if p.parent in selected and "Z" not in p.state
        }
        if children.keys() <= selected.keys():
            return selected
        selected.update(children)


def remaining(targets: dict[int, Process]) -> dict[int, Process]:
    """Ignore exited/zombie processes and PIDs now owned by a different command."""
    current = processes()
    return {
        pid: current[pid]
        for pid, old in targets.items()
        if pid in current and current[pid].command == old.command and "Z" not in current[pid].state
    }


def send(targets: dict[int, Process], sig: signal.Signals) -> None:
    """Signal only still-matching processes; disappearing processes are already stopped."""
    for pid in remaining(targets):
        with suppress(ProcessLookupError):
            os.kill(pid, sig)


def stop_servers(root: Path) -> int:
    """Try graceful shutdown, resume suspended jobs, then force-stop remaining processes."""
    targets = find_servers(root, processes())
    if not targets:
        print("No project development servers are running.")
        return 0
    print(f"Stopping {len(targets)} project development processes...", flush=True)
    send(targets, signal.SIGTERM)
    # Ctrl-Z suspends signal handling. Continue so the pending termination can be handled.
    send(targets, signal.SIGCONT)
    deadline = time.monotonic() + 3
    while remaining(targets) and time.monotonic() < deadline:
        time.sleep(0.1)
    if remaining(targets):
        send(targets, signal.SIGKILL)
        time.sleep(0.1)
    if remaining(targets) or find_servers(root, processes()):
        print("Some development servers are still running. Run make stop again.", file=sys.stderr)
        return 1
    print("Project development servers stopped; their ports are free.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(stop_servers(Path(__file__).resolve().parents[1]))
    except (OSError, subprocess.SubprocessError, RuntimeError):
        print(
            "Could not stop development servers. Check permissions and that ps/lsof are installed.",
            file=sys.stderr,
        )
        sys.exit(1)
