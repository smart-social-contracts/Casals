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
