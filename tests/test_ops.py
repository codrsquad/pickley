import os
import sys
import time

import pytest
import runez
from mock import patch
from runez.conftest import project_folder

from pickley import PackageSpec, PickleyConfig
from pickley.cli import find_base, PackageFinalizer, protected_main, SoftLock, SoftLockException
from pickley.delivery import WRAPPER_MARK
from pickley.package import Packager


def test_base(temp_folder):
    expected_base = runez.resolved_path("temp-base")
    with patch.dict(os.environ, {"PICKLEY_ROOT": "temp-base"}, clear=True):
        with pytest.raises(SystemExit):  # Env var points to a non-existing folder
            find_base()

        runez.ensure_folder("temp-base")
        assert find_base() == expected_base

    with runez.TempArgv([], exe="temp-base/pickley"):
        assert find_base() == expected_base

    with runez.TempArgv([], exe="temp-base/.pickley/pickley"):
        assert find_base() == expected_base

    with runez.TempArgv([], exe=".venv/bin/pickley"):
        assert find_base() == runez.resolved_path(".venv/root")


def dummy_finalizer(dist, symlink="root:root/usr/local/bin"):
    p = PackageFinalizer(".", "build", dist, symlink, None, None)
    p.package_name = "foo"
    return p


def test_debian_mode(temp_folder, logged):
    p = dummy_finalizer("root/apps")
    p.resolve_dist()
    assert p.dist == "root/apps/foo"
    assert p.root == "root"
    assert p.requirements == ["."]
    assert not logged

    foo = runez.resolved_path("root/foo")
    runez.touch(foo)
    logged.pop()

    # Symlink not created unless target effectively exists
    p.symlink.apply(foo, p.root)
    assert not logged
    assert not os.path.isdir("root/usr/local/bin")

    # Simulate target exists
    p.symlink.must_exist = False
    p.symlink.apply(foo, p.root)
    assert "Symlinked root/usr/local/bin/foo -> /foo" in logged.pop()
    assert os.path.isdir("root/usr/local/bin")
    assert os.path.islink("root/usr/local/bin/foo")

    p = dummy_finalizer("root/apps")
    with patch("os.path.isdir", return_value=True):  # pretend /apps exists
        p.resolve_dist()

    assert "debian mode" in logged.pop()
    assert p.dist == "/apps/foo"
    assert p.root == "root"

    with patch("runez.run", return_value=runez.program.RunResult("usage: ...")):
        assert p.validate_sanity_check("foo", "--version") == "does not respond to --version"

    with patch("runez.run", return_value=runez.program.RunResult("failed")):
        with pytest.raises(SystemExit):
            p.validate_sanity_check("foo", "--version")

        assert "'foo' failed sanity check" in logged.pop()


def test_dryrun(cli):
    cli.expect_success("--help", "Usage:")

    cli.expect_success("-n auto-upgrade", "Would save")
    cli.expect_success("-n --debug auto-upgrade mgit", "Would wrap mgit")
    runez.touch(".pickley/mgit.lock")
    cli.expect_success("-n --debug auto-upgrade mgit", "Lock file present, another installation is in progress")

    cli.expect_success("-n base", os.getcwd())
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

    cli.expect_failure("-n -Pfoo install mgit", "Python 'foo' is not usable: not available")

    # Simulate an old entry point that was now removed
    runez.write(".pickley/mgit/.manifest.json", '{"entrypoints": ["bogus-mgit"]}')
    cli.expect_failure("-n install mgit pickley2.a", "Would state: Installed mgit v", "'pickley2.a' is not pypi canonical")
    runez.delete(".pickley/mgit")

    cli.expect_success("-n diagnostics -v", "sys.executable")
    cli.run("-n install mgit")
    assert cli.succeeded
    assert cli.match("Would wrap mgit -> .pickley/mgit/")
    assert cli.match("Would save .pickley/mgit/.manifest.json")
    assert cli.match("Would state: Installed mgit v")

    cli.expect_failure("-n -dfoo install mgit", "Unknown delivery method 'foo'")

    cli.expect_success("-n list", "No packages installed")

    cli.expect_failure("-n package foo", "Folder ... does not exist")
    cli.expect_failure("-n package . --no-sanity-check -sfoo", "Invalid symlink specification")
    cli.expect_failure("-n package . -sroot:root/usr/local/bin", "No setup.py in ")

    runez.touch("setup.py")
    cli.expect_failure("-n package .", "Could not determine package name")
    runez.write("setup.py", "import sys\nfrom setuptools import setup\nif sys.argv[1]=='--version': sys.exit(1)\nsetup(name='foo')")
    cli.expect_failure("-n package .", "Could not determine package version")

    cli.expect_success(["-n", "package", project_folder()], "Would run: ... -mpip ... install ...requirements.txt")

    cli.expect_failure("-n uninstall", "Specify packages to uninstall, or --all")
    cli.expect_failure("-n uninstall pickley", "Run 'uninstall --all' if you wish to uninstall pickley itself")
    cli.expect_failure("-n uninstall mgit", "mgit was not installed with pickley")
    cli.expect_failure("-n uninstall mgit --all", "Either specify packages to uninstall, or --all (but not both)")
    cli.expect_success("-n uninstall --all", "pickley is now uninstalled")

    cli.expect_success("-n upgrade", "No packages installed, nothing to upgrade")
    cli.expect_failure("-n upgrade mgit", "'mgit' is not installed")

    # Simulate old pickley v1 install
    cli.expect_success("-n list", "No packages installed")
    runez.write(".pickley/mgit/.current.json", '{"version": "0.0.1"}')
    runez.write(".pickley/mgit/.entry-points.json", '{"mgit": "mgit.cli:main"}')
    cli.expect_success("-n upgrade mgit", "Would state: Upgraded mgit")
    cli.expect_success("-n list", "mgit")


def test_edge_cases(temp_folder, logged):
    import pickley.__main__  # noqa, just verify it imports

    # Exercise protected_main()
    with patch("pickley.cli.main", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit):
            protected_main()
    assert "Aborted" in logged.pop()

    with patch("pickley.cli.main", side_effect=SoftLockException("mocked lock")):
        with pytest.raises(SystemExit):
            protected_main()
    assert "mocked lock" in logged

    with patch("pickley.cli.main", side_effect=NotImplementedError("{packager} is not supported")):
        with pytest.raises(SystemExit):
            protected_main()
    assert "venv is not supported" in logged

    with pytest.raises(NotImplementedError):
        Packager.install(None)

    with pytest.raises(NotImplementedError):
        Packager.package(None, None, None, None)


def test_lock(temp_folder):
    with SoftLock("foo", 600, 600) as lock:
        assert str(lock) == "foo"
        assert os.path.exists("foo")
        try:
            # Try to grab same lock a seconds time, give up after 1 second
            with SoftLock("foo", 1, 600):
                assert False, "Should not grab same lock twice!"

        except SoftLockException as e:
            assert "giving up" in str(e)

    assert not os.path.exists("foo")  # Check that lock was released

    # Check that lock detects bogus (or dead) PID
    runez.write("foo", "0\nbar\n")
    with SoftLock("foo", 600, 600):
        lines = runez.readlines("foo")
        assert lines[0] == str(os.getpid())  # File "foo" replaced with correct stuff

    assert not os.path.exists("foo")  # Lock released


def check_install(cli, delivery, package, simulate_version=None):
    cli.expect_success("-d%s install %s" % (delivery, package), "Installed %s" % package)
    assert runez.is_executable(package)
    m = runez.read_json(".pickley/%s/.manifest.json" % package)
    assert m["settings"]
    assert package in m["entrypoints"]
    assert "command" in m["pickley"]
    assert m["version"]

    r = runez.run(package, "--version")
    assert r.succeeded

    cli.expect_success("--debug auto-upgrade %s" % package, "Skipping auto-upgrade, checked recently")
    cli.expect_success("install %s" % package, "is already installed")
    cli.expect_success("check", "is installed")
    cli.expect_success("list", package)
    cli.expect_success("upgrade", "is already up-to-date")

    if simulate_version:
        m["version"] = simulate_version
        runez.save_json(m, ".pickley/%s/.manifest.json" % package)
        cli.expect_success("check", "v%s installed, can be upgraded to" % simulate_version)


@pytest.mark.skipif(sys.version_info[:2] not in ((2, 7), (3, 7)), reason="Functional test")
def test_installation(cli):
    cli.expect_failure("install six", "it is not a CLI")
    assert not os.path.exists(".pickley/six")

    cli.expect_failure("install mgit+foo", "not a valid pypi package name")

    runez.touch(".pickley/mgit/.foo")  # Should stay because name starts with '.'
    runez.touch(".pickley/mgit/mgit-foo")  # Bogus installation
    runez.touch(".pickley/mgit/mgit-0.0.1/foo")  # Oldest should be deleted
    time.sleep(0.01)  # Ensure 0.0.1 is older than 0.0.2
    runez.touch(".pickley/mgit/mgit-0.0.2/foo")  # Youngest should remain for an hour
    check_install(cli, "symlink", "mgit")
    assert os.path.islink("mgit")
    assert os.path.exists(".pickley/mgit/.manifest.json")
    assert os.path.exists(".pickley/mgit/.foo")
    assert os.path.exists(".pickley/mgit/mgit-0.0.2")
    assert not os.path.exists(".pickley/mgit/mgit-foo")
    assert not os.path.exists(".pickley/mgit/mgit-0.0.1")

    cfg = PickleyConfig()
    cfg.set_base(".")
    pspec = PackageSpec(cfg, "mgit")
    pspec.groom_installation(keep_for=0)
    assert not os.path.exists(".pickley/mgit/mgit-0.0.2")

    cli.expect_success("uninstall mgit", "Uninstalled mgit")
    assert not runez.is_executable("mgit")
    assert not os.path.exists(".pickley/mgit")
    assert os.path.exists(".pickley/audit.log")

    check_install(cli, "wrap", "mgit", simulate_version="0.0.0")
    assert not os.path.islink("mgit")
    contents = runez.readlines("mgit")
    assert WRAPPER_MARK in contents


@pytest.mark.skipif(sys.version_info[:2] != (3, 7), reason="Long test, testing with most common python version only")
def test_package_pex(cli):
    expected = "dist/pickley"
    cli.run("-ppex", "package", project_folder())
    assert cli.succeeded
    assert "--version" in cli.logged
    assert runez.is_executable(expected)
    r = runez.run(expected, "--version")
    assert r.succeeded


@pytest.mark.skipif(sys.version_info[:2] not in ((2, 7), (3, 7)), reason="Functional test")
def test_package_venv(cli):
    expected = "root/apps/pickley/bin/pickley"
    # Using --no-sanity-check and -s for code coverage
    cli.run("package", project_folder(), "-droot/apps", "--no-sanity-check", "-sroot:root/usr/local/bin")
    assert cli.succeeded
    assert runez.is_executable(expected)
    r = runez.run(expected, "--version")
    assert r.succeeded
