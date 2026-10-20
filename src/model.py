import json

from ic_python_db import Boolean, Database, Entity, Integer, ManyToOne, OneToMany, String


PAGE = 200
PAGE_MAX = 500


class Json(String):
    # Read gives a copy; write by assignment, in-place edits are not saved.
    def __init__(self, default=None):
        super().__init__(default=json.dumps(default))

    def __get__(self, obj, t=None):
        return self if obj is None else json.loads(super().__get__(obj, t))

    def __set__(self, obj, value):
        super().__set__(obj, json.dumps(value))


class Orchestra(Entity):
    __alias__ = "key"
    key = String(default="orchestra")
    name = String(default="")
    description = String(default="")
    env = String(default="")
    network = String(default="")
    vars = Json({})
    deployer = String(default="")
    built_at = Integer(default=0)
    store_id = String(default="")
    frontend_id = String(default="")
    multisig_id = String(default="")
    reserve = Integer(default=0)
    policy = Json({})
    balances_at = Integer(default=0)
    currency = String(default="USD")
    subnets = Json([])
    creatable_subnets = Json([])
    subnets_at = Integer(default=0)
    monitor = Json({})
    aliases = Json({})
    contacts = Json({})
    snapshots_keep = Integer(default=2)
    up_cursor = Integer(default=1)  # next grant id the `up` round re-resolves


class Section(Entity):
    __alias__ = "name"
    name = String()
    description = String(default="")
    template = Json(None)
    policy = Json({})
    subnet = String(default="")
    stands = OneToMany("Stand", "section")


class Stand(Entity):
    __alias__ = "name"
    name = String()
    description = String(default="")
    section = ManyToOne("Section", "stands")
    members = Json([])
    built_at = Integer(default=0)
    build_error = String(default="")
    # "pending" | "failed" | None (built), kept by set_build. Built is None so the index, one id list per
    # value, only holds the few stands still to build; rows from before it existed are None until reindex.
    state = String(default=None, indexed=True)
    policy = Json({})
    subnet = String(default="")
    release = String(default="")
    baton_synced = Integer(default=0)  # proposed_at of the last baton action baton_sync applied
    canisters = OneToMany("Canister", "stand")

    def __init__(self, **kw):
        # Only values given to the constructor are indexed, not defaults.
        if not kw.get("_loaded"):
            kw.setdefault("state", None if kw.get("built_at") else "failed" if kw.get("build_error") else "pending")
        super().__init__(**kw)


class Canister(Entity):
    __alias__ = "name"
    name = String()
    stand = ManyToOne("Stand", "canisters")
    canister_id = String(default="", indexed=True)
    kind = String(default="backend")
    wasm = String(default="")
    wasm_hash = String(default="")
    status = String(default="created")
    adopted = Boolean(default=False)
    public = Boolean(default=False)
    install_arg = Json(None)
    content = String(default="")
    assets_props = Json({})
    files = Json({})
    controllers = Json([])
    config = Json([])
    policy = Json({})
    tags = Json([])
    cycles = Integer(default=0)
    snapshot = String(default="")
    snapshot_labels = Json({})  # snapshot id → what took it, for the snapshots the conductor took


class Wasm(Entity):
    __alias__ = "key"
    key = String()
    path = String(default="")
    sha256 = String(default="", indexed=True)
    kind = String(default="backend")
    type = String(default="")
    memory_keep = String(default="")


class Grant(Entity):
    __alias__ = "key"
    key = String()
    scope = String(indexed=True)
    principal = String(indexed=True)
    permissions = Json([])
    calls = Json([])


class Event(Entity):
    type = String()
    caller = String(default="")
    canister_id = String(default="")
    payload = Json({})
    ts = Integer(default=0)


def orchestra():
    return Orchestra["orchestra"] or Orchestra(key="orchestra")


def page(cls, start=1, count=PAGE):
    """Rows of ``cls`` from id ``start``, at most ``count`` (≤ PAGE_MAX), and the id the next page
    starts at (None after the last)."""
    count = max(1, min(int(count), PAGE_MAX))
    got = cls.load_some(max(1, int(start)), count)
    return got, (int(got[-1]._id) + 1 if len(got) == count else None)


def page_of(cls, args, name):
    """The page of ``cls`` a list op's caller asked for: ``<name>_from`` (absent: the first page; null:
    none, the caller has the whole list) and ``count``. Callers pass a reply's ``next`` back as is."""
    key = f"{name}_from"
    if key in args and not args[key]:
        return [], None
    return page(cls, args.get(key) or 1, args.get("count") or PAGE_MAX)


def each(cls):
    """Every row of ``cls``, a page at a time. Only for loops that await per row, so that no
    message handles more than a page; lists use page_of, lookups matching."""
    start = 1
    while start:
        got, start = page(cls, start)
        yield from got


def stand_state(stand):
    return None if stand.built_at else "failed" if stand.build_error else "pending"


def set_build(stand, built_at=0, error=""):
    stand.built_at, stand.build_error = built_at, error
    stand.state = stand_state(stand)


# (entity, indexed field, how a row's value is derived when the row predates the field)
INDEXES = ((Grant, "principal", None), (Grant, "scope", None), (Canister, "canister_id", None),
           (Wasm, "sha256", None), (Stand, "state", stand_state))


def _system(key, value=None):
    db = Database.get_instance()
    if value is None:
        return int(db.load("_system", f"casals_{key}") or 0)
    db.save("_system", f"casals_{key}", str(value))


def indexed(cls, field):
    return any(c is cls and f == field for c, f, _ in INDEXES[:_system("indexes")])


def matching(cls, field, value, limit=None):
    """Rows whose ``field`` is ``value``, through the field's index: the cost follows the matches."""
    if not indexed(cls, field):
        derive = next(d for c, f, d in INDEXES if c is cls and f == field) or (lambda r: getattr(r, field))
        out = []
        for row in each(cls):
            if derive(row) == value:
                out.append(row)
                if limit and len(out) >= limit:
                    break
        return out
    out, start = [], 1
    while start and not (limit and len(out) >= limit):
        got, start = cls.find_by(field, value, from_id=start, count=min(limit or PAGE, PAGE))
        out += got
    return out[:limit] if limit else out


def first(cls, field, value):
    return next(iter(matching(cls, field, value, limit=1)), None)


def reindex_pending():
    return ["indexes"] if _system("indexes") < len(INDEXES) else []


def reindex_batch():
    """One batch of the backfill that adds rows from before an index existed to it; True once every
    index in INDEXES is complete. Until then matching() reads that field by paging through rows."""
    done = _system("indexes")
    if done < len(INDEXES):
        cls, field, derive = INDEXES[done]
        start = _system("index_from") or 1
        if derive:
            for row in page(cls, start)[0]:
                setattr(row, field, derive(row))
        nxt = cls.rebuild_field_index(field, from_id=start, batch=PAGE)
        _system("index_from", nxt or 1)
        if not nxt:
            _system("indexes", done + 1)
    return _system("indexes") >= len(INDEXES)


def rename(row, name):
    # ic-python-db keeps the old alias mapping when the alias field changes.
    row.db().delete(row._alias_key(), row.name)
    row.name = name


def lookup(token, stand, this):
    known = orchestra().vars
    if token in known:
        return known[token]
    if token == "$this" and this:
        return this
    row = (Canister[token[10:]] if token.startswith("$canister:")
           else Canister[f"{stand.name}-{token[7:]}"] if stand and token.startswith("$stand.") else None)
    return row.canister_id if row and row.canister_id else token


def resolve(value, stand=None, this=""):
    if isinstance(value, dict):
        return {k: resolve(v, stand, this) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, stand, this) for v in value]
    if not isinstance(value, str):
        return value
    value, out, i = value.replace("{stand}", stand.name) if stand else value, [], 0
    while (j := value.find("$", i)) >= 0:
        k = j + 1
        while k < len(value) and (value[k].isalnum() or value[k] in "_.:-"):
            k += 1
        out += [value[i:j], lookup(value[j:k], stand, this)]
        i = k
    return "".join(out) + value[i:]
