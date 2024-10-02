import os

import pytest
import runez
from runez.conftest import cli, logged
from runez.pyenv import PythonDepot

from pickley import bstrap
from pickley.cli import CFG, main

cli.default_main = main
PythonDepot.use_path = False
bstrap.DEFAULT_BASE = ".local/bin"  # Make sure tests stay away from ~/.local/bin
bstrap.PIP_CONFS = ()  # Don't read any pip.conf files in tests
assert logged  # Just making fixtures available, with no complaints about unused imports


def dot_meta(relative=None, parent=None):
    path = bstrap.DOT_META
    if relative:
        if relative.endswith("manifest.json"):
            path = os.path.join(path, ".manifest")

        path = os.path.join(path, relative)

    if parent:
        path = os.path.join(parent, path)

    return path


class TemporaryBase(runez.TempFolder):
    def __enter__(self):
        if bstrap.USE_UV and not bstrap._UV_PATH:
            # Ensure `uv` is downloaded once for all tests, in the project's `./build/uv` folder
            target = runez.to_path(runez.DEV.project_path("build/uv"))
            bstrap._UV_PATH = target / "bin/uv"
            if not runez.is_executable(bstrap._UV_PATH):  # pragma: no cover
                bstrap.download_uv(target)

        super(TemporaryBase, self).__enter__()
        os.environ["PICKLEY_ROOT"] = self.tmp_folder
        CFG.reset()
        return self.tmp_folder

    def __exit__(self, *_):
        super(TemporaryBase, self).__exit__(*_)
        bstrap.clean_env_vars(keys=("PICKLEY_ROOT", "PIP_INDEX_URL", "UV_INDEX_URL"))


cli.context = TemporaryBase


@pytest.fixture
def temp_cfg():
    with TemporaryBase() as base:
        CFG.reset()
        CFG.set_base(base)
        yield CFG
