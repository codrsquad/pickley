import os
import sys

import six
import virtualenv
from mock import patch

from pickley import capture_output, ImplementationMap, python_interpreter, system
from pickley.install import add_paths, PexRunner, Runner
from pickley.package import find_entry_points, find_prefix, find_site_packages

from .conftest import INEXISTING_FILE, verify_abort


def test_find():
    assert find_entry_points("", "", "") is None
    assert find_entry_points(INEXISTING_FILE, "foo", "1.0") is None
    entry_points = find_entry_points(sys.prefix, "virtualenv", virtualenv.__version__)
    assert find_entry_points(sys.prefix, "virtualenv", virtualenv.__version__ + ".0") == entry_points

    if virtualenv.__version__.endswith(".0"):
        assert entry_points == find_entry_points(sys.prefix, "virtualenv", virtualenv.__version__[:-2])

    assert find_entry_points(sys.prefix, "virtualenv", virtualenv.__version__ + ".0.0") is None

    if six.__version__.endswith(".0"):
        assert find_entry_points(sys.prefix, "six", six.__version__[:-2]) is None

    assert find_prefix({}, "") is None

    assert find_site_packages(INEXISTING_FILE) is None


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


def test_edge_cases(temp_base):
    assert system.which(None) is None
    assert system.which("foo/bar") is None
    assert system.which("bash")

    system.ensure_folder(None)
    assert "Can't create folder" in verify_abort(system.ensure_folder, "/dev/null/foo", exception=Exception)

    assert "Can't delete" in verify_abort(system.delete_file, "/dev/null", exception=Exception)
    assert "does not exist" in verify_abort(system.make_executable, "/dev/null/foo")
    assert "Can't chmod" in verify_abort(system.make_executable, "/dev/null", exception=Exception)

    assert "is not installed" in verify_abort(system.run_program, "foo/bar")
    assert "exited with code" in verify_abort(system.run_program, "ls", "foo/bar")

    assert system.run_program("foo/bar", fatal=False) is None
    assert system.run_program("ls", "foo/bar", fatal=False) is None


@patch("subprocess.Popen", side_effect=Exception)
def test_popen_crash(temp_base):
    assert "ls failed:" in verify_abort(system.run_program, "ls")


def test_real_run():
    old_prefix = getattr(sys, "real_prefix", None)
    sys.real_prefix = None
    assert python_interpreter() == sys.executable
    if old_prefix:
        sys.real_prefix = old_prefix
    else:
        delattr(sys, "real_prefix")

    assert len(system.config_paths(True)) == 1
    assert len(system.config_paths(False)) == 2


def test_implementation_map():
    # Check that an implementation without a class_implementation_name() function works
    m = ImplementationMap(None, "custom")
    m.register(ImplementationMap)
    assert len(m.names()) == 1


def test_capture_env():
    env = dict(PATH=None)
    with capture_output() as logged:
        with capture_output(env=env):
            assert "PATH" not in os.environ
        assert "PATH" in os.environ
        assert "Removing env PATH" in logged
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


@patch("pickley.system.DRYRUN", return_value=True)
def test_pex_runner(_, temp_base):
    p = PexRunner(os.path.join(temp_base, "foo"))
    assert not p.is_universal("tox", "1.0")

    p = PexRunner(temp_base)
    assert not p.is_universal("tox", "1.0")

    p = Runner(temp_base)
    assert p.effective_run(None)
    assert p.prelude_args() is None
    assert p.run() is None
    p.effective_run = failed_run
    system.DRYRUN = False
    assert "Failed run" in p.run()
