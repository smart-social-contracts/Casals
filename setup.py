"""The CLI shares a few pure modules with the canister (src/). The wheel carries
copies under casals_cli/_shared/, which casals_cli/__init__.py puts on sys.path
when there is no checkout; they are not installed as top-level modules.
scripts/check_wheel.py checks the copies against src/."""

import os

from setuptools import setup
from setuptools.command.build_py import build_py

SHARED_MODULES = ("sheetv2", "access_code", "auth", "ic_assets", "commanders")


class BuildPy(build_py):
    def run(self):
        super().run()
        dest = os.path.join(self.build_lib, "casals_cli", "_shared")
        self.mkpath(dest)
        for name in SHARED_MODULES:
            self.copy_file(os.path.join("src", f"{name}.py"), os.path.join(dest, f"{name}.py"))


setup(cmdclass={"build_py": BuildPy})
