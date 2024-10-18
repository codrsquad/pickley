import os

import pytest
import runez
from runez.conftest import cli, logged
from runez.pyenv import PythonDepot

from pickley import bstrap
from pickley.cli import CFG, main


def mocked_expanduser(path):
    if path and path.startswith("~/"):
        path = path[2:]

    return path


cli.default_main = main
PythonDepot.use_path = False
bstrap.expanduser = mocked_expanduser
assert logged  # Just making fixtures available, with no complaints about unused imports

TEST_UV = bstrap.UvBootstrap(runez.to_path(runez.DEV.project_path("build/test-uv")))
TEST_UV.auto_bootstrap_uv()


class TemporaryBase(runez.TempFolder):
    def __enter__(self):
        super(TemporaryBase, self).__enter__()
        os.environ["PICKLEY_ROOT"] = self.tmp_folder
        # Provide a `uv` binary out-of-the-box so that tests don't have to bootstrap uv over and over
        runez.copy(TEST_UV.uv_path, os.path.join(self.tmp_folder, "uv"), logger=None)
        runez.touch(os.path.join(self.tmp_folder, ".pk/.cache/uv.cooldown"), logger=None)
        runez.save_json({"vpickley": "0.0.0"}, ".pk/.manifest/.bootstrap.json", logger=None)
        CFG.reset()
        return self.tmp_folder

    def __exit__(self, *_):
        super(TemporaryBase, self).__exit__(*_)
        del os.environ["PICKLEY_ROOT"]


cli.context = TemporaryBase


@pytest.fixture
def temp_cfg():
    with TemporaryBase() as base:
        CFG.set_base(base)
        yield CFG
