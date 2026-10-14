import json

import fake_chain
import op
from model import Canister, Grant, Section, Stand


def call(name, caller="ctl", /, **args):
    fake_chain.state["caller"] = caller
    return json.loads(op.run(op.dispatch(name, json.dumps(args))))


def world():
    for n in "12":
        sec = Section(name=f"s{n}")
        stand = Stand(name=f"d{n}")
        stand.section = sec
        can = Canister(name=f"c{n}", canister_id=f"c{n}-id")
        can.stand = stand


def grant(principal, scope, *perms, calls=()):
    Grant(key=f"{scope}|{principal}", scope=scope, principal=principal,
          permissions=sorted(perms), calls=list(calls))
