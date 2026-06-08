"""WASM key / version helpers — pure, no IC-runtime dependencies.

These are the key-parsing and version-sorting primitives used by the
lifecycle layer (resolve_authorized_wasm, _versions_in_family, …).
Keeping them pure allows unit-testing without Basilisk or a replica.
"""


def _split_key(key: str):
    """Split an authorized-wasm key into (family, version).

    "foo@1.2.0" -> ("foo", "1.2.0"); a bare "foo" -> ("foo", "").
    """
    key = (key or "").strip()
    if "@" in key:
        fam, _, ver = key.partition("@")
        return fam.strip(), ver.strip()
    return key, ""


def _ver_tuple(version: str):
    """Comparable tuple for a version string ("1.2.0" -> (1, 2, 0)).

    Non-numeric components and the empty (unversioned) string sort lowest.
    """
    out = []
    for part in (version or "0").replace("-", ".").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out)


def _family_of(w) -> str:
    """Canonical family name for an AuthorizedWasm-like object.

    Prefers the explicit `.family` attribute; falls back to the family
    component of `.key` (the part before '@').
    """
    return (w.family or "").strip() or _split_key(w.key)[0]
