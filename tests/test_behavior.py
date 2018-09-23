import os

import pytest
import runez
from mock import patch

from pickley import system
from pickley.context import ImplementationMap
from pickley.lock import SharedVenv, SoftLock, SoftLockException
from pickley.settings import Settings

from .conftest import PROJECT, verify_abort


def test_lock(temp_base):
    folder = os.path.join(temp_base, "foo")
    with SoftLock(folder, timeout=10) as lock:
        assert lock._locked()
        with pytest.raises(SoftLockException):
            with SoftLock(folder, timeout=0.01):
                pass
        assert str(lock) == folder + ".lock"
        runez.delete(str(lock))
        assert not lock._locked()

        with patch("pickley.system.virtualenv_path", return_value=None):
            assert "Can't determine path to virtualenv.py" in verify_abort(SharedVenv, lock, None)


@patch("runez.run_program", return_value="pex==1.0")
@patch("runez.file_younger", return_value=True)
def test_ensure_freeze(_, __, temp_base):
    # Test edge case for _installed_module()
    with SoftLock(temp_base) as lock:
        fake_pex = os.path.join(temp_base, "bin/pex")
        runez.touch(fake_pex)
        runez.make_executable(fake_pex)
        v = SharedVenv(lock, None)
        assert v._installed_module("pex")


def test_config():
    s = Settings()
    s.load_config()
    assert len(s.config_paths) == 1
    s.load_config("foo.json")
    assert len(s.config_paths) == 2


def test_missing_implementation():
    m = ImplementationMap("custom")
    m.register(ImplementationMap)
    assert len(m.names()) == 1
    assert "No custom type configured" in verify_abort(m.resolved, "foo")
    system.SETTINGS.cli.contents["custom"] = "bar"
    assert "Unknown custom type" in verify_abort(m.resolved, "foo")


def test_relocate_venv_successfully(temp_base):
    runez.write_contents("foo", "line 1: source\nline 2\n", quiet=False)
    assert system.relocate_venv("foo", "source", "dest", fatal=False) == 1
    assert runez.get_lines("foo") == ["line 1: dest\n", "line 2\n"]


def test_find_venvs():
    # There's always at least one venv in project when running tests
    # Just need to check that we yield bin folders
    venvs = list(system.find_venvs(PROJECT))
    assert venvs
    assert os.path.basename(venvs[0]) == "bin"
