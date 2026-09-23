"""IC access layer: icp-cli subprocess + anonymous read_state via ic-py."""

from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Protocol

from casals_cli.replica import icp_project_args, network_url as replica_network_url, replica_home
from casals_cli.util import candid_text_arg, parse_icp_output, run_icp_cmd

NETWORK_URLS = {
    "local": "http://127.0.0.1:8000",
    "ic": "https://icp0.io",
}

# Mainnet answers a long `up` with the occasional 502 from the boundary node, a
# `read_state` that never comes back, or an ingress expiry ("The request timed
# out"). Those are retried, with backoff, for the operations that are safe to
# repeat: calls (a repeated `apply` meets the conductor's stale-plan check),
# status reads, settings updates (idempotent), balance reads. Never `canister
# create` (a second create is another 2 TC deposit), `top-up`, `install` or
# `cycles transfer`.
RETRYABLE_OPS = frozenset({("canister", "call"), ("canister", "status"), ("canister", "settings"), ("cycles", "balance")})
TRANSIENT_ATTEMPTS = 5
_TRANSIENT_MARKERS = (
    "502", "503", "504", "bad gateway", "service unavailable", "gateway timeout",
    "timed out", "error sending request", "error reading a body", "connection reset",
    "connection closed", "read_state", "temporarily unavailable", "operation was canceled",
    "no route to host", "dns error",
)


def icp_failure_detail(argv: list[str], stdout: str, stderr: str) -> str:
    """Keep the canister trap line. The Rust backtrace otherwise fills the tail."""
    err = stderr or ""
    marker = ""
    for needle in ("Failed to execute Python code:", "Panicked at", "Canister called `ic0.trap`"):
        i = err.find(needle)
        if i >= 0:
            marker = err[i:].split("\n", 1)[0][:500]
            break
    tail = err[-800:]
    head = f"{marker}\n" if marker and marker not in tail else ""
    return (
        f"icp {' '.join(argv)} failed:\n"
        f"stdout: {(stdout or '')[-400:]}\nstderr: {head}{tail}"
    )


def is_transient_ic_error(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _TRANSIENT_MARKERS)


_PIN_FILES: list[str] = []


def hsm_pin_hint(output: str) -> str | None:
    """How to feed icp a PIV PIN when it cannot prompt (stdout is captured).

    icp ignores ``DFX_HSM_PIN``; casals reads it (or ``ICP_IDENTITY_PASSWORD_FILE``)
    and passes ``--identity-password-file``. Do not put a real PIN in this string."""
    text = output or ""
    if "User PIN is required" not in text and "failed to load HSM identity" not in text:
        return None
    if (os.environ.get("DFX_HSM_PIN") or "").strip():
        return None
    if (os.environ.get("ICP_IDENTITY_PASSWORD_FILE") or "").strip():
        return None
    return (
        "YubiKey / HSM identity: User PIN is required, and icp cannot prompt "
        "because casals captures its output.\n"
        "\n"
        "Type this with a leading space so bash does not store the PIN "
        "(needs HISTCONTROL=ignorespace or ignoreboth):\n"
        "\n"
        " export DFX_HSM_PIN='<your PIV PIN>'\n"
        "\n"
        "Then re-run the same casals command. casals reads DFX_HSM_PIN and "
        "passes it to icp as --identity-password-file."
    )


def _hsm_pin_file() -> str | None:
    """Path to a PIN file icp will accept, or None.

    icp ignores ``DFX_HSM_PIN`` and cannot prompt when stdout is captured.
    ``ICP_IDENTITY_PASSWORD_FILE`` is used as-is; otherwise a 0600 tempfile
    is created from ``DFX_HSM_PIN`` for this process."""
    explicit = (os.environ.get("ICP_IDENTITY_PASSWORD_FILE") or "").strip()
    if explicit:
        return explicit
    pin = os.environ.get("DFX_HSM_PIN") or ""
    if not pin:
        return None
    tmp = tempfile.NamedTemporaryFile("w", prefix="casals-hsm-pin-", delete=False)
    os.chmod(tmp.name, 0o600)
    tmp.write(pin)
    tmp.close()
    _PIN_FILES.append(tmp.name)
    return tmp.name


def _cleanup_pin_files() -> None:
    for path in _PIN_FILES:
        try:
            os.unlink(path)
        except OSError:
            pass
    _PIN_FILES.clear()


atexit.register(_cleanup_pin_files)


class IcAccess(Protocol):
    env: str
    identity: str | None
    project_root: str
    network_url: str

    def deployer_principal(self) -> str: ...
    def read_controllers(self, canister_id: str) -> list[str]: ...
    def read_module_hash(self, canister_id: str) -> str | None: ...
    def canister_exists(self, canister_id: str) -> bool: ...
    def query(self, canister_id: str, method: str, text_arg: str | None = None) -> Any: ...
    def call_update(self, canister_id: str, method: str, text_arg: str | None = None, *, timeout: int = 300) -> Any: ...
    def call_candid(self, canister_id: str, method: str, arg: bytes, *, query: bool = False, timeout: int = 300) -> bytes: ...
    def icp(self, argv: list[str], *, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[str]: ...
    def deployer_cycles_balance(self) -> int | None: ...
    def icp_project(self, project_dir: str, argv: list[str], *, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[str]: ...
    def canister_cycles(self, canister_id: str) -> int | None: ...
    def top_up(self, canister_id: str, cycles: int) -> None: ...


class IcClient:
    """Production IC client: icp-cli + ic-py read_state."""

    def __init__(
        self,
        env: str = "local",
        identity: str | None = None,
        project_root: str | None = None,
        network_url: str | None = None,
    ) -> None:
        self.env = env
        self.identity = identity
        # Isolated replicas own their icp.yaml; product builds still use the
        # Casals checkout (passed as project_root by the CLI for wasm paths).
        isolated = replica_home()
        self.project_root = isolated or project_root or os.getcwd()
        if network_url:
            self.network_url = network_url
        elif env in ("ic", "production"):
            # Sheet env `production` is the IC. icp.yaml in this checkout has
            # no such environment, so calls use `-n ic` (see `_base_flags`).
            self.network_url = NETWORK_URLS["ic"]
        else:
            self.network_url = replica_network_url()
        self._agent = None
        # icp has no DFX_HSM_PIN: it prompts, and we capture stdout so that
        # is "not a terminal". A PIN in the env (or a file) is written to a
        # 0600 temp file and passed as --identity-password-file on every call.
        self._pin_file = _hsm_pin_file()

    def _is_mainnet(self) -> bool:
        url = (self.network_url or "").rstrip("/")
        return url in (NETWORK_URLS["ic"].rstrip("/"), "https://ic0.app") or self.env in ("ic", "production")

    def _base_flags(self, env: bool = True) -> list[str]:
        # Mainnet: `-n ic`. A sheet env named `production` is not an icp
        # environment in this repo's icp.yaml (`-e production` fails).
        if not env:
            flags: list[str] = []
        elif self._is_mainnet():
            flags = ["-n", "ic"]
        else:
            flags = ["-e", self.env]
        if self.identity:
            flags += ["--identity", self.identity]
        if self._pin_file:
            flags += ["--identity-password-file", self._pin_file]
        return flags

    def _project_root_flag(self) -> list[str]:
        extra = icp_project_args()
        if extra:
            return extra
        if os.path.isfile(os.path.join(self.project_root, "icp.yaml")):
            return ["--project-root-override", self.project_root]
        return []

    def _announce_signing(self, argv: list[str]) -> None:
        """A hardware key signs every call and may want a touch; icp's own
        prompt is swallowed with its stdout, so say what is being signed
        (`CASALS_QUIET_SIGNING=1` silences it)."""
        if not self._pin_file or os.environ.get("CASALS_QUIET_SIGNING") or argv[:2] == ["canister", "link"]:
            return
        what = " ".join(argv[:4]) if argv[:2] == ["canister", "call"] else " ".join(argv[:3])
        print(f"  signing {what} as {self.identity or 'default'}", file=sys.stderr, flush=True)

    def icp(self, argv: list[str], *, timeout: int = 300, check: bool = True, env: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = ["icp"] + argv + self._base_flags(env) + self._project_root_flag()
        attempts = TRANSIENT_ATTEMPTS if tuple(argv[:2]) in RETRYABLE_OPS else 1
        self._announce_signing(argv)
        for attempt in range(1, attempts + 1):
            result = run_icp_cmd(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0 or not check:
                return result
            if attempt < attempts and is_transient_ic_error(result.stderr + result.stdout):
                delay = min(60, 5 * 2 ** (attempt - 1))
                first = next((ln.strip() for ln in (result.stderr or result.stdout).splitlines() if ln.strip()), "")
                print(
                    f"  icp {' '.join(argv[:4])}: transient IC error ({first[:120]}); "
                    f"retry {attempt}/{attempts - 1} in {delay}s",
                    file=sys.stderr, flush=True,
                )
                time.sleep(delay)
                continue
            combined = f"{result.stdout or ''}{result.stderr or ''}"
            hint = hsm_pin_hint(combined)
            detail = icp_failure_detail(argv, result.stdout, result.stderr)
            raise RuntimeError(f"{hint}\n\n{detail}" if hint else detail)
        return result  # unreachable

    def _agent_client(self):
        if self._agent is None:
            from ic.agent import Agent
            from ic.client import Client
            from ic.identity import Identity

            self._agent = Agent(Identity(), Client(self.network_url))
        return self._agent

    def _read_state(self, what: str, canister_id: str, fn):
        """read_state with retries: a busy local replica answers late (httpx's
        5 s default → "timed out"), and a failure must never be read as an answer."""
        last: Exception | None = None
        for attempt in range(4):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"read_state {what} {canister_id} failed: {last}")

    def read_controllers(self, canister_id: str) -> list[str]:
        from ic import system_state

        agent = self._agent_client()
        principals = self._read_state("controllers", canister_id,
                                      lambda: system_state.canister_controllers(agent, canister_id))
        return [p.to_str() for p in principals]

    def read_module_hash(self, canister_id: str) -> str | None:
        from ic.certificate import lookup
        from ic.principal import Principal

        agent = self._agent_client()
        path = [b"canister", Principal.from_str(canister_id).bytes, b"module_hash"]
        found = self._read_state("module_hash", canister_id,
                                 lambda: lookup(path, agent.read_state_raw(canister_id, [path])))
        return found.hex() if found else None  # None means "no module", never "read_state failed"

    def canister_exists(self, canister_id: str) -> bool:
        """Is this id a canister on *this* network? A created-but-empty canister
        already has controllers in the state tree; an id from another replica
        (stale bindings, a replica started over) has nothing there."""
        from ic.certificate import lookup
        from ic.principal import Principal

        agent = self._agent_client()
        path = [b"canister", Principal.from_str(canister_id).bytes, b"controllers"]
        found = self._read_state("controllers", canister_id,
                                 lambda: lookup(path, agent.read_state_raw(canister_id, [path])))
        return found is not None

    def query(self, canister_id: str, method: str, text_arg: str | None = None) -> Any:
        cmd = ["canister", "call", canister_id, method, "--query"]
        if text_arg is None:
            return parse_icp_output(self.icp(cmd + ["()"], timeout=120).stdout)
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".candid", delete=False, encoding="utf-8")
        tmp.write(candid_text_arg(text_arg))
        tmp.close()
        try:
            cmd += ["--args-file", tmp.name, "--args-format", "candid"]
            return parse_icp_output(self.icp(cmd, timeout=120).stdout)
        finally:
            os.unlink(tmp.name)

    def call_update(
        self,
        canister_id: str,
        method: str,
        text_arg: str | None = None,
        *,
        timeout: int = 300,
    ) -> Any:
        cmd = ["canister", "call", canister_id, method]
        if text_arg is None:
            cmd.append("()")
            return parse_icp_output(self.icp(cmd, timeout=timeout).stdout)
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".candid", delete=False, encoding="utf-8")
        tmp.write(candid_text_arg(text_arg))
        tmp.close()
        try:
            cmd += ["--args-file", tmp.name, "--args-format", "candid"]
            return parse_icp_output(self.icp(cmd, timeout=timeout).stdout)
        finally:
            os.unlink(tmp.name)

    def call_conductor(self, backend_id: str, method: str, text_arg: str | None = None, *, timeout: int = 300) -> Any:
        return self.call_update(backend_id, method, text_arg, timeout=timeout)

    def call_candid(self, canister_id: str, method: str, arg: bytes, *, query: bool = False, timeout: int = 300) -> bytes:
        """Binary Candid in, binary Candid out — for canisters with a typed
        interface (the asset store): a 1 MiB blob goes through as bytes, not as
        a 4 MB text escape. The reply is the raw response, for the caller to
        decode with the types it knows."""
        cmd = ["canister", "call", canister_id, method, "--args-format", "bin", "--json"]
        if query:
            cmd.append("--query")
        tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".bin", delete=False)
        tmp.write(arg)
        tmp.close()
        try:
            out = self.icp(cmd + ["--args-file", tmp.name], timeout=timeout).stdout
        finally:
            os.unlink(tmp.name)
        try:
            payload = json.loads(out.strip().splitlines()[-1])
            return bytes.fromhex(payload["response_bytes"])
        except (ValueError, KeyError, IndexError) as exc:
            raise RuntimeError(f"icp canister call {method}: unexpected reply {out[-400:]!r}") from exc

    def deployer_principal(self) -> str:
        out = self.icp(["identity", "principal"], env=False).stdout.strip()
        return out.split()[-1] if out else ""

    def deployer_cycles_balance(self) -> int | None:
        """Cycles balance for the active identity (user principal, not a canister)."""
        result = self.icp(["cycles", "balance", "-q"], check=False)
        if result.returncode != 0:
            return None
        text = (result.stdout or "").strip()
        m = re.search(r"([\d_]+)", text)
        return int(m.group(1).replace("_", "")) if m else None

    def icp_project(
        self,
        project_dir: str,
        argv: list[str],
        *,
        timeout: int = 300,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        # Sidecar icp.yaml declares environment `self.env` (e.g. production)
        # pointed at this network. `-n ic` is rejected by `canister link` and
        # is the wrong flag here anyway (`-e production` is the project env).
        # `canister link` is local bookkeeping: no --identity.
        needs_identity = argv[:2] != ["canister", "link"]
        flags = ["-e", self.env]
        if needs_identity and self.identity:
            flags += ["--identity", self.identity]
        if needs_identity and self._pin_file:
            flags += ["--identity-password-file", self._pin_file]
        cmd = ["icp"] + argv + flags + ["--project-root-override", project_dir]
        result = run_icp_cmd(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"icp {' '.join(argv)} failed (project={project_dir}):\n"
                f"stdout: {result.stdout[-800:]}\nstderr: {result.stderr[-800:]}"
            )
        return result

    def canister_cycles(self, canister_id: str) -> int | None:
        try:
            out = self.icp(["canister", "status", canister_id], check=False).stdout
        except Exception:
            return None
        m = re.search(r"(?:Balance|Cycles):\s*([\d_]+)", out)
        return int(m.group(1).replace("_", "")) if m else None

    def top_up(self, canister_id: str, cycles: int) -> None:
        self.icp(["canister", "top-up", canister_id, "--amount", str(cycles)])

    def create_detached(self) -> str:
        out = self.icp(["canister", "create", "--detached"]).stdout
        m = re.search(r"ID\s+([a-z0-9-]+)", out)
        if not m:
            raise RuntimeError(f"could not parse detached canister id from: {out!r}")
        return m.group(1)

    def install_wasm(self, canister_id: str, wasm_path: str, *, mode: str = "install") -> None:
        self.icp(
            ["canister", "install", canister_id, "--wasm", wasm_path, "--mode", mode, "-y"],
            timeout=900,
        )

    def settings_update(self, canister_id: str, *, set_controllers: list[str] | None = None) -> None:
        cmd = ["canister", "settings", "update", canister_id, "-f"]
        if set_controllers is not None:
            cmd.append("--remove-all-controllers")
            for c in set_controllers:
                cmd += ["--add-controller", c]
        self.icp(cmd)

    def stop_canister(self, canister_id: str) -> None:
        self.icp(["canister", "stop", canister_id])

    def delete_canister(self, canister_id: str) -> None:
        # Default: recover liquid cycles to the caller's cycles-ledger account.
        self.icp(["canister", "delete", canister_id])


class RecordingIc:
    """Fake IC layer for tests — records calls, returns scripted responses."""

    def __init__(
        self,
        env: str = "local",
        identity: str | None = None,
        project_root: str | None = None,
        network_url: str | None = None,
    ) -> None:
        self.env = env
        self.identity = identity
        self.project_root = project_root or os.getcwd()
        self.network_url = network_url or NETWORK_URLS["local"]
        self.calls: list[tuple[str, tuple, dict]] = []
        self.controllers: dict[str, list[str]] = {}
        self.module_hashes: dict[str, str | None] = {}
        self.queries: dict[tuple[str, str], Any] = {}
        self.updates: dict[tuple[str, str], Any] = {}
        self.cycles: dict[str, int] = {}
        self.candid: dict[Any, Any] = {}
        self.deployer = "aaaaa-aa"
        self.converged = False

    def record(self, op: str, *args, **kwargs) -> None:
        self.calls.append((op, args, kwargs))

    def deployer_principal(self) -> str:
        self.record("deployer_principal")
        return self.deployer

    def deployer_cycles_balance(self) -> int | None:
        self.record("deployer_cycles_balance")
        return self.cycles.get("__deployer__")

    def icp_project(
        self,
        project_dir: str,
        argv: list[str],
        *,
        timeout: int = 300,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self.record("icp_project", project_dir, tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def read_controllers(self, canister_id: str) -> list[str]:
        self.record("read_controllers", canister_id)
        return list(self.controllers.get(canister_id, []))

    def read_module_hash(self, canister_id: str) -> str | None:
        self.record("read_module_hash", canister_id)
        return self.module_hashes.get(canister_id)

    def canister_exists(self, canister_id: str) -> bool:
        self.record("canister_exists", canister_id)
        return canister_id in self.controllers or canister_id in self.module_hashes

    def query(self, canister_id: str, method: str, text_arg: str | None = None) -> Any:
        self.record("query", canister_id, method, text_arg)
        return self.queries.get((canister_id, method), {"ok": True})

    def call_candid(self, canister_id: str, method: str, arg: bytes, *, query: bool = False, timeout: int = 300) -> bytes:
        """Typed calls go to a scripted handler: ``self.candid[(canister_id, method)]``
        or ``self.candid[method]`` is called with the raw argument bytes and
        returns the raw reply bytes (see casals_cli.wasm_store.FakeAssetStore)."""
        self.record("call_candid", canister_id, method, len(arg), query)
        handler = getattr(self, "candid", {}).get((canister_id, method)) or getattr(self, "candid", {}).get(method)
        if handler is None:
            raise RuntimeError(f"RecordingIc: no candid handler for {method} on {canister_id}")
        return handler(arg)

    def call_update(self, canister_id: str, method: str, text_arg: str | None = None, *, timeout: int = 300) -> Any:
        self.record("call_update", canister_id, method, text_arg)
        key = (canister_id, method)
        if key in self.updates:
            return self.updates[key]
        if method == "plan":
            if self.converged:
                return {"ok": True, "plan": {"hash": "h0", "items": [], "unverifiable": [], "info": []}}
            return {"ok": True, "plan": {"hash": "h1", "items": [{"seq": 0, "kind": "create_canister", "target": {"name": "x"}, "requires": "self"}], "unverifiable": [], "info": []}}
        if method == "apply":
            self.converged = True
            return {"ok": True, "plan_hash": "h1", "applied": [{"kind": "noop", "target": {"name": "x"}, "result": "ok"}], "failed": None, "skipped": [], "remaining": 0}
        if method == "set_sheet":
            return {"ok": True, "sheet_hash": "sh1", "env": self.env, "warnings": []}
        if method == "bind_conductor":
            return {"ok": True}
        if method == "get_bindings":
            return {"ok": True, "bindings": {}, "self": canister_id, "env": self.env}
        return {"ok": True}

    def icp(self, argv: list[str], *, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.record("icp", tuple(argv))
        for prefix, out in getattr(self, "icp_outputs", {}).items():  # canned raw outputs (tests)
            if tuple(argv[:len(prefix)]) == prefix:
                return subprocess.CompletedProcess(argv, 0, out, "")
        op = argv[0] if argv else ""
        if op == "canister" and len(argv) > 1:
            sub = argv[1]
            if sub == "create" and "--detached" in argv:
                cid = "new-canister-001"
                return subprocess.CompletedProcess(argv, 0, f"Created canister.\nID {cid}\n", "")
            if sub == "install":
                return subprocess.CompletedProcess(argv, 0, "Installed.\n", "")
            if sub == "settings":
                return subprocess.CompletedProcess(argv, 0, "Updated.\n", "")
            if sub == "status":
                cid = argv[2] if len(argv) > 2 else ""
                bal = self.cycles.get(cid, 5_000_000_000_000)
                return subprocess.CompletedProcess(
                    argv, 0, f"Canister Id: {cid}\nBalance: {bal} Cycles\n", ""
                )
        return subprocess.CompletedProcess(argv, 0, "", "")

    def canister_cycles(self, canister_id: str) -> int | None:
        self.record("canister_cycles", canister_id)
        return self.cycles.get(canister_id)

    def top_up(self, canister_id: str, cycles: int) -> None:
        self.record("top_up", canister_id, cycles)
        self.cycles[canister_id] = (self.cycles.get(canister_id) or 0) + cycles

    def create_detached(self) -> str:
        self.record("create_detached")
        return "new-canister-001"

    def install_wasm(self, canister_id: str, wasm_path: str, *, mode: str = "install") -> None:
        self.record("install_wasm", canister_id, wasm_path, mode=mode)

    def settings_update(self, canister_id: str, *, set_controllers: list[str] | None = None) -> None:
        self.record("settings_update", canister_id, set_controllers=set_controllers)

    def stop_canister(self, canister_id: str) -> None:
        self.record("stop_canister", canister_id)

    def delete_canister(self, canister_id: str) -> None:
        self.record("delete_canister", canister_id)

    def call_conductor(self, backend_id: str, method: str, text_arg: str | None = None, *, timeout: int = 300) -> Any:
        return self.call_update(backend_id, method, text_arg, timeout=timeout)
