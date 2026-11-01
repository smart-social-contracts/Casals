import glob
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "src")
sys.path[:0] = [HERE, SRC]

import fake_chain  # noqa: E402

sys.modules["chain"] = sys.modules["store"] = fake_chain

from ic_python_db import Database  # noqa: E402

Database.init()
for path in sorted(glob.glob(os.path.join(SRC, "*.py"))):
    name = os.path.basename(path)[:-3]
    if name not in ("main", "chain", "store"):
        importlib.import_module(name)


@pytest.fixture(autouse=True)
def fresh():
    import content
    import grants
    import model
    import rounds
    import wasms

    Database.get_instance().clear()
    model._system("indexes", len(model.INDEXES))
    fake_chain.reset()
    for heap in (grants.failures, rounds.queue, rounds.armed, rounds.running, content.deploys, content.bundles, wasms.uploads):
        heap.clear()
