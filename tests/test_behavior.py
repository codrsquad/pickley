import logging
import os

import pytest
import runez
from mock import patch

from pickley import system
from pickley.context import ImplementationMap
from pickley.delivery import relocate_venv
from pickley.lock import SharedVenv, SoftLock, SoftLockException
from pickley.settings import Settings

from .conftest import verify_abort


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

        with patch("pickley.lock.virtualenv_path", return_value=None):
            assert "Can't determine path to virtualenv.py" in verify_abort(SharedVenv, lock, None)


@patch("runez.run", return_value="pex==1.0")
@patch("runez.is_younger", return_value=True)
def test_ensure_freeze(_, __, temp_base):
    # Test edge case for _installed_module()
    with SoftLock(temp_base) as lock:
        fake_pex = os.path.join(temp_base, "bin/pex")
        runez.touch(fake_pex)
        runez.make_executable(fake_pex)
        v = SharedVenv(lock, None)
        assert v._installed_module(system.PackageSpec("pex"))


def test_config():
    s = Settings()
    s.load_config()
    assert len(s.config_paths) == 1
    s.load_config("foo.json")
    assert len(s.config_paths) == 2


def test_missing_implementation():
    m = ImplementationMap("custom")
    m.register(ImplementationMap)
    foo = system.PackageSpec("foo")
    assert len(m.names()) == 1
    assert "No custom type configured" in verify_abort(m.resolved, foo)
    system.SETTINGS.cli.contents["custom"] = "bar"
    assert "Unknown custom type" in verify_abort(m.resolved, foo)


def test_relocate_venv_successfully(temp_base):
    with runez.CaptureOutput() as logged:
        original = "line 1: source\nline 2\n"
        runez.write("foo/bar/bin/baz", original, logger=logging.debug)
        runez.write("foo/bar/bin/empty", "", logger=logging.debug)
        runez.write("foo/bar/bin/python", "", logger=logging.debug)
        runez.make_executable("foo/bar/bin/baz")
        runez.make_executable("foo/bar/bin/empty")
        runez.make_executable("foo/bar/bin/python")
        assert "Created" in logged.pop()

        # Simulate already seen
        expected = ["line 1: source\n", "line 2\n"]
        assert relocate_venv("foo", "source", "dest", fatal=False, _seen={"foo"}) == 0
        assert runez.get_lines("foo/bar/bin/baz") == expected
        assert not logged

        # Simulate failure to write
        with patch("runez.write", return_value=-1):
            assert relocate_venv("foo", "source", "dest", fatal=False) == -1
        assert runez.get_lines("foo/bar/bin/baz") == expected
        assert not logged

        # Simulate effective relocation, by folder
        expected = ["line 1: dest\n", "line 2\n"]
        assert relocate_venv("foo", "source", "dest", fatal=False) == 1
        assert runez.get_lines("foo/bar/bin/baz") == expected
        assert "Relocated " in logged

        # Second relocation is a no-op
        assert relocate_venv("foo", "source", "dest", fatal=False) == 0

        # Test relocating a single file
        runez.write("foo/bar/bin/baz", original, logger=logging.debug)
        assert relocate_venv("foo/bar/bin/baz", "source", "dest", fatal=False) == 1
        assert "Relocated " in logged
