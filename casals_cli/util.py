"""Shared CLI utilities (Candid encoding, JSON output)."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

# Official install docs — do not embed a command here; the repo is the source.
ICP_CLI_REPO = "https://github.com/dfinity/icp-cli"


def missing_icp_cli_message() -> str:
    return f"casals needs icp-cli on PATH. See {ICP_CLI_REPO}"


def run_icp_cmd(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` for an ``icp …`` argv. Missing binary → repo pointer."""
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError as exc:
        name = cmd[0] if cmd else ""
        if name == "icp" or getattr(exc, "filename", None) in (None, "icp"):
            raise RuntimeError(missing_icp_cli_message()) from exc
        raise

_CANDID_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "'": "'"}


def candid_unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in _CANDID_ESCAPES:
            out.append(_CANDID_ESCAPES[s[i + 1]])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_icp_output(output: str):
    text = output.strip()
    first, last = text.find('"'), text.rfind('"')
    if first != -1 and last > first:
        inner = candid_unescape(text[first + 1:last])
        try:
            return json.loads(inner)
        except Exception:
            return inner
    try:
        return json.loads(text.strip("()").strip())
    except Exception:
        return text


def candid_text_arg(json_str: str) -> str:
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def emit_json(data, *, indent: int | None = 2) -> None:
    print(json.dumps(data, indent=indent, sort_keys=True))


def emit_error(message: str, **extra) -> None:
    payload = {"ok": False, "error": message, **extra}
    print(json.dumps(payload, indent=2), file=sys.stderr)
    sys.exit(1)


def load_json_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def tc_to_cycles(tc: float) -> int:
    return int(float(tc) * 1_000_000_000_000)


def cycles_to_tc(cycles: int) -> float:
    return cycles / 1_000_000_000_000
