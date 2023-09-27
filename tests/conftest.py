import os

import pytest
import runez
from runez.conftest import cli, logged, temp_folder
from runez.http import GlobalHttpCalls
from runez.pyenv import PythonDepot

from pickley import DOT_META, PickleyConfig
from pickley.cli import main
from pickley.package import PythonVenv


cli.default_main = main
GlobalHttpCalls.forbid()
PythonDepot.use_path = False
PythonVenv._vv_fallback = None
assert logged, temp_folder  # Just making fixtures available, with no complaints about unused imports


def dot_meta(relative=None, parent=None):
    path = DOT_META
    if relative:
        path = os.path.join(path, relative)

    if parent:
        path = os.path.join(parent, path)

    return path


class TemporaryBase(runez.TempFolder):
    def __enter__(self):
        super(TemporaryBase, self).__enter__()
        os.environ["PICKLEY_ROOT"] = self.tmp_folder
        return self.tmp_folder

    def __exit__(self, *_):
        super(TemporaryBase, self).__exit__(*_)
        del os.environ["PICKLEY_ROOT"]


cli.context = TemporaryBase


@pytest.fixture
def temp_cfg():
    with TemporaryBase() as base:
        cfg = PickleyConfig()
        cfg.set_base(base)
        yield cfg
