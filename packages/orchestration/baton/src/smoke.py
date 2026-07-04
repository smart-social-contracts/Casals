"""Optional post-upgrade smoke test (user-defined method + response checks)."""

from basilisk import Async, Principal, ic


def validate_smoke_test(smoke) -> None:
    if smoke is None:
        return
    if not isinstance(smoke, dict):
        raise ValueError("smoke_test must be an object")
    if not (smoke.get("method") or "").strip():
        raise ValueError("smoke_test.method is required when smoke_test is set")


def check_smoke_response(
    body: str,
    must_contain: str = "",
    must_not_contain: str = "",
) -> tuple[bool, str]:
    text = "" if body is None else str(body)
    need = (must_contain or "").strip()
    forbid = (must_not_contain or "").strip()
    if need and need not in text:
        return False, f"response missing {need!r}: {text[:200]!r}"
    if forbid and forbid in text:
        return False, f"response contains forbidden {forbid!r}: {text[:200]!r}"
    return True, ""


def _candid_escape_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def smoke_call_arg_candid(arg: str) -> bytes:
    arg = (arg or "").strip()
    if not arg:
        return ic.candid_encode("()")
    return ic.candid_encode(f'("{_candid_escape_text(arg)}")')


def _unwrap_call_raw(res):
    if isinstance(res, dict):
        if "Err" in res:
            raise RuntimeError(str(res["Err"]))
        if "Ok" in res:
            return res["Ok"]
    if hasattr(res, "Err") and res.Err is not None:
        raise RuntimeError(str(res.Err))
    if hasattr(res, "Ok"):
        return res.Ok
    return res


def _decode_text_reply(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        decoded = ic.candid_decode(bytes(raw))
        return decoded if isinstance(decoded, str) else str(decoded)
    return str(raw)


def run_smoke_test_gen(canister_id: str, smoke: dict) -> Async[str]:
    """Call ``smoke_test.method`` on the target (update call) and check the reply."""
    validate_smoke_test(smoke)
    method = (smoke.get("method") or "").strip()
    arg = smoke.get("arg")
    if arg is None:
        arg = ""
    args_raw = smoke_call_arg_candid(str(arg))
    res = yield ic.call_raw(Principal.from_str(canister_id), method, args_raw, 0)
    body = _decode_text_reply(_unwrap_call_raw(res))
    ok, err = check_smoke_response(
        body,
        smoke.get("must_contain") or "",
        smoke.get("must_not_contain") or "",
    )
    if not ok:
        raise ValueError(err)
    return body
