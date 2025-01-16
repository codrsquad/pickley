import os
import sys
import time
from pathlib import Path

import pytest
import runez

from pickley import bstrap, CFG, PackageSpec
from pickley.cli import clean_compiled_artifacts, find_base, SoftLock, SoftLockException


def test_base(cli, monkeypatch):
    folder = os.getcwd()
    cli.run("-vv base")
    assert cli.succeeded
    assert folder in cli.logged.stdout

    cli.run("-vv base audit.log")
    assert cli.succeeded
    assert ".pk/audit.log" in cli.logged.stdout

    cli.run("-n base foo")
    assert cli.failed
    assert "Can't find 'foo', try:" in cli.logged.stdout

    monkeypatch.delenv("PICKLEY_ROOT")
    assert find_base("/foo/.venv/bin/pickley") == CFG.resolved_path("/foo/.venv/dev_mode")
    assert find_base(CFG.base / "foo/.pk/pickley-0.0.0/bin/pickley") == CFG.resolved_path("foo")
    assert find_base("foo/bar/baz") == CFG.resolved_path("foo/bar")

    monkeypatch.setenv("PICKLEY_ROOT", "temp-base")
    with pytest.raises(runez.system.AbortException):  # Env var points to a non-existing folder
        find_base()

    runez.ensure_folder("temp-base", logger=None)
    assert find_base() == CFG.resolved_path("temp-base")


def test_dev_mode(cli, monkeypatch):
    runez.ensure_folder("dev_mode", logger=None)
    monkeypatch.setenv("PICKLEY_ROOT", "dev_mode")
    cli.run("-nv", "install", runez.DEV.project_folder)
    assert cli.succeeded
    assert "install -e " in cli.logged
    assert "Would state: Installed pickley v" in cli.logged


def test_edge_cases(temp_cfg, logged):
    runez.touch("share/python-wheels/some-wheel.whl", logger=None)
    runez.touch("__pycache__/some_module.py", logger=None)
    runez.touch("some_module.pyc", logger=None)
    logged.pop()
    clean_compiled_artifacts(Path("."))
    assert "Deleted 3 compiled artifacts" in logged.pop()
    assert not os.path.exists("share/python-wheels")
    assert os.path.isdir("share")


def test_facultative(cli):
    cli.run("-n check virtualenv")
    assert cli.failed
    assert "not installed" in cli.logged

    config_path = CFG.meta / "config.json"
    runez.save_json({"pinned": {"virtualenv": {"facultative": True}}}, config_path, logger=None)

    cli.run("-n check virtualenv>10000")
    assert cli.failed
    assert "'virtualenv>10000' is not a canonical pypi package name" in cli.logged

    cli.run("-n check virtualenv")
    assert cli.succeeded
    assert "not installed" in cli.logged

    cli.run("--no-color", "-vv", f"-p{sys.executable}", "install", " virtualenv")
    assert cli.succeeded
    if bstrap.USE_UV:
        assert cli.match("Symlink .+/bin/python.* <- .pk/virtualenv-.+/bin/python.*", regex=True)
        assert cli.match("Created pip wrapper .../bin/pip")
        assert "platformdirs" not in cli.logged  # Verify that --no-color turns off uv output

    assert "Installed virtualenv" in cli.logged

    cli.run("-n install virtualenv")
    assert cli.succeeded
    assert "is already installed" in cli.logged

    cli.run("-vv uninstall virtualenv")
    assert cli.succeeded
    assert "Deleted .pk/.manifest/virtualenv.manifest.json" in cli.logged
    assert "Uninstalled virtualenv" in cli.logged

    cli.run("-n install virtualenv")
    assert cli.succeeded
    assert "Would state: Installed virtualenv" in cli.logged

    # Simulate a bogus symlink
    runez.write("some-folder/virtualenv", "#!/bin/sh\nexit 1", logger=None)
    runez.make_executable("some-folder/virtualenv", logger=None)
    runez.symlink("some-folder/virtualenv", "virtualenv", logger=None)
    cli.run("-n check virtualenv")
    assert cli.succeeded
    assert "present, but not installed by pickley"

    cli.run("-nvv install virtualenv")
    assert cli.succeeded
    assert "Symlink virtualenv -> some-folder/virtualenv does not belong to pickley" in cli.logged
    assert "Skipping facultative installation 'virtualenv', not installed by pickley" in cli.logged

    # Simulate a symlink to an older version
    runez.touch(".pk/virtualenv-1.0/bin/virtualenv", logger=None)
    runez.symlink(".pk/virtualenv-1.0/bin/virtualenv", "virtualenv", logger=None)
    cli.run("-n install virtualenv")
    assert cli.succeeded
    assert "Would state: Installed virtualenv" in cli.logged

    # Empty file -> proceed with install as if it wasn't there
    runez.delete("virtualenv", logger=None)
    runez.touch("virtualenv", logger=None)
    cli.run("-n install virtualenv")
    assert cli.succeeded
    assert "Would state: Installed virtualenv" in cli.logged

    # Simulate pickley wrapper
    runez.write("virtualenv", "echo installed by pickley", logger=None)
    runez.make_executable("virtualenv", logger=None)
    cli.run("-n install virtualenv")
    assert cli.succeeded
    assert "Would state: Installed virtualenv" in cli.logged

    # Unknown executable -> skip pickley installation (since facultative)
    runez.write("virtualenv", "echo foo", logger=None)
    runez.make_executable("virtualenv", logger=None)
    cli.run("-n install virtualenv")
    assert cli.succeeded
    assert "Skipping facultative installation 'virtualenv', not installed by pickley" in cli.logged

    # Fail when not facultative
    runez.delete(config_path, logger=None)
    cli.run("-n install virtualenv")
    assert cli.failed
    assert "virtualenv is not installed by pickley, please uninstall it first" in cli.logged


def test_install_pypi(cli):
    cli.run("check")
    assert cli.succeeded
    assert "No packages installed" in cli.logged

    cli.run("list")
    assert cli.succeeded
    assert " pickley " in cli.logged

    cli.run("list -v")
    assert cli.succeeded
    assert "pickley" in cli.logged

    cli.run("upgrade mgit")
    assert cli.failed
    assert "Can't upgrade 'mgit': not installed with pickley" in cli.logged
    assert "Traceback" not in cli.logged

    cli.run("uninstall mgit --all")
    assert cli.failed
    assert "Either specify packages to uninstall, or --all" in cli.logged

    cli.run("uninstall")
    assert cli.failed
    assert "Specify packages to uninstall" in cli.logged

    cli.run("uninstall pickley")
    assert cli.failed
    assert "Run 'uninstall --all' if you wish to uninstall pickley" in cli.logged

    cli.run("uninstall mgit")
    assert cli.failed
    assert "mgit was not installed with pickley" in cli.logged

    if bstrap.USE_UV:
        cli.run("-nv install yq[test]")
        assert cli.succeeded
        assert "Would run: uv -q pip install yq[test]" in cli.logged

    # Simulate a few older versions to exercise grooming
    runez.touch(".pk/mgit-/bin/mgit", logger=None)  # Simulate a buggy old installation
    runez.touch(".pk/mgit-0.9rc5+local/bin/mgit", logger=None)
    runez.touch(".pk/mgit-1.0/bin/mgit", logger=None)
    time.sleep(0.1)
    runez.touch(".pk/mgit-1.1/bin/mgit", logger=None)
    cli.run("--no-color -vv install mgit<1.3.0")
    assert cli.succeeded
    assert "Installed mgit v1.2.1" in cli.logged
    assert "Deleted .pk/mgit-\n" in cli.logged
    assert "Deleted .pk/mgit-0.9rc5+local\n" in cli.logged
    assert "Deleted .pk/mgit-1.0\n" in cli.logged
    assert "Deleted .pk/mgit-1.1" not in cli.logged
    assert not os.path.exists(".pk/mgit-1.0")  # Groomed away
    assert os.path.exists(".pk/mgit-1.1")  # Still there (version N-1 kept for 7 days) .pk/config

    runez.write(".pk/config.json", '{"installation_retention": 0}', logger=None)
    cli.run("--no-color -vv install mgit<1.3.0")
    assert cli.succeeded
    assert "mgit v1.2.1 is already installed" in cli.logged
    assert "Deleted .pk/mgit-1.1" in cli.logged
    assert not os.path.exists(".pk/mgit-1.1")  # Still there (version N-1 kept for 7 days) .pk/config

    mgit = PackageSpec("mgit")
    manifest = mgit.manifest
    assert str(mgit.resolved_info) == "mgit<1.3.0"
    assert str(manifest) == "mgit<1.3.0"
    assert mgit.auto_upgrade_spec == "mgit<1.3.0"
    assert manifest.entrypoints == ["mgit"]
    assert manifest.install_info.args == "--no-color -vv install mgit<1.3.0"
    assert manifest.settings.auto_upgrade_spec == "mgit<1.3.0"
    assert manifest.version == "1.2.1"

    cli.run("-v auto-upgrade mgit")
    assert cli.succeeded
    assert "Skipping auto-upgrade, checked recently" in cli.logged

    cli.run("-v auto-upgrade --force mgit")
    assert cli.succeeded
    assert "is already up-to-date" in cli.logged

    cli.run("upgrade mgit")
    assert cli.succeeded
    assert "is already up-to-date" in cli.logged

    cli.run("pip show mgit")
    assert cli.succeeded
    assert "pip show mgit" in cli.logged

    cli.run("-v auto-heal")
    assert cli.succeeded
    assert "mgit<1.3.0 is healthy" in cli.logged
    assert "Auto-healed 0 / 1 packages" in cli.logged

    cli.run("check")
    assert cli.succeeded
    assert cli.logged.stdout.contents().strip() == "mgit: v1.2.1 up-to-date (tracks: mgit<1.3.0)"

    runez.delete("mgit", logger=None)
    cli.run("check")
    assert cli.succeeded
    assert cli.logged.stdout.contents().strip() == "mgit: v1.2.1 available (upgrade reason: unhealthy) (tracks: mgit<1.3.0)"

    cli.run("--no-color -vv upgrade mgit")
    assert cli.succeeded
    assert "Using previous authoritative auto-upgrade spec 'mgit<1.3.0'" in cli.logged
    assert "Upgraded mgit v1.2.1" in cli.logged

    cli.run("check -f")
    assert cli.succeeded
    assert cli.logged.stdout.contents().strip() == "mgit: v1.2.1 up-to-date (tracks: mgit<1.3.0)"

    cli.run("install -f mgit<1.4")
    assert cli.succeeded
    assert "Installed mgit v1.3.0" in cli.logged

    cli.run("base mgit")
    assert cli.succeeded
    assert ".pk/mgit-1.3.0" in cli.logged.stdout

    cli.run("list --format=json")
    assert cli.succeeded
    assert "mgit" in cli.logged

    cli.run("list --format=csv")
    assert cli.succeeded
    assert "mgit" in cli.logged

    cli.run("list --format=yaml")
    assert cli.succeeded
    assert "mgit" in cli.logged

    runez.write(".pk/config.json", '{"cache_retention": 0}', logger=None)
    runez.delete("mgit", logger=None)
    cli.run("--no-color -vv auto-heal")
    assert cli.succeeded
    assert "Deleted .pk/.cache/" in cli.logged
    assert "Auto-healed mgit v1.3.0" in cli.logged
    assert "Auto-healed 1 / 1 packages" in cli.logged

    cli.run("uninstall --all")
    assert cli.succeeded
    assert "Uninstalled mgit" in cli.logged
    assert "Uninstalled pickley and 1 package: mgit<1.4" in cli.logged

    cli.run("list")
    assert cli.failed
    assert "This command applies only to bootstrapped pickley installations" in cli.logged


def test_invalid(cli):
    cli.run("-P10.1 check six")
    assert cli.failed
    assert "Invalid python: 10.1 [not available]"

    cli.run("check six")
    assert cli.failed
    assert "not a CLI" in cli.logged

    cli.run("--color install six")
    assert cli.failed
    assert "not a CLI" in cli.logged
    assert not (CFG.meta / "six.manifest.json").exists()

    cli.run("install mgit+foo")
    assert cli.failed
    assert "Can't install mgit+foo: " in cli.logged


def test_lock(temp_cfg, monkeypatch):
    lock_path = CFG.meta / "foo.lock"

    # Verify that a SystemExit honors lock release
    assert not lock_path.exists()
    with SoftLock("foo"):
        assert lock_path.exists()
        with pytest.raises(runez.system.AbortException):
            runez.abort("oops")
    assert not lock_path.exists()

    monkeypatch.setattr(runez.system, "AbortException", SystemExit)
    with SoftLock("foo"):
        assert lock_path.exists()
        with pytest.raises(SystemExit):
            runez.abort("oops")

    with SoftLock("foo", give_up=600) as lock:
        assert str(lock) == "lock foo"
        assert os.path.exists(lock_path)
        with pytest.raises(SoftLockException, match="giving up"):
            # Try to grab same lock a seconds time, give up after 1 second
            with SoftLock("foo", give_up=1, invalid=600):
                raise AssertionError("should not be reached")  # pragma: no cover

    assert not os.path.exists(lock_path)  # Check that lock was released

    # Check that lock detects bogus (or dead) PID
    runez.write(lock_path, "0\nbar\n", logger=None)
    with SoftLock("foo", give_up=600):
        lines = list(runez.readlines(lock_path))
        assert lines[0] == str(os.getpid())  # Lock file replaced with correct stuff

    assert not os.path.exists(lock_path)  # Lock released


def test_main(cli):
    cli.exercise_main("-mpickley", "src/pickley/bstrap.py")


def test_package_command(cli):
    # TODO: retire the `package` command, not worth the effort to support it
    if bstrap.USE_UV:
        # Exercise -mcopileall failure
        cli.run("--no-color", "--package-manager=uv", "package", cli.project_folder, "pyrepl==0.9.0")
        assert cli.failed
        assert "Failed to run `python -mcompileall`" in cli.logged

        # Simulate some artifacts to be picked up by cleanup
        runez.delete(".tox/_pickley_package/dist", logger=None)
        runez.touch(".tox/_pickley_package/dist/a/__pycache__/a.pyc", logger=None)
        runez.touch(".tox/_pickley_package/dist/a/__pycache__/a2.pyc", logger=None)  # Entire folder deleted, counts as 1 deletion
        runez.touch(".tox/_pickley_package/dist/b/b1.pyc", logger=None)
        runez.touch(".tox/_pickley_package/dist/b/b2.pyc", logger=None)
        cli.run("-n", "--package-manager=uv", "package", "--no-compile", cli.project_folder)
        assert cli.succeeded
        assert "Using '.tox/_pickley_package/' as base folder" in cli.logged
        assert "uv -q venv" in cli.logged
        assert "Would delete .tox/_pickley_package/dist/a/__pycache__" in cli.logged
        assert "Would delete .tox/_pickley_package/dist/b/b1.pyc" in cli.logged
        assert "Would delete .tox/_pickley_package/dist/b/b2.pyc" in cli.logged
        assert "Deleted 3 compiled artifacts" in cli.logged

    # Verify that "debian mode" works as expected, with -droot/tmp <-> /tmp
    runez.delete("/tmp/pickley", logger=None)
    cli.run("package", "--base", ".", cli.project_folder, "-droot/tmp", "--sanity-check=--version", "-sroot:root/usr/local/bin", "runez")
    assert cli.succeeded
    assert " install -r requirements.txt" in cli.logged
    assert " install runez" in cli.logged
    assert "Symlink /tmp/pickley/bin/pickley <- root/usr/local/bin/pickley" in cli.logged
    assert "- /tmp/pickley/bin/pickley, --version:" in cli.logged
    assert os.path.islink("root/usr/local/bin/pickley")
    rp = os.path.realpath("root/usr/local/bin/pickley")
    assert os.path.exists(rp)
    assert runez.is_executable("/tmp/pickley/bin/python")
    assert runez.is_executable("/tmp/pickley/bin/pickley")
    assert CFG.program_version("/tmp/pickley/bin/pickley")
    runez.delete("/tmp/pickley", logger=None)


def test_version_check(cli):
    cli.run("version-check")
    assert cli.failed
    assert "Specify at least one program" in cli.logged

    cli.run("version-check", "python")
    assert cli.failed
    assert "Invalid argument" in cli.logged

    cli.run("-n version-check foo:1.0")
    assert cli.failed
    assert "foo is not installed in " in cli.logged

    cli.run("version-check --system python:1.0")
    assert cli.succeeded
    assert cli.logged.stdout.contents().startswith("python ")

    cli.run("version-check --system python:100.0")
    assert cli.failed
    assert "python version too low" in cli.logged
