import os

import pytest
import runez
from runez.conftest import cli, logged, temp_folder
from runez.pyenv import PythonDepot

from pickley import DOT_META, PICKLEY, PickleyConfig
from pickley.cli import main


cli.default_exe = PickleyConfig.program_path
cli.default_main = main
PythonDepot.use_path = False
assert logged, temp_folder  # Just making fixtures available, with no complaints about unused imports

PickleyConfig.program_path = PickleyConfig.program_path.replace("pytest", PICKLEY)


def dot_meta(relative=None, parent=None):
    path = DOT_META
    if relative:
        path = os.path.join(path, relative)

    if parent:
        path = os.path.join(parent, path)

    return path


def verify_abort(func, *args, **kwargs):
    exception = kwargs.pop("exception", SystemExit)
    with runez.CaptureOutput() as logged:
        with pytest.raises(exception):
            func(*args, **kwargs)
        return str(logged)


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
