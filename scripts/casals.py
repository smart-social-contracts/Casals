#!/usr/bin/env python3
"""Thin entry point for running the Casals CLI directly from a repo checkout.

For the installed command (``pip install ic-casals``) the ``casals`` console
script calls ``casals_cli.main()`` directly. This wrapper lets you also run:

    python3 scripts/casals.py status
    python3 scripts/casals.py sheet deploy my-sheet.json

from anywhere inside the repo without installing the package. It adds the repo
root to sys.path so that ``casals_cli`` is importable regardless of CWD.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from casals_cli import main  # noqa: E402

if __name__ == "__main__":
    main()
