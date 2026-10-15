import importlib
import os
import pkgutil
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")


def test_every_cli_module_imports():
    sys.path.insert(0, ROOT)
    try:
        import casals_cli

        for m in pkgutil.iter_modules(casals_cli.__path__):
            importlib.import_module(f"casals_cli.{m.name}")
    finally:
        sys.path.remove(ROOT)
