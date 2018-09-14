import os
import sys

import pytest
from mock import mock_open, patch

from pickley import python_interpreter, system
from pickley.context import CaptureOutput, ImplementationMap
from pickley.lock import SharedVenv, SoftLock, SoftLockException
from pickley.settings import Settings

from .conftest import INEXISTING_FILE, PROJECT, verify_abort


def test_lock(temp_base):
    folder = os.path.join(temp_base, "foo")
    with SoftLock(folder, timeout=10) as lock:
        assert lock._locked()
        with pytest.raises(SoftLockException):
            with SoftLock(folder, timeout=0.01):
                pass
        system.delete_file(folder + ".lock")
        assert not lock._locked()

        with patch("pickley.system.virtualenv_path", return_value=None):
            assert "Can't determine path to virtualenv.py" in verify_abort(SharedVenv, lock)


def test_flattened():
    assert len(system.flattened(None)) == 0
    assert len(system.flattened("")) == 0
    assert system.flattened("a b") == ["a b"]
    assert system.flattened("a b", separator=" ") == ["a", "b"]
    assert system.flattened(["a b"]) == ["a b"]
    assert system.flattened(["a b", ["a b c"]]) == ["a b", "a b c"]
    assert system.flattened(["a b", ["a b c"]], separator=" ") == ["a", "b", "c"]
    assert system.flattened(["a b", ["a b c"], "a"], separator=" ", unique=False) == ["a", "b", "a", "b", "c", "a"]

    assert system.flattened(["a b", [None, "-i", None]]) == ["a b", "-i"]
    assert system.flattened(["a b", [None, "-i", None]], unique=False) == ["a b"]


def test_file_operations(temp_base):
    system.touch("foo")
    with CaptureOutput(dryrun=True) as logged:
        system.copy_file("foo", "bar")
        system.move_file("foo", "bar")
        system.delete_file("foo")
        assert system.make_executable("foo") == 1
        assert system.write_contents("foo", "bar", verbose=True) == 1
        assert "Would copy foo -> bar" in logged
        assert "Would move foo -> bar" in logged
        assert "Would delete foo" in logged
        assert "Would make foo executable" in logged
        assert "Would write 3 bytes to foo" in logged


def test_edge_cases(temp_base):
    assert system.added_env_paths(dict(FOO="."), env=dict(FOO="bar:baz")) == dict(FOO="bar:baz:.")

    assert system.check_pid(None) is False
    assert system.check_pid("foo") is False
    with patch("os.kill", return_value=True):
        assert system.check_pid(5) is True

    assert not system.resolved_path("")

    assert system.write_contents("", "") == 0

    assert system.which("") is None
    assert system.which(INEXISTING_FILE) is None
    assert system.which("foo/bar/baz/not/a/program") is None
    assert system.which("bash")

    assert system.ensure_folder("") == 0

    assert "does not exist" in verify_abort(system.move_file, INEXISTING_FILE, "bar")

    assert "Can't create folder" in verify_abort(system.ensure_folder, INEXISTING_FILE)

    assert system.copy_file("", "") == 0
    assert system.move_file("", "") == 0

    assert system.delete_file("/dev/null", fatal=False) == -1
    assert system.delete_file("/dev/null", fatal=False) == -1
    assert system.make_executable(INEXISTING_FILE, fatal=False) == -1
    assert system.make_executable("/dev/null", fatal=False) == -1

    assert "is not installed" in verify_abort(system.run_program, INEXISTING_FILE)
    assert "exited with code" in verify_abort(system.run_program, "ls", INEXISTING_FILE)

    assert system.run_program(INEXISTING_FILE, fatal=False) is None
    assert system.run_program("ls", INEXISTING_FILE, fatal=False) is None

    # Can't copy non-existing file
    with patch("os.path.exists", return_value=False):
        assert system.copy_file("foo", "bar", fatal=False) == -1

    # Can't read
    with patch("os.path.isfile", return_value=True):
        with patch("os.path.getsize", return_value=10):
            with patch("io.open", mock_open()) as m:
                m.side_effect = Exception
                assert "Can't read" in verify_abort(system.relocate_venv_file, "foo", "source", "dest")

    # Can't write
    with patch("io.open", mock_open()) as m:
        m.return_value.write.side_effect = Exception
        assert "Can't write" in verify_abort(system.write_contents, "foo", "test")

    # Copy/move crash
    with patch("os.path.exists", return_value=True):
        with patch("shutil.copy", side_effect=Exception):
                assert system.copy_file("foo", "bar", fatal=False) == -1
        with patch("shutil.move", side_effect=Exception):
            assert system.move_file("foo", "bar", fatal=False) == -1


@patch("subprocess.Popen", side_effect=Exception)
def test_popen_crash(_):
    assert "ls failed:" in verify_abort(system.run_program, "ls")


def test_real_run():
    old_prefix = getattr(sys, "real_prefix", None)
    sys.real_prefix = None
    assert python_interpreter() == sys.executable
    if old_prefix:
        sys.real_prefix = old_prefix
    else:
        delattr(sys, "real_prefix")

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


def test_relocate_venv_file_successfully(temp_base):
    system.write_contents("foo", "line 1: source\nline 2\n", verbose=True)
    assert system.relocate_venv_file("foo", "source", "dest", fatal=False) == 1
    assert system.get_lines("foo") == ["line 1: dest\n", "line 2\n"]


def test_find_venvs():
    # There's always at least one venv in project when running tests
    # No need to check which ones are there, just that they yield bin folders
    venvs = list(system.find_venvs(PROJECT))
    assert venvs
    assert os.path.basename(venvs[0]) == "bin"
