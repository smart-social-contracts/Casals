"""AssetPermission Candid hygiene + grant_stand_backend_commit contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DID = ROOT / "casals_backend.did"
BATON_DID = ROOT / "packages" / "orchestration" / "baton" / "baton.did"
FIX = ROOT / "scripts" / "fix_asset_permission_did.py"

BROKEN = "variant { Commit : ; Prepare : ; ManagePermissions :  }"
FIXED = "variant { Commit; Prepare; ManagePermissions }"


def test_casals_did_has_unit_asset_permission_variants():
    text = DID.read_text(encoding="utf-8")
    assert BROKEN not in text
    assert FIXED in text
    assert '"grant_stand_backend_commit" : (text) -> (text);' in text


def test_baton_did_has_unit_asset_permission_variants():
    text = BATON_DID.read_text(encoding="utf-8")
    assert BROKEN not in text
    assert FIXED in text


def test_fix_script_rewrites_broken_variant(tmp_path: Path):
    sample = tmp_path / "t.did"
    sample.write_text(
        "type AssetPermission = variant { Commit : ; Prepare : ; ManagePermissions :  };\n",
        encoding="utf-8",
    )
    subprocess.check_call([sys.executable, str(FIX), str(sample)])
    out = sample.read_text(encoding="utf-8")
    assert BROKEN not in out
    assert FIXED in out
