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


def grab_test_uv():
    """Ensure `uv` is downloaded once for all tests, in './build/uv'"""
    build_folder = runez.to_path(runez.DEV.project_path("build"))
    if bstrap.USE_UV:
        uv_path = build_folder / "uv"
        if not bstrap.is_valid_uv_executable(uv_path):
            bstrap.download_uv(build_folder, dryrun=False)

        return uv_path

    forbidden_uv = build_folder / "uv-forbidden"
    if not forbidden_uv.exists():
        runez.write(forbidden_uv, "#!/bin/sh\necho This python version is not supposed to use uv\nexit 1", logger=None)
        runez.make_executable(forbidden_uv, logger=None)

    return forbidden_uv


bstrap._UV_PATH = grab_test_uv()


class TemporaryBase(runez.TempFolder):
    def __enter__(self):
        super(TemporaryBase, self).__enter__()
        os.environ["PICKLEY_ROOT"] = self.tmp_folder
        CFG.reset()
        if bstrap.USE_UV:
            CFG._uv_path = bstrap._UV_PATH
            runez.touch(os.path.join(self.tmp_folder, ".pk/.cache/uv.cooldown"), logger=None)

        return self.tmp_folder

    def __exit__(self, *_):
        super(TemporaryBase, self).__exit__(*_)
        del os.environ["PICKLEY_ROOT"]


cli.context = TemporaryBase


@pytest.fixture
def temp_cfg():
    with TemporaryBase() as base:
        CFG.reset()
        CFG.set_base(base)
        yield CFG
