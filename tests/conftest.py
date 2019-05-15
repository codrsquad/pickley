import os

import pytest
import runez
from runez.conftest import cli

from pickley import system
from pickley.cli import main
from pickley.settings import DOT_PICKLEY  # noqa: imported to ensure that system.SETTINGS is set


TESTS = runez.parent_folder(__file__)
PROJECT = runez.parent_folder(TESTS)
INEXISTING_FILE = "/dev/null/foo/bar"


cli.default_main = main


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
def temp_base():
    with TemporaryBase() as base:
        yield base
