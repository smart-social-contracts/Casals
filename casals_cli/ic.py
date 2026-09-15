"""IC access layer: icp-cli subprocess + anonymous read_state via ic-py."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Any, Protocol

from casals_cli.util import candid_text_arg, parse_icp_output

NETWORK_URLS = {
    "local": "http://127.0.0.1:8000",
    "ic": "https://icp0.io",
}


class IcAccess(Protocol):
    env: str
    identity: str | None
    project_root: str
    network_url: str

    def deployer_principal(self) -> str: ...
    def read_controllers(self, canister_id: str) -> list[str]: ...
    def read_module_hash(self, canister_id: str) -> str | None: ...
    def query(self, canister_id: str, method: str, text_arg: str | None = None) -> Any: ...
    def call_update(self, canister_id: str, method: str, text_arg: str | None = None, *, timeout: int = 300) -> Any: ...
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
        self.project_root = project_root or os.getcwd()
        self.network_url = network_url or NETWORK_URLS.get(env, NETWORK_URLS["local"])
        self._agent = None

    def _base_flags(self, env: bool = True) -> list[str]:
        flags = ["-e", self.env] if env else []
        if self.identity:
            flags += ["--identity", self.identity]
        return flags

    def _project_root_flag(self) -> list[str]:
        if os.path.isfile(os.path.join(self.project_root, "icp.yaml")):
            return ["--project-root-override", self.project_root]
        return []

    def icp(self, argv: list[str], *, timeout: int = 300, check: bool = True, env: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = ["icp"] + argv + self._base_flags(env) + self._project_root_flag()
        result = subprocess.run(
            cmd,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"icp {' '.join(argv)} failed:\n"
                f"stdout: {result.stdout[-800:]}\nstderr: {result.stderr[-800:]}"
            )
        return result

    def _agent_client(self):
        if self._agent is None:
            from ic.agent import Agent
            from ic.client import Client
            from ic.identity import Identity

            self._agent = Agent(Identity(), Client(self.network_url))
        return self._agent

    def read_controllers(self, canister_id: str) -> list[str]:
        from ic import system_state

        agent = self._agent_client()
        principals = system_state.canister_controllers(agent, canister_id)
        return [p.to_str() for p in principals]

    def read_module_hash(self, canister_id: str) -> str | None:
        from ic import system_state

        agent = self._agent_client()
        try:
            return system_state.canister_module_hash(agent, canister_id)
        except Exception:
            return None

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
        # `icp canister link` is local project bookkeeping: no --identity flag.
        needs_identity = argv[:2] != ["canister", "link"]
        cmd = ["icp"] + argv + self._base_flags() + ["--project-root-override", project_dir]
        if not needs_identity and self.identity:
            cmd = [f for f in cmd if f not in ("--identity", self.identity)]
        result = subprocess.run(
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
        self.icp(["canister", "stop", canister_id, "-y"])

    def delete_canister(self, canister_id: str) -> None:
        self.icp(["canister", "delete", canister_id, "-y"])


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

    def query(self, canister_id: str, method: str, text_arg: str | None = None) -> Any:
        self.record("query", canister_id, method, text_arg)
        return self.queries.get((canister_id, method), {"ok": True})

    def call_update(self, canister_id: str, method: str, text_arg: str | None = None, *, timeout: int = 300) -> Any:
        self.record("call_update", canister_id, method, text_arg)
        key = (canister_id, method)
        if key in self.updates:
            return self.updates[key]
        if method == "plan":
            if self.converged:
                return {"ok": True, "plan": {"hash": "h0", "items": [], "unverifiable": [], "drift": [], "unmanaged": [], "info": []}}
            return {"ok": True, "plan": {"hash": "h1", "items": [{"seq": 0, "kind": "create_canister", "target": {"name": "x"}, "requires": "self"}], "unverifiable": [], "drift": [], "unmanaged": [], "info": []}}
        if method == "apply":
            self.converged = True
            return {"ok": True, "plan_hash": "h1", "applied": [{"kind": "noop", "target": {"name": "x"}, "result": "ok"}], "failed": None, "skipped": [], "remaining": 0}
        if method == "verify":
            return {"ok": True, "converged": self.converged, "plan": {"hash": "h0", "items": []}}
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

    def call_conductor(self, backend_id: str, method: str, text_arg: str | None = None, *, timeout: int = 300) -> Any:
        return self.call_update(backend_id, method, text_arg, timeout=timeout)
