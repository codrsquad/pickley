import os
import shutil
from tempfile import mkdtemp

import pytest

from pickley import system
from pickley.context import CaptureOutput
from pickley.settings import DOT_PICKLEY  # noqa: imported to ensure that system.SETTINGS is set


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
    old_base = system.SETTINGS.base
    old_config = system.SETTINGS.config
    old_cwd = os.getcwd()
    path = mkdtemp()

    try:
        os.chdir(path)
        system.SETTINGS.set_base(path)
        yield path

    finally:
        os.chdir(old_cwd)
        system.SETTINGS.set_base(old_base)
        system.SETTINGS.load_config(config=old_config)
        shutil.rmtree(path)
