import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wasm_types import (
    has_basilisk_features,
    infer_wasm_type,
    wasm_type_tags,
)


def test_infer_multisig():
    assert infer_wasm_type("orchestration-multisig@1.0.0") == "multisig"


def test_infer_baton():
    assert infer_wasm_type("orchestration-baton@1.0.0") == "baton"


def test_multisig_tags_include_motoko_not_basilisk():
    assert wasm_type_tags("multisig") == ["Multisig", "Motoko"]
    assert not has_basilisk_features("multisig")


def test_baton_has_basilisk_features():
    assert has_basilisk_features("baton")
    assert "Basilisk" in wasm_type_tags("baton")
