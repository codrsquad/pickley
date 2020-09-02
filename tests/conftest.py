import os

import pytest
import runez
from runez.conftest import cli, logged, temp_folder

from pickley import PickleyConfig
from pickley.cli import main


TESTS = runez.parent_folder(__file__)
PROJECT = runez.parent_folder(TESTS)
INEXISTING_FILE = "/dev/null/foo/bar"


cli.default_main = main
assert logged, temp_folder  # Just making fixtures available, with no complaints about unused imports


def sample_path(*relative):
    return os.path.join(TESTS, "samples", *relative)


def verify_abort(func, *args, **kwargs):
    exception = kwargs.pop('exception', SystemExit)
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
