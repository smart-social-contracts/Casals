"""Invite access codes — pure, standard library only.

A commander slot may be declared before anyone knows the principal that
will hold it: the sheet (``environments.<env>.principals``) or a runtime
``set_commander`` call lists ``sha256:<hex>`` — the checksum of a secret
code — in place of a principal. Whoever presents the code to
``claim_commander`` becomes that commander. The checksum is the only thing
ever stored; the plaintext code lives with the person it was handed to.
"""

from __future__ import annotations

import hashlib

CODE_CHECKSUM_PREFIX = "sha256:"
_HEX = frozenset("0123456789abcdef")


def is_code_checksum(value) -> bool:
    """True when ``value`` is written in the ``sha256:<hex>`` slot form
    (regardless of whether the hex part is well-formed)."""
    return isinstance(value, str) and value.strip().lower().startswith(CODE_CHECKSUM_PREFIX)


def normalize_code_checksum(value: str) -> str:
    """Canonical ``sha256:<64 lowercase hex>``; raises ValueError otherwise."""
    v = (value or "").strip().lower()
    if not v.startswith(CODE_CHECKSUM_PREFIX):
        raise ValueError(f"invalid access-code checksum {value!r}: expected {CODE_CHECKSUM_PREFIX}<64 hex>")
    digest = v[len(CODE_CHECKSUM_PREFIX):]
    if len(digest) != 64 or not set(digest) <= _HEX:
        raise ValueError(f"invalid access-code checksum {value!r}: expected {CODE_CHECKSUM_PREFIX}<64 hex>")
    return CODE_CHECKSUM_PREFIX + digest


def code_checksum(code: str) -> str:
    """``sha256:<hex>`` of a code exactly as typed (surrounding whitespace stripped)."""
    return CODE_CHECKSUM_PREFIX + hashlib.sha256((code or "").strip().encode("utf-8")).hexdigest()


# Digests of example access codes this repository publishes in plaintext.
# Each entry is ``code_checksum`` of one source string (whitespace stripped,
# then SHA-256 over UTF-8). Hardcoded so validation does not scan the tree.
# A local network may still use them. Any other network must not put one on
# a commanders principal or a principals alias: the code is already public.
#
# Source strings:
#   casals.json — CASALS
#   casals_cli/examples/minimal.json and tests/e2e/orchestras/*/casals.json —
#     casals, casals-auditor, casals-dev, casals-lifecycle, casals-motoko,
#     casals-operator, casals-owner, casals-platform, casals-python,
#     casals-realms, casals-release, casals-rust, casals-sre, casals-steward
#   tests/e2e/run_e2e.py, tests/test_access_code.py,
#   tests/e2e/orchestras/governed/casals.json — CASALS-E2E-ACCESS-CODE
PUBLISHED_ACCESS_CODE_CHECKSUMS = frozenset({
    "sha256:0ee72a3fff6256024f93326689d0ac6e371020c41cfa925c30e4dfa63b4d9980",
    "sha256:de5b0cf9529d693d3f371967298ca3841b1b0cdf7a41d8102e45c8f3e5dce688",
    "sha256:ccadbf8d475e57765abdd4150b80abaa1cf0467e2c79b2f7f4adebc53d9b0e31",
    "sha256:fdfb3d345c4403de4f4b536375d4312538a9030850e19098ad6cf4c8a65c2707",
    "sha256:399f110d3d5f533181d50a7c0dcf0e8518235123cbe9fd45e075c9e6cbd3f033",
    "sha256:84675f7c094aff5946321fe9c5ec1764229091cfdbbd12f59fc64fb275d99fbd",
    "sha256:ab18cfe86953fc7ee0f9c5197c081e764465a09d8b0361ffc80b708abb1058a6",
    "sha256:e96e0fcf99e791d5293d86f3032c11de0bb8fc3f85c36db495d7a059caff8809",
    "sha256:2338c757727ad87f8527f871f9dd28af850bf97fb4f496ab94f45913496cd126",
    "sha256:d36cff64938b358015571bc697b8eb14d311d3be486c3ef7ca57244e795151a6",
    "sha256:1fff220f03f8e6ae51e75fc583fb2948a9447fe2b42b0984e89fe835a5f6a884",
    "sha256:d6606c861e0311482adeac5d701f162d3d7365d612eada47ca9007ebc9d2f27b",
    "sha256:de2e8fcf679a855ba862c7264278e2c118c5839da20e09f44ce3766acb3f6323",
    "sha256:aaf1b5ae00d2d256e6cf53f77f6807ba608c99defed0a05a7dffb846c5a63947",
    "sha256:837a16075a6d801bd8c1065191b64086e2ca7cc6f06049cdde9b120ae7b1b818",
    "sha256:65fa65e8d3e12282657a11e515874c26e11858802bd9512e2d7c5eef88c5f409",
})


def is_published_code(value) -> bool:
    """True for the checksum of an access code this repository publishes."""
    if not is_code_checksum(value):
        return False
    try:
        return normalize_code_checksum(value) in PUBLISHED_ACCESS_CODE_CHECKSUMS
    except ValueError:
        return False


def checksums_equal(a: str, b: str) -> bool:
    """Constant-time comparison of two checksum strings."""
    a_b = (a or "").encode("utf-8")
    b_b = (b or "").encode("utf-8")
    if len(a_b) != len(b_b):
        return False
    acc = 0
    for x, y in zip(a_b, b_b):
        acc |= x ^ y
    return acc == 0
