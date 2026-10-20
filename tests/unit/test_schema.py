"""The conductor's post_upgrade runs ic-python-db's schema check against the schema the running release
stored; a breaking change traps the upgrade. `schema_released.json` is that stored schema: rewrite it with
`python3 tests/unit/test_schema.py` once a release is live everywhere."""

import json
import pathlib

from ic_python_db import Database
from ic_python_db.schema import build_schema, check_upgrade_compatibility

RELEASED = pathlib.Path(__file__).with_name("schema_released.json")


def test_the_released_conductor_upgrades_to_this_model():
    db = Database.get_instance()
    db.save("_system", "_schema", json.loads(RELEASED.read_text()))
    check_upgrade_compatibility(db)


if __name__ == "__main__":
    import conftest  # noqa: F401  (imports every model)

    RELEASED.write_text(json.dumps(build_schema(Database.get_instance()._entity_types), indent=1, sort_keys=True) + "\n")
