import os
import sys
import time
from pathlib import Path

import pytest
import runez

from pickley import bstrap, CFG, PackageSpec, program_version
from pickley.cli import clean_compiled_artifacts, find_base, SoftLock, SoftLockException

from .conftest import dot_meta


def test_base(cli, monkeypatch):
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "foo")
    folder = os.getcwd()
    cli.expect_success("-n base", folder)
    assert "__PYVENV_LAUNCHER__" not in os.environ
    cli.expect_success("-n base audit", dot_meta("audit.log", parent=folder))
    cli.expect_success("-n base cache", dot_meta(".cache", parent=folder))
    cli.expect_success("-n base meta", dot_meta(parent=folder))
    cli.expect_failure("-n base foo", "Unknown base folder reference")

    cli.run("-n base bootstrap-own-wrapper")
    assert cli.succeeded
    assert "Would wrap pickley" in cli.logged

    monkeypatch.setenv("PICKLEY_ROOT", "temp-base")
    with pytest.raises(runez.system.AbortException):  # Env var points to a non-existing folder
        find_base()

    runez.ensure_folder("temp-base", logger=None)
    assert find_base() == CFG.resolved_path("temp-base")

    monkeypatch.delenv("PICKLEY_ROOT")
    assert find_base("/foo/.venv/bin/pickley") == CFG.resolved_path("/foo/.venv/root")
    assert find_base(dot_meta("pickley-0.0.0/bin/pickley", parent="foo")) == CFG.resolved_path("foo")
    assert find_base("foo/bar/baz") == CFG.resolved_path("foo/bar")


def test_dev_mode(cli):
    cli.run("-nv", "install", runez.DEV.project_folder)
    assert cli.succeeded
    assert "pip install -e " in cli.logged
    assert "Would wrap pickley -> .pk/pickley-dev/bin/pickley" in cli.logged
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


@pytest.mark.skipif(not bstrap.USE_UV, reason="to keep test case simple (uv only)")
def test_facultative(cli):
    runez.save_json({"pinned": {"virtualenv": {"facultative": True}}}, dot_meta("config.json"), logger=None)

    cli.run("-n check virtualenv>10000")
    assert cli.failed
    assert "virtualenv>10000: " in cli.logged

    cli.run("-n check virtualenv")
    assert cli.failed
    assert "not installed" in cli.logged

    cli.run("install virtualenv")
    assert cli.succeeded
    assert "Installed virtualenv" in cli.logged

    cli.run("-n install virtualenv")
    assert cli.succeeded
    assert "is already installed" in cli.logged

    cli.run("-v uninstall virtualenv")
    assert cli.succeeded
    assert "Deleted .pk/.manifest/virtualenv.manifest.json" in cli.logged
    assert "Uninstalled virtualenv" in cli.logged

    cli.run("-n install virtualenv")
    assert cli.succeeded
    assert "Would state: Installed virtualenv" in cli.logged

    # Simulate a bogus symlink
    runez.touch("virtualenv-foo", logger=None)
    runez.symlink("virtualenv-foo", "virtualenv", logger=None)
    cli.run("-n check virtualenv")

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
    assert "Skipping installation of virtualenv: not installed by pickley" in cli.logged

    cli.run("-n check virtualenv")
    assert cli.succeeded
    assert "skipped, not installed by pickley" in cli.logged


@pytest.mark.skipif(not bstrap.USE_UV or sys.version_info[:2] < (3, 10), reason="to keep test case simple (uv only)")
def test_install_pypi(cli):
    cli.run("check")
    assert cli.succeeded
    assert "No packages installed" in cli.logged

    cli.run("list")
    assert cli.succeeded
    assert "No packages installed" in cli.logged

    cli.run("upgrade")
    assert cli.succeeded
    assert "No packages installed" in cli.logged

    cli.run("upgrade mgit")
    assert cli.failed
    assert "'mgit' is not installed" in cli.logged

    cli.run("uninstall mgit --all")
    assert cli.failed
    assert "Either specify packages to uninstall, or --all" in cli.logged

    cli.run("uninstall")
    assert cli.failed
    assert "Specify packages to uninstall" in cli.logged

    cli.run("uninstall pickley")
    assert cli.failed
    assert "Run 'uninstall --all' if you wish to uninstall pickley itself" in cli.logged

    cli.run("uninstall mgit")
    assert cli.failed
    assert "mgit was not installed with pickley" in cli.logged

    # Simulate a few older versions to exercise grooming
    runez.touch(".pk/mgit-1.0/bin/mgit", logger=None)
    time.sleep(0.1)
    runez.touch(".pk/mgit-1.1/bin/mgit", logger=None)
    cli.run("install mgit<1.3.0")
    assert cli.succeeded
    assert "Installed mgit v1.2.1" in cli.logged
    assert not os.path.exists(".pk/mgit-1.0")  # Groomed away
    assert os.path.exists(".pk/mgit-1.1")  # Still there (version N-1 kept for 7 days)

    mgit = PackageSpec("mgit")
    manifest = mgit.manifest
    assert str(manifest) == "mgit<1.3.0"
    assert manifest.entrypoints == ["mgit"]
    assert manifest.install_info.args == "install mgit<1.3.0"
    assert manifest.settings.auto_upgrade_spec == "mgit<1.3.0"
    assert manifest.venv_basename == "mgit-1.2.1"
    assert manifest.version == "1.2.1"

    cli.run("-v auto-upgrade mgit")
    assert cli.succeeded
    assert "Skipping auto-upgrade, checked recently" in cli.logged

    cli.run("-v auto-upgrade --force mgit")
    assert cli.succeeded

    cli.run("-v auto-heal")
    assert cli.succeeded
    assert "mgit is healthy" in cli.logged
    assert "Auto-healed 0 / 1 packages" in cli.logged

    cli.run("check")
    assert cli.succeeded
    assert " (currently 1.2.1)" in cli.logged

    runez.delete("mgit", logger=None)
    cli.run("check")
    assert cli.succeeded
    assert " (currently 1.2.1 unhealthy)" in cli.logged

    cli.run("upgrade mgit")
    assert cli.succeeded
    assert "Upgraded mgit v" in cli.logged

    cli.run("check -f")
    assert cli.succeeded
    mgit_version = program_version("mgit")
    assert f"mgit: {mgit_version} up-to-date" in cli.logged

    cli.run("list --format=json")
    assert cli.succeeded
    assert "mgit" in cli.logged

    cli.run("list --format=csv")
    assert cli.succeeded
    assert "mgit" in cli.logged

    cli.run("list --format=yaml")
    assert cli.succeeded
    assert "mgit" in cli.logged

    runez.delete("mgit", logger=None)
    cli.run("auto-heal")
    assert cli.succeeded
    assert "Auto-healed mgit v1.3.0" in cli.logged
    assert "Auto-healed 1 / 1 packages" in cli.logged

    cli.run("uninstall --all")
    assert cli.succeeded
    assert "Uninstalled mgit" in cli.logged
    assert "pickley is now uninstalled" in cli.logged

    cli.run("list")
    assert cli.succeeded
    assert "No packages installed" in cli.logged


@pytest.mark.skipif(not bstrap.USE_UV, reason="to keep test case simple (uv only)")
def test_invalid(cli):
    cli.run("--color install six")
    assert cli.failed
    assert "not a CLI" in cli.logged
    assert not os.path.exists(dot_meta("six.manifest.json"))

    cli.run("install mgit+foo")
    assert cli.failed
    assert "Can't install mgit+foo: " in cli.logged


def test_lock(temp_cfg):
    lock_path = dot_meta("foo.lock")
    with SoftLock("foo", give_up=600) as lock:
        assert str(lock) == "lock foo"
        assert os.path.exists(lock_path)
        with pytest.raises(SoftLockException) as e:
            # Try to grab same lock a seconds time, give up after 1 second
            with SoftLock("foo", give_up=1, invalid=600):
                pass

        assert "giving up" in str(e)

    assert not os.path.exists(lock_path)  # Check that lock was released

    # Check that lock detects bogus (or dead) PID
    runez.write(lock_path, "0\nbar\n", logger=None)
    with SoftLock("foo", give_up=600):
        lines = list(runez.readlines(lock_path))
        assert lines[0] == str(os.getpid())  # Lock file replaced with correct stuff

    assert not os.path.exists(lock_path)  # Lock released


def test_main(cli):
    cli.exercise_main("-mpickley", "src/pickley/bstrap.py")


def test_package_venv(cli):
    # TODO: retire the `package` command, not worth the effort to support it
    # Verify that "debian mode" works as expected, with -droot/tmp <-> /tmp
    runez.delete("/tmp/pickley", logger=None)
    cli.run("package", cli.project_folder, "-droot/tmp", "--sanity-check=--version", "-sroot:root/usr/local/bin", "runez")
    assert cli.succeeded
    assert " install -r requirements.txt" in cli.logged
    assert " install runez" in cli.logged
    assert "pickley --version" in cli.logged
    assert "Symlink /tmp/pickley/bin/pickley <- root/usr/local/bin/pickley" in cli.logged
    assert os.path.islink("root/usr/local/bin/pickley")
    rp = os.path.realpath("root/usr/local/bin/pickley")
    assert os.path.exists(rp)
    assert runez.is_executable("/tmp/pickley/bin/python")
    assert runez.is_executable("/tmp/pickley/bin/pickley")
    r = runez.run("/tmp/pickley/bin/pickley", "--version")
    assert r.succeeded
    runez.delete("/tmp/pickley", logger=None)


def test_version_check(cli):
    cli.run("version-check")
    assert cli.failed
    assert "Specify at least one program" in cli.logged

    cli.run("version-check", "python")
    assert cli.failed
    assert "Invalid argument" in cli.logged

    cli.run("-n version-check python:1.0")
    assert cli.succeeded
    assert cli.match("Would run: python --version")

    cli.run("-v version-check --system python:1.0")
    assert cli.succeeded
    assert "python --version" in cli.logged

    cli.run("version-check --system python:100.0")
    assert cli.failed
    assert "python version too low" in cli.logged
