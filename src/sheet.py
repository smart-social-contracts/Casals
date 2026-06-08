"""Live orchestra sheet management.

The sheet is a JSON document describing the desired orchestra (sections,
stands, and their WASM assignments).  It is EPHEMERAL: held only in the
Wasm heap, loaded from stable storage at canister start, and reset on every
restart/upgrade.  Editing it (``set_sheet``) changes nothing on-chain until
``deploy_sheet`` is called; deploy reconciles real canisters to the sheet.
"""

import json

from default_sheet import DEFAULT_SHEET
from helpers import _settings
from ic_python_logging import get_logger

_log = get_logger("casals")

# In-process desired-state cache.  None until _load_sheet() has run.
_live_sheet = None


def get_live_sheet():
    """Return the current in-memory sheet (may be None before bootstrap)."""
    return _live_sheet


def _default_sheet_copy() -> dict:
    """A fresh deep copy of the bundled default sheet."""
    try:
        return json.loads(json.dumps(DEFAULT_SHEET))
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"could not load default sheet: {e}")
        return {"sections": []}


def _persist_sheet(sheet) -> None:
    """Write the live sheet to stable storage (the persistent source of truth)."""
    _settings().sheet_json = json.dumps(sheet)


def _load_sheet() -> None:
    """Load the live sheet from stable storage at canister start. The bundled
    default is used only to seed the very first boot (when nothing is persisted
    yet); after that, edits survive restarts and upgrades."""
    global _live_sheet
    raw = (_settings().sheet_json or "").strip()
    if raw:
        try:
            _live_sheet = json.loads(raw)
            return
        except Exception as e:  # pragma: no cover - defensive
            _log.error(f"could not parse persisted sheet, reseeding default: {e}")
    _live_sheet = _default_sheet_copy()
    try:
        _persist_sheet(_live_sheet)
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"could not persist default sheet: {e}")


def _set_live_sheet(sheet) -> dict:
    """Validate, replace, and persist the live sheet (the desired orchestra)."""
    global _live_sheet
    if isinstance(sheet, str):
        sheet = json.loads(sheet)
    if not isinstance(sheet, dict):
        raise Exception("sheet must be a JSON object")
    if not isinstance(sheet.get("sections", []), list):
        raise Exception("sheet.sections must be a list")
    _live_sheet = sheet
    _persist_sheet(_live_sheet)
    return _live_sheet
