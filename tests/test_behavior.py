import os
import sys
import time

import pytest
from mock import patch

from pickley import CaptureOutput, ImplementationMap, PingLock, PingLockException, python_interpreter, relocate_venv_file, system
from pickley.install import add_paths, PexRunner, Runner
from pickley.settings import Settings

from .conftest import INEXISTING_FILE, verify_abort


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
        system.make_executable("foo")
        system.write_contents("foo", "bar")
        assert "Would copy foo -> bar" in logged
        assert "Would move foo -> bar" in logged
        assert "Would delete foo" in logged
        assert "Would make foo executable" in logged
        assert "Would write 3 bytes to foo" in logged

    work = os.path.join(temp_base, "work")
    system.ensure_folder(work, folder=True)
    with CaptureOutput() as logged:
        with PingLock(work, seconds=1) as lock:
            assert lock.is_young()
            with pytest.raises(PingLockException):
                with PingLock(work, seconds=10):
                    pass
            time.sleep(1.2)
            assert not lock.is_young()


def test_edge_cases():
    assert system.resolved_path(None) is None

    assert system.write_contents(None, None) is None

    assert system.which(None) is None
    assert system.which(INEXISTING_FILE) is None
    assert system.which("foo/bar/baz/not/a/program") is None
    assert system.which("bash")

    system.ensure_folder(None)

    assert "does not exist" in verify_abort(system.move_file, INEXISTING_FILE, "bar")

    assert "Can't create folder" in verify_abort(system.ensure_folder, INEXISTING_FILE, exception=Exception)

    assert "Can't delete" in verify_abort(system.delete_file, "/dev/null", exception=Exception)
    assert "does not exist" in verify_abort(system.make_executable, INEXISTING_FILE)
    assert "Can't chmod" in verify_abort(system.make_executable, "/dev/null", exception=Exception)

    assert "is not installed" in verify_abort(system.run_program, INEXISTING_FILE)
    assert "exited with code" in verify_abort(system.run_program, "ls", INEXISTING_FILE)

    assert system.run_program(INEXISTING_FILE, fatal=False) is None
    assert system.run_program("ls", INEXISTING_FILE, fatal=False) is None


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
    s.load_config(testing=True)
    assert len(s.config) == 1
    s.load_config(testing=False)
    assert len(s.config) == 2


def test_implementation_map():
    # Check that an implementation without a class_implementation_name() function works
    s = Settings()
    m = ImplementationMap(s, "custom")
    m.register(ImplementationMap)
    assert len(m.names()) == 1
    assert "No custom type configured" in verify_abort(m.resolved, "foo")
    s.set_cli_config(custom="bar")
    assert "Unknown custom type" in verify_abort(m.resolved, "foo")


def test_capture_env():
    env = dict(PATH=None, JAVA_HOME="/some-place/java")
    with CaptureOutput() as logged:
        with CaptureOutput(env=env):
            os.environ["ENV_ADDED"] = "testing"
            assert "PATH" not in os.environ
            assert os.environ.get("JAVA_HOME") == "/some-place/java"
        assert "PATH" in os.environ
        assert "Cleaning up env ENV_ADDED" in logged
        assert "Removing env PATH" in logged
        assert "Customizing env JAVA_HOME=/some-place/java" in logged
        assert "Restoring env PATH=" in logged
    assert "PATH" in os.environ


@patch.dict(os.environ, dict(FOO="bar:baz"), clear=True)
def test_add_paths():
    result = {}
    add_paths(result, "FOO", ".")
    assert result.get("FOO") == "bar:baz:."


def failed_run(*_):
    system.error("Failed run")
    sys.exit(1)


def test_pex_runner(temp_base):
    with CaptureOutput(dryrun=True):
        p = PexRunner(os.path.join(temp_base, "foo"))
        assert not p.is_universal("tox", "1.0")

        p = PexRunner(temp_base)
        assert not p.is_universal("tox", "1.0")

        p = Runner(temp_base)

        # Edge cases
        p.effective_run = lambda *_: 1
        assert p.run() is None

        p.effective_run = failed_run
        system.dryrun = False
        assert "Failed run" in p.run()


def test_bad_copy(temp_base):
    assert "does not exist, can't copy" in verify_abort(system.copy_file, "foo", "bar")


def io_write_fail(name, mode, *_):
    if mode == "rt":
        return open(name, mode)
    raise Exception("oops")


@patch("io.open", side_effect=Exception("utf-8"))
def test_relocate_venv_non_utf(_, temp_base):
    system.touch("foo")
    assert relocate_venv_file("foo", "source", "dest") is False


@patch("io.open", side_effect=Exception)
def test_relocate_venv_read_crash(_, temp_base):
    system.touch("foo")
    assert "Can't relocate" in verify_abort(relocate_venv_file, "foo", "source", "dest")


@patch("io.open", side_effect=io_write_fail)
def test_relocate_venv_write_crash(_, temp_base):
    system.write_contents("foo", "line 1: source\nline 2\n")
    assert "Can't relocate" in verify_abort(relocate_venv_file, "foo", "source", "dest")


def test_relocate_venv_file_successfully(temp_base):
    lines = "line 1: source\nline 2\n"
    system.write_contents("foo", lines)
    relocate_venv_file("foo", "source", "dest")

    expected = ["line 1: dest\n", "line 2\n"]
    with open("foo", "rt") as fh:
        assert fh.readlines() == expected
