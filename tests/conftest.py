import os
import shutil
from tempfile import mkdtemp

import pytest

from pickley import capture_output, system
from pickley.cli import setup_debug_log
from pickley.settings import SETTINGS


TESTS = system.parent_folder(__file__)
PROJECT = system.parent_folder(TESTS)
INEXISTING_FILE = "/dev/null/foo/bar"


def sample_path(*relative):
    return os.path.join(TESTS, "samples", *relative)


def verify_abort(func, *args, **kwargs):
    exception = kwargs.pop('exception', SystemExit)
    with capture_output() as logged:
        with pytest.raises(exception):
            func(*args, **kwargs)
        return str(logged)


@pytest.fixture
def temp_base():
    setup_debug_log()
    old_base = SETTINGS.base
    old_cwd = os.getcwd()

    path = mkdtemp()
    os.chdir(path)
    SETTINGS.set_base(path)
    yield path

    os.chdir(old_cwd)
    SETTINGS.set_base(old_base)
    SETTINGS.cli.set_contents({})
    shutil.rmtree(path)
