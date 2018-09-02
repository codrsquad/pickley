import os
import shutil
from tempfile import mkdtemp

import pytest

from pickley.cli import setup_debug_log
from pickley.settings import SETTINGS


@pytest.fixture
def temp_base():
    old_base = SETTINGS.base
    old_cwd = os.getcwd()

    path = mkdtemp()
    os.chdir(path)
    setup_debug_log()
    SETTINGS.set_base(path)
    yield path

    os.chdir(old_cwd)
    SETTINGS.set_base(old_base)
    SETTINGS.cli.set_contents({})
    shutil.rmtree(path)
