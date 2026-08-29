"""GET /version contract tests (gos-as-a-service#39).

The Casals conductor serves build provenance at /version over the IC
HTTP interface (http_request + http_request_update). Values are stamped
at build/release time; unstamped placeholders (local/dev builds) are
omitted honestly — never invented at query time.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import version_http  # noqa: E402

_REQ = {"method": "GET", "url": "/version", "headers": [], "body": b""}


def _headers_dict(resp):
    return {k: v for k, v in resp["headers"]}


def _assert_contract(resp, canister_name):
    assert resp["status_code"] == 200
    headers = _headers_dict(resp)
    assert headers["Content-Type"] == "application/json"
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert headers["Cache-Control"] == "no-cache, must-revalidate"
    payload = json.loads(resp["body"].decode("utf-8"))
    assert payload["canister"] == canister_name
    return payload


def _assert_unstamped_omits(payload):
    # Repo checkout is unstamped: placeholder-backed fields are omitted.
    assert "sha" not in payload
    assert "built_at" not in payload
    assert "version" not in payload


def test_version_ok_unstamped():
    resp = version_http.version_http_response(dict(_REQ))
    payload = _assert_contract(resp, "casals_backend")
    _assert_unstamped_omits(payload)


def test_version_strips_query_string():
    resp = version_http.version_http_response({**_REQ, "url": "/version?foo=bar"})
    assert resp["status_code"] == 200
    payload = json.loads(resp["body"].decode("utf-8"))
    assert payload["canister"] == "casals_backend"


def test_version_strips_fragment():
    resp = version_http.version_http_response({**_REQ, "url": "/version#frag"})
    assert resp["status_code"] == 200


def test_version_404_other_paths():
    resp = version_http.version_http_response({**_REQ, "url": "/status"})
    assert resp["status_code"] == 404
    headers = _headers_dict(resp)
    assert headers["Content-Type"] == "application/json"
    assert headers["Access-Control-Allow-Origin"] == "*"
    body = json.loads(resp["body"].decode("utf-8"))
    assert body["error"] == "not found"


def test_version_404_root():
    resp = version_http.version_http_response({**_REQ, "url": "/"})
    assert resp["status_code"] == 404


def test_version_options_preflight():
    resp = version_http.version_http_response({**_REQ, "method": "OPTIONS"})
    assert resp["status_code"] == 204
    assert _headers_dict(resp)["Access-Control-Allow-Origin"] == "*"


def test_version_options_any_path():
    resp = version_http.version_http_response({**_REQ, "method": "OPTIONS", "url": "/"})
    assert resp["status_code"] == 204


def test_version_stamped_fields(monkeypatch):
    monkeypatch.setattr(version_http, "_SHA_STAMP", "a1b2c3d")
    monkeypatch.setattr(version_http, "_BUILT_AT_STAMP", "2026-08-29T13:04:05Z")
    monkeypatch.setattr(version_http, "_VERSION_STAMP", "v0.4.0")
    payload = version_http.get_version_payload()
    assert payload == {
        "canister": "casals_backend",
        "sha": "a1b2c3d",
        "built_at": "2026-08-29T13:04:05Z",
        "version": "v0.4.0",
    }


def test_version_stamped_omits_unstamped_version(monkeypatch):
    monkeypatch.setattr(version_http, "_SHA_STAMP", "a1b2c3d")
    monkeypatch.setattr(version_http, "_BUILT_AT_STAMP", "2026-08-29T13:04:05Z")
    payload = version_http.get_version_payload()
    assert payload["sha"] == "a1b2c3d"
    assert payload["built_at"] == "2026-08-29T13:04:05Z"
    assert "version" not in payload  # VERSION_STAMP left unstamped


def test_version_accepts_record_like_request():
    req = type("Req", (), {"method": "GET", "url": "/version"})()
    resp = version_http.version_http_response(req)
    assert resp["status_code"] == 200


def test_version_never_invents_at_query_time():
    # Placeholders must stay in source; query-time code must not call git/date.
    assert version_http._SHA_STAMP == "COMMIT_HASH_PLACEHOLDER"
    assert version_http._BUILT_AT_STAMP == "BUILT_AT_ISO_PLACEHOLDER"
    assert version_http._VERSION_STAMP == "VERSION_PLACEHOLDER"
    assert "sha" not in version_http.get_version_payload()
