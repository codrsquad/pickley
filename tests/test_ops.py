import os
import sys
import time

import pytest
import runez
from mock import patch
from runez.http import GlobalHttpCalls

from pickley import __version__, get_program_path, PackageSpec, PICKLEY, PickleyConfig, TrackedManifest
from pickley.cli import clean_compiled_artifacts, find_base, PackageFinalizer, SoftLock, SoftLockException
from pickley.delivery import WRAPPER_MARK
from pickley.package import Packager

from .conftest import dot_meta


def test_base(cli, monkeypatch):
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "foo")
    folder = os.getcwd()
    cli.expect_success("-n base", folder)
    cli.expect_success("-n base audit", dot_meta("audit.log", parent=folder))
    cli.expect_success("-n base cache", dot_meta(".cache", parent=folder))
    cli.expect_success("-n base meta", dot_meta(parent=folder))
    cli.expect_failure("-n base foo", "Unknown base folder reference")

    cli.run("-n base bootstrap-own-wrapper")
    assert cli.succeeded
    assert "Would wrap pickley" in cli.logged

    monkeypatch.setenv("PICKLEY_ROOT", "temp-base")
    with pytest.raises(SystemExit):  # Env var points to a non-existing folder
        find_base()

    runez.ensure_folder("temp-base")
    assert find_base() == runez.resolved_path("temp-base")

    assert sys.prefix in get_program_path("foo/bar.py")

    monkeypatch.delenv("PICKLEY_ROOT")
    monkeypatch.setattr(PickleyConfig, "program_path", "/foo/.venv/bin/pickley")
    assert find_base() == "/foo/.venv/root"

    monkeypatch.setattr(PickleyConfig, "program_path", dot_meta("pickley-0.0.0/bin/pickley", parent="foo"))
    assert find_base() == "foo"

    monkeypatch.setattr(PickleyConfig, "program_path", "foo/bar")
    assert find_base() == "foo"


def dummy_finalizer(dist, symlink="root:root/usr/local/bin"):
    p = PackageFinalizer("foo", dist, symlink)
    p.resolve()
    assert p.pspec.dashed == "foo"
    return p


def test_debian_mode(temp_folder, logged):
    runez.write("foo/setup.py", "import setuptools\nsetuptools.setup(name='foo', version='1.0')")
    p = dummy_finalizer("root/apps")
    assert p.dist == "root/apps/foo"
    assert p.requirements == [runez.resolved_path("foo")]
    assert "Using python:" in logged.pop()

    # Symlink not created unless source effectively exists
    p.symlink.apply("root/foo")
    assert "skipping symlink" in logged.pop()
    assert not os.path.isdir("root/usr/local/bin")

    foo = runez.resolved_path("root/foo")
    runez.touch(foo)
    logged.pop()

    # Simulate symlink
    p.symlink.apply(foo)
    assert "Symlinked root/usr/local/bin/foo -> root/foo" in logged.pop()
    assert os.path.isdir("root/usr/local/bin")
    assert os.path.islink("root/usr/local/bin/foo")

    with patch("os.path.isdir", return_value=True):  # pretend /apps exists
        p = dummy_finalizer("root/apps")
        assert "debian mode" in logged.pop()
        assert p.dist == "/apps/foo"

    with patch("runez.run", return_value=runez.program.RunResult("usage: ...")):
        assert p.validate_sanity_check("foo", "--version") == "does not respond to --version"

    with patch("runez.run", return_value=runez.program.RunResult("failed")):
        with pytest.raises(SystemExit):
            p.validate_sanity_check("foo", "--version")

        assert "'foo' failed --version sanity check" in logged.pop()


def mock_git_clone(pspec):
    basename = runez.basename(pspec.original)
    pspec.folder = pspec.cfg.cache.full_path("checkout", basename)
    setup_py = os.path.join(pspec.folder, "setup.py")
    runez.write(setup_py, "from setuptools import setup\nsetup(name='%s', version='1.0')\n" % basename, dryrun=False)


@GlobalHttpCalls.allowed
def test_dryrun(cli, monkeypatch):
    cli.run("-n --debug auto-upgrade mgit")
    assert cli.succeeded
    assert "Would wrap mgit" in cli.logged
    runez.touch(dot_meta("mgit.lock"))
    cli.run("-n --debug auto-upgrade mgit")
    assert cli.succeeded
    assert "Lock file present, another installation is in progress" in cli.logged

    cli.expect_success("-n check", "No packages installed")
    cli.expect_failure("-n check foo+bar", "'foo+bar' is not a valid pypi package name")
    cli.expect_failure("-n check mgit pickley2-a", "is not installed", "pickley2-a: does not exist")

    cli.run("-n config")
    assert cli.succeeded
    assert not cli.logged.stderr
    assert "cli:  # empty" in cli.logged.stdout
    assert "defaults:" in cli.logged.stdout

    cli.run("-n --color config")
    assert cli.succeeded

    cli.expect_failure("-n -Pfoo install bundle:bar", "No suitable python")

    # Simulate an old entry point that was now removed
    runez.write(dot_meta("mgit/.manifest.json"), '{"entrypoints": ["bogus-mgit"]}')
    cli.expect_failure("-n install mgit pickley2.a", "Would state: Installed mgit v", "'pickley2.a' is not pypi canonical")
    runez.delete(dot_meta("mgit"))

    cli.expect_success("-n -Pfoo diagnostics", "desired python : foo", "foo [not available]", "sys.executable")
    with patch("runez.run", return_value=runez.program.RunResult("failed")):
        cli.run("-n install git@github.com:zsimic/mgit.git")
        assert cli.failed
        assert cli.match("No setup.py")

    with patch("pickley.git_clone", side_effect=mock_git_clone):
        cli.run("-n install git@github.com:zsimic/mgit.git")
        assert cli.succeeded
        assert " install .pickley/.cache/checkout/mgit" in cli.logged

    cli.run("-n -Pinvoker install --no-binary :all: mgit")
    assert cli.succeeded
    assert " --no-binary :all: mgit" in cli.logged
    assert cli.match("Would wrap mgit -> %s" % dot_meta("mgit"))
    assert cli.match("Would save %s" % dot_meta("mgit/.manifest.json"))
    assert cli.match("Would state: Installed mgit v")

    cli.expect_failure("-n -dfoo install mgit", "Unknown delivery method 'foo'")

    cli.expect_success("-n list", "No packages installed")

    cli.expect_failure("-n package foo", "Folder ... does not exist")
    cli.expect_failure("-n package . -sfoo", "Invalid symlink specification")
    cli.expect_failure("-n package . -sroot:root/usr/local/bin", "No setup.py in ")

    runez.touch("setup.py")
    cli.expect_failure("-n package .", "Could not determine package name")
    runez.write("setup.py", "import sys\nfrom setuptools import setup\nif sys.argv[1]=='--version': sys.exit(1)\nsetup(name='foo')")
    cli.expect_failure("-n package .", "Could not determine package version")

    cli.expect_success(["-n", "package", cli.project_folder], "Would run: ...pip... install -r requirements.txt")

    cli.expect_failure("-n uninstall", "Specify packages to uninstall, or --all")
    cli.expect_failure("-n uninstall pickley", "Run 'uninstall --all' if you wish to uninstall pickley itself")
    cli.expect_failure("-n uninstall mgit", "mgit was not installed with pickley")
    cli.expect_failure("-n uninstall mgit --all", "Either specify packages to uninstall, or --all (but not both)")
    cli.expect_success("-n uninstall --all", "pickley is now uninstalled")

    cli.expect_success("-n upgrade", "No packages installed, nothing to upgrade")
    cli.expect_failure("-n upgrade mgit", "'mgit' is not installed")


def test_edge_cases(temp_cfg, monkeypatch, logged):
    monkeypatch.setattr(PickleyConfig, "_pickley_dev_path", None)
    pspec = PackageSpec(temp_cfg, PICKLEY)
    assert pspec._pickley_dev_mode == runez.DEV.project_folder
    assert pspec.install_path.endswith("%s-dev" % PICKLEY)

    assert pspec.find_wheel(".", fatal=False) is None
    assert "Expecting 1 wheel" in logged.pop()

    runez.touch("%s-1.0.0.whl" % PICKLEY)
    w = pspec.find_wheel(".", fatal=False)
    assert w == "./%s-1.0.0.whl" % PICKLEY

    with pytest.raises(NotImplementedError):
        Packager.package(None, None, None, None, False)

    runez.touch("share/python-wheels/some-wheel.whl")
    runez.touch("__pycache__/some_module.py")
    runez.touch("some_module.pyc")
    logged.pop()
    clean_compiled_artifacts(".")
    assert "Deleted 3 compiled artifacts" in logged.pop()
    assert not os.path.exists("share/python-wheels")
    assert os.path.isdir("share")


@GlobalHttpCalls.allowed
def test_facultative(cli):
    runez.save_json({"pinned": {"virtualenv": {"facultative": True}}}, dot_meta("config.json"))

    # Empty file -> proceed with install as if it wasn't there
    runez.touch("virtualenv")
    cli.expect_success("-n install virtualenv", "Would state: Installed virtualenv")

    # Simulate pickley wrapper
    runez.write("virtualenv", "echo installed by pickley")
    runez.make_executable("virtualenv")
    cli.expect_success("-n install virtualenv", "Would state: Installed virtualenv")

    # Unknown executable -> skip pickley installation (since facultative)
    runez.write("virtualenv", "echo foo")
    runez.make_executable("virtualenv")
    cli.expect_success("-n install virtualenv", "Skipping installation of virtualenv: not installed by pickley")
    cli.expect_success("-n check virtualenv", "skipped, not installed by pickley")

    # --force ignores 'facultative' setting
    cli.expect_failure("-n install --force virtualenv", "Can't automatically uninstall virtualenv")

    # Simulate pickley symlink delivery
    dummy_target = dot_meta("foo")
    runez.touch(dummy_target)
    runez.symlink(dummy_target, "virtualenv")
    cli.expect_success("-n install virtualenv", "Would state: Installed virtualenv")


def check_is_wrapper(path, is_wrapper):
    if is_wrapper:
        assert not os.path.islink(path)
        contents = runez.readlines(path)
        assert WRAPPER_MARK in contents

    else:
        assert os.path.islink(path)

    r = runez.run(path, "--version")
    assert r.succeeded


def check_install_from_pypi(cli, delivery, package, simulate_version=None):
    cli.run("--debug", "-d%s" % delivery, "install", package)
    assert cli.succeeded
    assert cli.match("Installed %s" % package)
    assert runez.is_executable(package)
    m = TrackedManifest.from_file(dot_meta("%s/.manifest.json" % package))
    assert str(m)
    assert m.entrypoints[package]
    assert m.install_info.args == runez.quoted(cli.args)
    assert m.install_info.timestamp
    assert m.install_info.vpickley == __version__
    assert m.settings.delivery == delivery
    assert m.settings.python
    assert m.version

    r = runez.run(package, "--version")
    assert r.succeeded

    cli.expect_success("--debug auto-upgrade %s" % package, "Skipping auto-upgrade, checked recently")
    cli.expect_success("install %s" % package, "is already installed")
    cli.expect_success("check", "is installed")
    cli.expect_success("list", package)
    cli.expect_success("upgrade", "is already up-to-date")

    if simulate_version:
        m.version = simulate_version
        runez.save_json(m.to_dict(), dot_meta("%s/.manifest.json" % package))
        cli.expect_success("check", "v%s installed, can be upgraded to" % simulate_version)


@GlobalHttpCalls.allowed
def test_install_pypi(cli):
    cli.expect_failure("--color install six", "it is not a CLI")
    assert not os.path.exists(dot_meta("six"))

    cli.expect_failure("install mgit+foo", "not a valid pypi package name")

    runez.touch(dot_meta("mgit/.foo"))  # Should stay because name starts with '.'
    runez.touch(dot_meta("mgit/mgit-foo"))  # Bogus installation
    runez.touch(dot_meta("mgit/mgit-0.0.1/foo"))  # Oldest should be deleted

    # Simulate the presence of an old entry point
    manifest_path = dot_meta("mgit/.manifest.json")
    runez.save_json(dict(entrypoints=["mgit", "old-mgit-entrypoint"]), manifest_path)
    runez.touch("old-mgit-entrypoint")
    assert os.path.exists("old-mgit-entrypoint")

    time.sleep(0.01)  # Ensure 0.0.1 is older than 0.0.2
    runez.touch(dot_meta("mgit/mgit-0.0.2/foo"))  # Youngest should remain for an hour
    check_install_from_pypi(cli, "symlink", "mgit")
    assert not os.path.exists("old-mgit-entrypoint")
    assert os.path.islink("mgit")
    assert os.path.exists(dot_meta("mgit/.manifest.json"))
    assert os.path.exists(dot_meta("mgit/.foo"))
    assert os.path.exists(dot_meta("mgit/mgit-0.0.2"))
    assert not os.path.exists(dot_meta("mgit/mgit-foo"))
    assert not os.path.exists(dot_meta("mgit/mgit-0.0.1"))

    cfg = PickleyConfig()
    cfg.set_base(".")
    pspec = PackageSpec(cfg, "mgit")
    pspec.groom_installation(keep_for=0)
    assert not os.path.exists(dot_meta("mgit/mgit-0.0.2"))

    cli.expect_success("uninstall mgit", "Uninstalled mgit")
    assert not runez.is_executable("mgit")
    assert not os.path.exists(dot_meta("mgit"))
    assert os.path.exists(dot_meta("audit.log"))

    check_install_from_pypi(cli, "wrap", "mgit", simulate_version="0.0.0")
    check_is_wrapper("mgit", True)


def test_lock(temp_cfg, logged):
    pspec = PackageSpec(temp_cfg, "foo")
    lock_path = dot_meta("foo.lock")
    with SoftLock(pspec, give_up=600) as lock:
        assert str(lock) == "lock foo"
        assert os.path.exists(lock_path)
        try:
            # Try to grab same lock a seconds time, give up after 1 second
            with SoftLock(pspec, give_up=1, invalid=600):
                assert False, "Should not grab same lock twice!"

        except SoftLockException as e:
            assert "giving up" in str(e)

    assert not os.path.exists(lock_path)  # Check that lock was released

    # Check that lock detects bogus (or dead) PID
    runez.write(lock_path, "0\nbar\n")
    with SoftLock(pspec, give_up=600):
        lines = list(runez.readlines(lock_path))
        assert lines[0] == str(os.getpid())  # Lock file replaced with correct stuff

    assert not os.path.exists(lock_path)  # Lock released


def test_main(cli):
    cli.exercise_main("-mpickley", "src/pickley/bstrap.py")


def test_package_pex(cli, monkeypatch):
    cli.run("--dryrun", "-ppex", "package", cli.project_folder)
    assert cli.succeeded
    assert cli.match("Using python: ... invoker", stdout=True)
    assert " -mpex " in cli.logged.stdout


def test_package_venv(cli):
    # Verify that "debian mode" works as expected, with -droot/tmp <-> /tmp
    runez.delete("/tmp/pickley")
    cli.run("package", cli.project_folder, "-droot/tmp", "--no-compile", "--sanity-check=--version", "-sroot:root/usr/local/bin")
    assert cli.succeeded
    assert "--version" in cli.logged
    assert runez.is_executable("/tmp/pickley/bin/pickley")
    r = runez.run("/tmp/pickley/bin/pickley", "--version")
    assert r.succeeded
    runez.delete("/tmp/pickley")


def test_version_check(cli):
    cli.run("version-check")
    assert cli.failed
    assert "Specify at least one program" in cli.logged

    cli.run("version-check", "python")
    assert cli.failed
    assert "Invalid argument" in cli.logged

    cli.run("--dryrun", "version-check", "python:1.0")
    assert cli.succeeded
    assert cli.match("Would run: .../python --version")

    cli.run("version-check", "--system", "python:1.0")
    assert cli.succeeded

    cli.run("version-check", "--system", "python:100.0")
    assert cli.failed
    assert "python version too low" in cli.logged

    with patch("runez.run", return_value=runez.program.RunResult(output="failed", code=1)):
        cli.run("version-check", "python:1.0")
        assert cli.failed
        assert "--version failed" in cli.logged

    with patch("runez.which", return_value=None):
        cli.run("version-check", "--system", "python:1.0")
        assert cli.failed
        assert "not installed" in cli.logged
