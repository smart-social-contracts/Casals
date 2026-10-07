"""Where `casals up` writes its full log, and what the terminal shows.

The terminal gets a short step summary. Every line, including that summary
and the old detailed progress, is appended to one file per run under
`$CASALS_HOME/logs` (default `~/.casals/logs`). `--verbose` echoes the
detail to the terminal as well.
"""

from __future__ import annotations

import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime

_current: "RunLog | None" = None


def _stamp(msg: str) -> str:
    if not msg:
        return ""
    return f"{datetime.now().strftime('%H:%M:%S')}  {msg}"


def display_path(path: str) -> str:
    """`~/...` when the file lives under the home directory."""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def log_file_path(sheet_name: str, env: str, *, now: datetime | None = None) -> str:
    """A new file under the Casals home: `logs/<sheet>-<env>-YYYYmmdd-HHMMSS.log`."""
    from casals_cli.bindings import bindings_dir

    moment = now or datetime.now()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", sheet_name).strip("-") or "orchestra"
    safe_env = re.sub(r"[^A-Za-z0-9._-]+", "-", env).strip("-") or "env"
    directory = os.path.join(bindings_dir(), "logs")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{safe}-{safe_env}-{moment.strftime('%Y%m%d-%H%M%S')}.log")
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{safe}-{safe_env}-{moment.strftime('%Y%m%d-%H%M%S')}-{n}.log")
        n += 1
    return path


class RunLog:
    def __init__(self, path: str, *, verbose: bool = False):
        self.path = path
        self.verbose = verbose
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self._fh = os.fdopen(fd, "a", encoding="utf-8")

    def detail(self, msg: str) -> None:
        line = _stamp(msg)
        self._fh.write(line + "\n")
        self._fh.flush()
        if self.verbose:
            print(line, file=sys.stderr, flush=True)

    def summary(self, msg: str) -> None:
        line = _stamp(msg)
        print(line, file=sys.stderr, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh.closed:
            return
        self._fh.close()


def open_run_log(sheet_name: str, env: str, *, verbose: bool = False) -> RunLog:
    """Start the run log. A later `detail` / `summary` writes here until `close_run_log`."""
    global _current
    close_run_log()
    log = RunLog(log_file_path(sheet_name, env), verbose=verbose)
    _current = log
    log.detail(f"casals log {display_path(log.path)}")
    return log


@contextmanager
def logged_run(sheet_name: str, env: str, *, verbose: bool = False):
    """Open the run log for the body, then always print where it was written."""
    log = open_run_log(sheet_name, env, verbose=verbose)
    try:
        yield log
    except Exception as exc:
        detail(f"failed: {exc}")
        raise
    finally:
        summary("")
        summary("Log")
        summary(f"  {display_path(log.path)}")
        close_run_log()


def close_run_log() -> None:
    global _current
    if _current is not None:
        _current.close()
        _current = None


def detail(msg: str) -> None:
    """Full progress: the log file, and the terminal only with `--verbose`."""
    if _current is None:
        print(msg, file=sys.stderr, flush=True)
        return
    _current.detail(msg)


def summary(msg: str) -> None:
    """A line the operator always sees. Also written into the log file."""
    if _current is None:
        print(_stamp(msg), file=sys.stderr, flush=True)
        return
    _current.summary(msg)
