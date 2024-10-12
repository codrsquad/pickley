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
    # Ensure `uv` is downloaded once for all tests, in the project's `./build/uv` folder
    target = runez.to_path(runez.DEV.project_path("build/uv"))
    uv_path = target / "bin/uv"
    if not runez.is_executable(uv_path):  # pragma: no cover
        bstrap.download_uv(target)

    return uv_path


if bstrap.USE_UV:
    bstrap._UV_PATH = grab_test_uv()


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
        super(TemporaryBase, self).__enter__()
        os.environ["PICKLEY_ROOT"] = self.tmp_folder
        CFG.reset()
        if bstrap.USE_UV:
            CFG._uv_path = bstrap._UV_PATH

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
