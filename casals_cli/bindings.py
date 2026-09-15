"""Runtime bindings: sheet name → canister id per environment."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sheetv2 import CONDUCTOR_NAMES



def bindings_dir() -> str:
    """`$CASALS_HOME` (tests, CI) or `~/.casals`."""
    return os.environ.get("CASALS_HOME") or os.path.expanduser("~/.casals")


@dataclass
class Bindings:
    sheet_name: str
    env: str
    network_url: str
    deployer: str
    conductor: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    backend_id: str = ""
    icp_project_dir: str = ""
    conductor_module_hashes: dict[str, str] = field(default_factory=dict)
    asset_dist_hashes: dict[str, str] = field(default_factory=dict)

    @property
    def casals_backend_id(self) -> str:
        return self.backend_id or self.conductor.get(CONDUCTOR_NAMES["backend"], "")

    def path(self) -> str:
        safe = self.sheet_name.replace("/", "_")
        return os.path.join(bindings_dir(), f"{safe}.{self.env}.json")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet_name": self.sheet_name,
            "env": self.env,
            "network_url": self.network_url,
            "deployer": self.deployer,
            "conductor": dict(self.conductor),
            "backend_id": self.backend_id or self.casals_backend_id,
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(),
            "icp_project_dir": self.icp_project_dir,
            "conductor_module_hashes": dict(self.conductor_module_hashes),
            "asset_dist_hashes": dict(self.asset_dist_hashes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Bindings":
        conductor = data.get("conductor") or {}
        backend = (data.get("backend_id") or conductor.get(CONDUCTOR_NAMES["backend"]) or "").strip()
        return cls(
            sheet_name=str(data.get("sheet_name") or ""),
            env=str(data.get("env") or ""),
            network_url=str(data.get("network_url") or ""),
            deployer=str(data.get("deployer") or ""),
            conductor={k: str(v) for k, v in conductor.items()},
            created_at=str(data.get("created_at") or ""),
            backend_id=backend,
            icp_project_dir=str(data.get("icp_project_dir") or ""),
            conductor_module_hashes={
                k: str(v) for k, v in (data.get("conductor_module_hashes") or {}).items()
            },
            asset_dist_hashes={
                k: str(v) for k, v in (data.get("asset_dist_hashes") or {}).items()
            },
        )

    def save(self) -> None:
        os.makedirs(bindings_dir(), exist_ok=True)
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        with open(self.path(), "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")

    def remove(self) -> None:
        path = self.path()
        if os.path.exists(path):
            os.remove(path)


def load_bindings(sheet_name: str, env: str) -> Bindings | None:
    b = Bindings(sheet_name=sheet_name, env=env, network_url="", deployer="")
    path = b.path()
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return Bindings.from_dict(json.load(f))


def resolve_backend_id(bindings: Bindings | None, conductor_override: str | None) -> str:
    if conductor_override:
        return conductor_override.strip()
    if bindings and bindings.casals_backend_id:
        return bindings.casals_backend_id
    return ""


def live_bindings(ic, sheet_name: str, env: str, conductor_override: str | None = None) -> tuple[str, dict[str, str]]:
    """(backend id, name → canister id) for an orchestra: local bindings file + the
    conductor's `get_bindings` (stand canisters are only known to the conductor)."""
    local = load_bindings(sheet_name, env)
    backend = resolve_backend_id(local, conductor_override)
    if not backend:
        raise RuntimeError("no conductor; run casals up or pass --conductor")
    ids = dict(local.conductor) if local else {}
    res = ic.query(backend, "get_bindings")
    if isinstance(res, dict) and res.get("bindings"):
        ids.update(res["bindings"])
    ids[CONDUCTOR_NAMES["backend"]] = backend
    return backend, ids


def find_bindings_for_env(env: str, conductor: str | None = None) -> Bindings | None:
    """Locate a bindings file for ``env``, optionally matching ``conductor``."""
    if not os.path.isdir(bindings_dir()):
        return None
    matches: list[Bindings] = []
    suffix = f".{env}.json"
    for fname in os.listdir(bindings_dir()):
        if not fname.endswith(suffix):
            continue
        path = os.path.join(bindings_dir(), fname)
        try:
            with open(path, encoding="utf-8") as f:
                b = Bindings.from_dict(json.load(f))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if conductor:
            cid = conductor.strip()
            if b.backend_id != cid and b.casals_backend_id != cid:
                continue
        matches.append(b)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if conductor:
        return matches[0]
    raise RuntimeError(
        f"multiple bindings for env {env!r}; pass --conductor or run from a named sheet"
    )


def live_stands(tree: dict) -> dict[str, str]:
    """stand name → section name from the conductor's `get_tree`."""
    return {st.get("name", ""): sec.get("name", "")
            for sec in (tree.get("sections") or []) if isinstance(sec, dict)
            for st in sec.get("stands") or [] if isinstance(st, dict)}
