import os
import time
from pathlib import Path

import pytest
import runez

from pickley import CFG, PackageSpec
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
    assert find_base("/foo/.venv/bin/pickley") == CFG.resolved_path("/foo/.venv/root")
    assert find_base(CFG.base / "foo/.pk/pickley-0.0.0/bin/pickley") == CFG.resolved_path("foo")
    assert find_base("foo/bar/baz") == CFG.resolved_path("foo/bar")

    monkeypatch.setenv("PICKLEY_ROOT", "temp-base")
    with pytest.raises(runez.system.AbortException):  # Env var points to a non-existing folder
        find_base()

    runez.ensure_folder("temp-base", logger=None)
    assert find_base() == CFG.resolved_path("temp-base")


def test_dev_mode(cli, monkeypatch):
    monkeypatch.setenv("PICKLEY_DEV", "1")
    cli.run("-nv", "install", runez.DEV.project_folder)
    assert cli.succeeded
    assert "pip install -e " in cli.logged
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
    CFG.set_base(".")
    config_path = CFG.meta / "config.json"
    runez.save_json({"pinned": {"virtualenv": {"facultative": True}}}, config_path, logger=None)

    cli.run("-n check virtualenv>10000")
    assert cli.failed
    assert "Invalid package name 'virtualenv>10000'" in cli.logged

    cli.run("-n check virtualenv")
    assert cli.failed
    assert "not installed" in cli.logged

    cli.run("install virtualenv")
    assert cli.succeeded
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
    assert "Skipping facultative installation 'virtualenv', not installed by pickley" in cli.logged

    cli.run("-n check virtualenv")
    assert cli.failed
    assert cli.logged.stdout.contents().strip() == "virtualenv: present, but not installed by pickley (v20.26.6 available)"

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
    assert "No packages installed" in cli.logged

    cli.run("list -v")
    assert cli.succeeded
    assert "pickley" in cli.logged

    cli.run("upgrade mgit")
    assert cli.failed
    assert "Can't upgrade 'mgit': not installed with pickley" in cli.logged

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

    # Simulate a few older versions to exercise grooming
    runez.touch(".pk/mgit-1.0/bin/mgit", logger=None)
    time.sleep(0.1)
    runez.touch(".pk/mgit-1.1/bin/mgit", logger=None)
    cli.run("-vv install mgit<1.3.0")
    assert cli.succeeded
    assert "Installed mgit v1.2.1" in cli.logged
    assert "Deleted .pk/mgit-1.0" in cli.logged
    assert "Deleted .pk/mgit-1.1" not in cli.logged
    assert not os.path.exists(".pk/mgit-1.0")  # Groomed away
    assert os.path.exists(".pk/mgit-1.1")  # Still there (version N-1 kept for 7 days) .pk/config

    runez.write(".pk/config.json", '{"installation_retention": 0}', logger=None)
    cli.run("-vv install mgit<1.3.0")
    assert cli.succeeded
    assert "mgit v1.2.1 is already installed" in cli.logged
    assert "Deleted .pk/mgit-1.1" in cli.logged
    assert not os.path.exists(".pk/mgit-1.1")  # Still there (version N-1 kept for 7 days) .pk/config

    mgit = PackageSpec("mgit")
    manifest = mgit.manifest
    assert str(manifest) == "mgit<1.3.0"
    assert mgit.auto_upgrade_spec == "mgit<1.3.0"
    assert manifest.entrypoints == ["mgit"]
    assert manifest.install_info.args == "-vv install mgit<1.3.0"
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
    assert cli.logged.stdout.contents().strip() == "mgit: v1.2.1 available (currently unhealthy) (tracks: mgit<1.3.0)"

    cli.run("-vv upgrade mgit")
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
    cli.run("-vv auto-heal")
    assert cli.succeeded
    assert "Deleted .pk/.cache/" in cli.logged
    assert "Auto-healed mgit v1.3.0" in cli.logged
    assert "Auto-healed 1 / 1 packages" in cli.logged

    cli.run("uninstall --all")
    assert cli.succeeded
    assert "Uninstalled mgit" in cli.logged
    assert "Uninstalled pickley and 1 package: mgit<1.4" in cli.logged

    cli.run("list")
    assert cli.succeeded
    assert "No packages installed" in cli.logged


def test_invalid(cli):
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


def test_lock(temp_cfg):
    lock_path = CFG.meta / "foo.lock"
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
