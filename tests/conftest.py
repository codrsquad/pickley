import os
import shutil
from tempfile import mkdtemp

import pytest
import runez

from pickley import system
from pickley.settings import DOT_PICKLEY  # noqa: imported to ensure that system.SETTINGS is set


TESTS = runez.parent(__file__)
PROJECT = runez.parent(TESTS)
INEXISTING_FILE = "/dev/null/foo/bar"

runez.State.testing = True


def sample_path(*relative):
    return os.path.join(TESTS, "samples", *relative)


def verify_abort(func, *args, **kwargs):
    exception = kwargs.pop('exception', SystemExit)
    with runez.CaptureOutput() as logged:
        with pytest.raises(exception):
            func(*args, **kwargs)
        return str(logged)


@pytest.fixture
def temp_base():
    old_base = system.SETTINGS.base
    old_config = system.SETTINGS.config
    old_cwd = os.getcwd()
    path = os.path.realpath(mkdtemp())

    try:
        os.chdir(path)
        system.SETTINGS.set_base(path)
        yield path

    finally:
        os.chdir(old_cwd)
        system.SETTINGS.set_base(old_base)
        system.SETTINGS.load_config(config=old_config)
        shutil.rmtree(path)
