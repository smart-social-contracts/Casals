import json

from ic_python_db import Boolean, Entity, Integer, ManyToOne, OneToMany, String


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
    policy = Json({})
    subnet = String(default="")
    canisters = OneToMany("Canister", "stand")


class Canister(Entity):
    __alias__ = "name"
    name = String()
    stand = ManyToOne("Stand", "canisters")
    canister_id = String(default="")
    kind = String(default="backend")
    wasm = String(default="")
    wasm_hash = String(default="")
    status = String(default="created")
    adopted = Boolean(default=False)
    public = Boolean(default=False)
    install_arg = Json(None)
    content = String(default="")
    files = Json({})
    controllers = Json([])
    config = Json([])
    policy = Json({})
    tags = Json([])
    cycles = Integer(default=0)


class Wasm(Entity):
    __alias__ = "key"
    key = String()
    path = String(default="")
    sha256 = String(default="")
    kind = String(default="backend")
    type = String(default="")
    memory_keep = String(default="")


class Grant(Entity):
    __alias__ = "key"
    key = String()
    scope = String()
    principal = String()
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


def rows(cls):
    return cls.load_some(1, cls.max_id() or 1)


def rename(row, name):
    # ic-python-db keeps the old alias mapping when the alias field changes.
    row.db().delete(row._alias_key(), row.name)
    row.name = name
