"""GET /version — build provenance over the IC HTTP interface.

Contract: every platform canister serves build provenance at /version,
so an operator can see what each environment runs. Values are stamped at build/release time (release.yml sed on the
placeholders below). A field still holding its placeholder (local/dev
builds) is omitted honestly — never invented at query time.

This module is basilisk-free so unit tests can import it without the
IC runtime. ``http_request_update`` serves ``version_http_response``.
``http_request`` upgrades only GET /version and otherwise returns that
same query response (404 or OPTIONS) with ``upgrade`` left null.
"""

import json

_VERSION_STAMP = "VERSION_PLACEHOLDER"
_SHA_STAMP = "COMMIT_HASH_PLACEHOLDER"
_BUILT_AT_STAMP = "BUILT_AT_ISO_PLACEHOLDER"

# Static canister name. This WASM is the Casals conductor only
# (``casals_backend``). marketplace_backend and other Basilisk canisters are
# separate codebases with their own version endpoints.
_CANISTER_NAME = "casals_backend"


def get_version_payload() -> dict:
    payload = {"canister": _CANISTER_NAME}
    if "PLACEHOLDER" not in _SHA_STAMP:
        payload["sha"] = _SHA_STAMP
    if "PLACEHOLDER" not in _BUILT_AT_STAMP:
        payload["built_at"] = _BUILT_AT_STAMP
    if "PLACEHOLDER" not in _VERSION_STAMP:
        payload["version"] = _VERSION_STAMP
    return payload


def _http_response(status: int, body: bytes, content_type: str, extra_headers=None) -> dict:
    headers = [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    return {
        "status_code": status,
        "headers": headers,
        "body": body,
        "streaming_strategy": None,
        "upgrade": None,
    }


def _req_field(req, key, default=""):
    """Read a field from a dict or a Basilisk Record (both appear at runtime)."""
    if isinstance(req, dict):
        return req.get(key, default)
    return getattr(req, key, default)


def version_http_response(req) -> dict:
    """Route an IC http_request(_update) call; only /version is served."""
    method = (_req_field(req, "method") or "GET").upper()
    path = (_req_field(req, "url") or "").split("?")[0].split("#")[0].strip()
    if method == "OPTIONS":
        return _http_response(204, b"", "text/plain")
    if path == "/version":
        body = json.dumps(get_version_payload()).encode("utf-8")
        return _http_response(
            200, body, "application/json",
            [("Cache-Control", "no-cache, must-revalidate")],
        )
    body = json.dumps({"error": "not found", "path": path or "/"}).encode("utf-8")
    return _http_response(404, body, "application/json")


def _is_get_version(req) -> bool:
    method = (_req_field(req, "method") or "GET").upper()
    path = (_req_field(req, "url") or "").split("?")[0].split("#")[0].strip()
    return method == "GET" and path == "/version"


def http_request_query(req) -> dict:
    """Query ``http_request`` body.

    GET /version asks the HTTP gateway to upgrade into ``http_request_update``.
    Every other method or path is ``version_http_response`` (404 or OPTIONS)
    with ``upgrade`` null, so the query is not turned into a paid update.
    """
    if _is_get_version(req):
        return {
            "status_code": 200,
            "headers": [],
            "body": b"",
            "streaming_strategy": None,
            "upgrade": True,
        }
    resp = version_http_response(req)
    if resp.get("upgrade"):
        resp = dict(resp)
        resp["upgrade"] = None
    return resp
