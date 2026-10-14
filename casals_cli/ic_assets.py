"""`.ic-assets.json5` — the asset-canister policy file dfx applies when it
deploys a frontend (headers, CSP, caching, raw access, aliasing). Casals
syncs frontends itself, so it honours the same file the same way: every
rule whose `match` glob fits an asset path contributes, later rules win per
key. Shared by the conductor (applies properties after `store`) and the CLI
oracle (checks the served headers). Pure Python, no imports beyond stdlib.

Supported keys per rule: `match`, `headers`, `security_policy`
(`standard` | `hardened` | `disabled`), `cache: {max_age}`,
`allow_raw_access`, `enable_aliasing`. `ignore` is not applied: Casals
publishes the whole dist. Only the dist root's file is read.
"""
from __future__ import annotations

import json

POLICY_FILE = "/.ic-assets.json5"

# dfx's "standard" security policy, verbatim.
STANDARD_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self';script-src 'self';connect-src 'self' http://localhost:* "
        "https://icp0.io https://*.icp0.io https://icp-api.io;img-src 'self' data:;"
        "style-src * 'unsafe-inline';style-src-elem * 'unsafe-inline';font-src *;"
        "object-src 'none';base-uri 'self';frame-ancestors 'none';form-action 'self';"
        "upgrade-insecure-requests;"
    ),
    "Permissions-Policy": (
        "accelerometer=(), ambient-light-sensor=(), autoplay=(), battery=(), camera=(), "
        "clipboard-read=(), clipboard-write=(self), conversion-measurement=(), cross-origin-isolated=(), "
        "display-capture=(), document-domain=(), encrypted-media=(), execution-while-not-rendered=(), "
        "execution-while-out-of-viewport=(), focus-without-user-activation=(), fullscreen=(), "
        "gamepad=(), geolocation=(), gyroscope=(), hid=(), idle-detection=(), interest-cohort=(), "
        "keyboard-map=(), magnetometer=(), microphone=(), midi=(), navigation-override=(), payment=(), "
        "picture-in-picture=(), publickey-credentials-get=(), screen-wake-lock=(), serial=(), "
        "speaker-selection=(), sync-script=(), sync-xhr=(self), trust-token-redemption=(), "
        "usb=(), vertical-scroll=(), web-share=(), window-placement=(), xr-spatial-tracking=()"
    ),
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
}


_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$-")


def parse_json5(text: str):
    """The JSON5 subset dfx files use: `//` and `/* */` comments, trailing
    commas, unquoted keys, single-quoted strings. (Hand-rolled: the canister's
    `re` is stubbed.)"""
    out: list[str] = []
    i, n = 0, len(text)

    def last_significant() -> str:
        for j in range(len(out) - 1, -1, -1):
            if out[j].strip():
                return out[j]
        return ""

    while i < n:
        ch = text[i]
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            body = text[i + 1:j]
            if ch == "'":
                body = body.replace("\\'", "'").replace('"', '\\"')
            out.append('"' + body + '"')
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
        elif ch in "}]":
            if last_significant() == ",":
                out[max(j for j in range(len(out)) if out[j] == ",")] = ""
            out.append(ch)
            i += 1
        elif ch in _ID_CHARS and last_significant() in ("{", ",") and not ch.isdigit() and ch != "-":
            j = i
            while j < n and text[j] in _ID_CHARS:
                j += 1
            word = text[i:j]
            k = j
            while k < n and text[k].isspace():
                k += 1
            out.append('"' + word + '"' if k < n and text[k] == ":" else word)
            i = j
        else:
            out.append(ch)
            i += 1
    return json.loads("".join(out))


def rules_from(text: str) -> list[dict]:
    doc = parse_json5(text)
    return [r for r in doc if isinstance(r, dict)] if isinstance(doc, list) else []


def _expand_braces(glob: str) -> list[str]:
    i = glob.find("{")
    j = glob.find("}", i) if i >= 0 else -1
    if i < 0 or j < 0:
        return [glob]
    return [g for alt in glob[i + 1:j].split(",") for g in _expand_braces(glob[:i] + alt + glob[j + 1:])]


def _in_class(cls: str, ch: str) -> bool:
    negate = cls.startswith("!") or cls.startswith("^")
    body = cls[1:] if negate else cls
    hit, k = False, 0
    while k < len(body):
        if k + 2 < len(body) and body[k + 1] == "-":
            hit = hit or body[k] <= ch <= body[k + 2]
            k += 3
        else:
            hit = hit or body[k] == ch
            k += 1
    return hit != negate


def _match(g: str, i: int, p: str, j: int) -> bool:
    while i < len(g):
        c = g[i]
        if g.startswith("**", i):
            k = i + 3 if g.startswith("**/", i) else i + 2
            return any(_match(g, k, p, jj) for jj in range(j, len(p) + 1))
        if c == "*":
            jj = j
            while True:
                if _match(g, i + 1, p, jj):
                    return True
                if jj >= len(p) or p[jj] == "/":
                    return False
                jj += 1
        if c == "?":
            if j < len(p) and p[j] != "/":
                i, j = i + 1, j + 1
                continue
            return False
        if c == "[" and g.find("]", i) > i:
            end = g.find("]", i)
            if j < len(p) and _in_class(g[i + 1:end], p[j]):
                i, j = end + 1, j + 1
                continue
            return False
        if j < len(p) and p[j] == c:
            i, j = i + 1, j + 1
            continue
        return False
    return j == len(p)


def glob_match(glob: str, path: str) -> bool:
    """globset semantics as dfx uses them: `**` crosses `/`, `*`/`?` do not,
    `{a,b}` alternation, `[...]` classes. Matched against the path relative
    to the dist root, no leading slash. (No `re`: the canister has none.)"""
    path = path.lstrip("/")
    return any(_match(g, 0, path, 0) for g in _expand_braces(glob))


def properties_for(key: str, rules: list[dict]) -> dict:
    """Asset properties for `key` (`/index.html`): `{"headers": {…} | None,
    "max_age": int | None, "allow_raw_access": bool | None,
    "enable_aliasing": bool | None}` — None where no rule speaks."""
    headers: dict[str, str] = {}
    spoke = False
    props: dict = {"max_age": None, "allow_raw_access": None, "enable_aliasing": None}
    for rule in rules:
        if not glob_match(str(rule.get("match") or ""), key):
            continue
        policy = rule.get("security_policy")
        if policy in ("standard", "hardened"):
            headers.update(STANDARD_HEADERS)
            spoke = True
        if isinstance(rule.get("headers"), dict):
            headers.update({str(k): str(v) for k, v in rule["headers"].items()})
            spoke = True
        cache = rule.get("cache")
        if isinstance(cache, dict) and isinstance(cache.get("max_age"), int):
            props["max_age"] = cache["max_age"]
        for src, dst in (("allow_raw_access", "allow_raw_access"), ("enable_aliasing", "enable_aliasing")):
            if isinstance(rule.get(src), bool):
                props[dst] = rule[src]
    props["headers"] = headers if spoke else None
    return props


def any_properties(props: dict) -> bool:
    return props.get("headers") is not None or any(
        props.get(k) is not None for k in ("max_age", "allow_raw_access", "enable_aliasing"))
