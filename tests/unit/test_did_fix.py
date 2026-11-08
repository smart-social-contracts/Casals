import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "fix_asset_permission_did.py"
VALID = 'type Header = record { 0 : text; 1 : text };\nservice : {\n  "get" : (text) -> (text) query;\n}\n'


def fixed(tmp_path, text):
    path = tmp_path / "x.did"
    path.write_text(text)
    subprocess.run([sys.executable, str(SCRIPT), str(path)], check=True, capture_output=True)
    return path.read_text()


def test_a_unit_variant_loses_its_missing_type(tmp_path):
    assert fixed(tmp_path, "type Permission = variant { Commit :  };\n") == "type Permission = variant { Commit };\n"


def test_every_case_of_a_variant_of_unit_cases_is_fixed(tmp_path):
    broken = "type P = variant { Commit : ; Prepare : ; ManagePermissions :  };\n"
    assert fixed(tmp_path, broken) == "type P = variant { Commit; Prepare; ManagePermissions };\n"


def test_valid_candid_is_left_alone(tmp_path):
    assert fixed(tmp_path, VALID) == VALID
    assert fixed(tmp_path, "type P = variant { Commit; Prepare };\n") == "type P = variant { Commit; Prepare };\n"
