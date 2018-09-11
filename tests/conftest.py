import os
import shutil
from tempfile import mkdtemp

import pytest

from pickley import system
from pickley.context import CaptureOutput
from pickley.settings import SETTINGS


TESTS = system.parent_folder(__file__)
PROJECT = system.parent_folder(TESTS)
INEXISTING_FILE = "/dev/null/foo/bar"

system.State.testing = True


def sample_path(*relative):
    return os.path.join(TESTS, "samples", *relative)


def verify_abort(func, *args, **kwargs):
    exception = kwargs.pop('exception', SystemExit)
    with CaptureOutput() as logged:
        with pytest.raises(exception):
            func(*args, **kwargs)
        return str(logged)


@pytest.fixture
def temp_base():
    old_base = SETTINGS.base
    old_config = SETTINGS.config
    old_cwd = os.getcwd()
    path = mkdtemp()

    try:
        os.chdir(path)
        SETTINGS.set_base(path)
        yield path

    finally:
        os.chdir(old_cwd)
        SETTINGS.set_base(old_base)
        SETTINGS.load_config(config=old_config)
        shutil.rmtree(path)
