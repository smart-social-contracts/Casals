import chain
import store
from auth import find
from model import Wasm, orchestra, page_of
from op import op

UPLOAD_SECS = 3600
uploads = {}

FIELDS = ("path", "sha256", "kind", "type", "memory_keep")


@op("reader", read=True)
def wasm_list(principal, args):
    """One page of wasm rows (``wasms_from``, ``count``); ``next`` continues it."""
    got, nxt = page_of(Wasm, args, "wasms")
    return {"wasms": [{"key": w.key, **{k: getattr(w, k) for k in FIELDS}} for w in got], "next": {"wasms": nxt}}


@op("wasm.authorize")
def wasm_authorize(principal, args):
    existed = Wasm[args["key"]] is not None
    w = Wasm[args["key"]] or Wasm(key=args["key"])
    for key in FIELDS:
        if key in args:
            setattr(w, key, args[key])
    return {"key": w.key, "updated": existed}


@op("wasm.authorize")
def wasm_revoke(principal, args):
    find(Wasm, args["key"]).delete()


def expire(now):
    for who in [p for p, until in uploads.items() if until <= now]:
        del uploads[who]
        yield from store.permit(orchestra().store_id, who, False)


@op("wasm.upload")
def wasm_upload_begin(principal, args):
    now = chain.now()
    yield from expire(now)
    yield from store.permit(orchestra().store_id, principal, True)
    uploads[principal] = now + UPLOAD_SECS
    return {"store_id": orchestra().store_id, "expires_at": uploads[principal]}


@op("wasm.upload")
def wasm_upload_end(principal, args):
    uploads.pop(principal, None)
    yield from store.permit(orchestra().store_id, principal, False)
    yield from expire(chain.now())
    return {"files": (yield from store.files(orchestra().store_id, args.get("prefix", "/")))}
