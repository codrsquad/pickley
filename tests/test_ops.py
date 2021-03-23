import os
import subprocess
import sys
import time

import pytest
import runez
from mock import patch

from pickley import __version__, get_program_path, PackageSpec, PickleyConfig, TrackedManifest
from pickley.cli import find_base, needs_bootstrap, PackageFinalizer, protected_main, SoftLock, SoftLockException
from pickley.delivery import WRAPPER_MARK
from pickley.package import download_command, Packager

from .conftest import dot_meta


def test_base(temp_folder):
    with patch.dict(os.environ, {"PICKLEY_ROOT": "temp-base"}, clear=True):
        with pytest.raises(SystemExit):  # Env var points to a non-existing folder
            find_base()

        runez.ensure_folder("temp-base")
        assert find_base() == runez.resolved_path("temp-base")

    assert sys.prefix in get_program_path("foo/bar.py")

    original = PickleyConfig.program_path
    PickleyConfig.program_path = "/foo/.venv/bin/pickley"
    assert find_base() == "/foo/.venv/root"

    PickleyConfig.program_path = dot_meta("pickley-0.0.0/bin/pickley", parent="foo")
    assert find_base() == "foo"

    PickleyConfig.program_path = "foo/bar"
    assert find_base() == "foo"

    PickleyConfig.program_path = original


def test_bootstrap(temp_cfg):
    assert needs_bootstrap() is False

    pspec = PackageSpec(temp_cfg, "pickley", "0.0")
    pspec.python = temp_cfg.available_pythons.invoker
    assert needs_bootstrap(pspec) is True  # Due to no manifest

    pspec.python.spec.version.components = (pspec.python.major + 1, 0, 0)
    assert needs_bootstrap(pspec) is True  # Due to higher version of python available

    with patch("runez.which", return_value="curl"):
        assert "curl" == download_command("", "")[0]

    with patch("runez.which", return_value=None):
        assert "wget" == download_command("", "")[0]


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


def test_dryrun(cli):
    with patch("pickley.cli.needs_bootstrap", return_value=False):
        cli.run("-n -Pinvoker auto-upgrade")
        assert cli.succeeded
        assert not cli.logged

    with patch("pickley.cli._location_grand_parent", return_value=".pex/pickley.whl"):
        cli.run("-n auto-upgrade")
        assert cli.failed
        assert "Internal error" in cli.logged
        runez.touch("pickley")  # Simulate a wheel present for pex-bootstrap case
        cli.run("-n auto-upgrade")
        assert cli.succeeded
        assert "Bootstrapping pickley" in cli.logged

    cli.run("-n auto-upgrade")
    assert cli.succeeded
    assert ".ping" not in cli.logged
    assert "Pass 1 bootstrap done" in cli.logged
    if sys.version_info[0] < 3:
        assert "pickley.bootstrap" in cli.logged

    cli.run("-n auto-upgrade", exe="pickley.bootstrap/bin/pickley")
    assert cli.succeeded
    assert "Pass 2 bootstrap done" in cli.logged
    assert ".ping" not in cli.logged

    if sys.version_info[0] > 2:
        cli.expect_success("-n --debug auto-upgrade mgit", "Would wrap mgit")
        runez.touch(dot_meta("mgit.lock"))
        cli.expect_success("-n --debug auto-upgrade mgit", "Lock file present, another installation is in progress")

    with patch.dict(os.environ, {"__PYVENV_LAUNCHER__": "foo"}):
        folder = os.getcwd()
        cli.expect_success("-n base", folder)
        cli.expect_success("-n base audit", dot_meta("audit.log", parent=folder))
        cli.expect_success("-n base cache", dot_meta(".cache", parent=folder))
        cli.expect_success("-n base meta", dot_meta(parent=folder))
        cli.expect_failure("-n base foo", "Unknown base folder reference")

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

    cli.expect_failure("-n -Pfoo install mgit", "No suitable python")

    # Simulate an old entry point that was now removed
    runez.write(dot_meta("mgit/.manifest.json"), '{"entrypoints": ["bogus-mgit"]}')
    cli.expect_failure("-n install mgit pickley2.a", "Would state: Installed mgit v", "'pickley2.a' is not pypi canonical")
    runez.delete(dot_meta("mgit"))

    cli.expect_success("-n diagnostics -v", "sys.executable")
    with patch("runez.run", return_value=runez.program.RunResult("failed")):
        cli.run("-n install git@github.com:zsimic/mgit.git")
        assert cli.failed
        assert cli.match("No setup.py")

    with patch("pickley.git_clone", side_effect=mock_git_clone):
        cli.run("-n install git@github.com:zsimic/mgit.git")
        assert cli.succeeded
        assert cli.match("Would run: ... -mpip ... install .pickley/.cache/checkout/mgit")

    cli.run("-n install mgit")
    assert cli.succeeded
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

    cli.expect_success(["-n", "package", cli.project_folder], "Would run: ... -mpip ... install ...requirements.txt")

    cli.expect_failure("-n uninstall", "Specify packages to uninstall, or --all")
    cli.expect_failure("-n uninstall pickley", "Run 'uninstall --all' if you wish to uninstall pickley itself")
    cli.expect_failure("-n uninstall mgit", "mgit was not installed with pickley")
    cli.expect_failure("-n uninstall mgit --all", "Either specify packages to uninstall, or --all (but not both)")
    cli.expect_success("-n uninstall --all", "pickley is now uninstalled")

    cli.expect_success("-n upgrade", "No packages installed, nothing to upgrade")
    cli.expect_failure("-n upgrade mgit", "'mgit' is not installed")

    # Simulate old pickley v1 install
    cli.expect_success("-n list", "No packages installed")
    runez.write(dot_meta("mgit/.current.json"), '{"version": "0.0.1"}')
    runez.write(dot_meta("mgit/.entry-points.json"), '{"mgit": "mgit.cli:main"}')
    cli.expect_success("-n upgrade mgit", "Would state: Upgraded mgit")
    cli.expect_success("-n list", "mgit")


def test_edge_cases(temp_cfg, logged):
    import pickley.__main__  # noqa, just verify it imports

    mgit = PackageSpec(temp_cfg, "mgit")
    assert mgit.find_wheel(".", fatal=False) is None
    assert "Expecting 1 wheel" in logged.pop()

    runez.touch("mgit-1.0.0.whl")
    w = mgit.find_wheel(".", fatal=False)
    assert w == "./mgit-1.0.0.whl"

    # Exercise protected_main()
    with patch("pickley.cli.main", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit):
            protected_main()
    assert "Aborted" in logged.pop()

    with patch("pickley.cli.main", side_effect=SoftLockException("mocked lock")):
        with pytest.raises(SystemExit):
            protected_main()
    assert "mocked lock" in logged

    with patch("pickley.cli.main", side_effect=NotImplementedError("packager is not supported")):
        with pytest.raises(SystemExit):
            protected_main()
    assert "packager is not supported" in logged

    with pytest.raises(NotImplementedError):
        Packager.package(None, None, None, None, False)


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
    runez.delete("virtualenv")
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


def test_install_folder(cli):
    """Check that flip-flopping between symlink/wrapper works"""
    project = runez.log.project_path()
    cli.run("--debug", "-dsymlink", "install", project)
    assert cli.succeeded
    check_is_wrapper("pickley", False)

    cli.run("--debug", "-dwrap", "install", "--force", project)
    assert cli.succeeded
    check_is_wrapper("pickley", True)

    cli.run("--debug", "-dsymlink", "install", "--force", project)
    assert cli.succeeded
    check_is_wrapper("pickley", False)


def check_install_from_pypi(cli, delivery, package, simulate_version=None):
    cli.run("--debug", "-d%s" % delivery, "install", package)
    assert cli.succeeded
    assert cli.match("Installed %s" % package)
    assert runez.is_executable(package)
    m = TrackedManifest.from_file(dot_meta("%s/.manifest.json" % package))
    assert m.entrypoints[package]
    assert m.install_info.args == runez.quoted(cli.args)
    assert m.install_info.timestamp
    assert m.install_info.vpickley == __version__
    assert m.settings.delivery == delivery
    assert m.settings.python
    assert m.version

    r = runez.run(package, "--version")
    assert r.succeeded

    if sys.version_info[0] > 2:
        # Bootstrapping out of py2 is tested separately
        cli.expect_success("--debug auto-upgrade %s" % package, "Skipping auto-upgrade, checked recently")

    cli.expect_success("install %s" % package, "is already installed")
    cli.expect_success("check", "is installed")
    cli.expect_success("list", package)
    cli.expect_success("upgrade", "is already up-to-date")

    if simulate_version:
        m.version = simulate_version
        runez.save_json(m.to_dict(), dot_meta("%s/.manifest.json" % package))
        cli.expect_success("check", "v%s installed, can be upgraded to" % simulate_version)


def test_install_pypi(cli):
    cli.expect_failure("install six", "it is not a CLI")
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

    if sys.version_info[0] > 2:
        # Bootstrapping out of py2 is tested separately
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
        lines = runez.readlines(lock_path)
        assert lines[0] == str(os.getpid())  # Lock file replaced with correct stuff

    assert not os.path.exists(lock_path)  # Lock released


def test_main():
    r = subprocess.check_output([sys.executable, "-mpickley", "--help"])  # Exercise __main__.py
    r = runez.decode(r)
    assert "auto-upgrade" in r


def test_package_pex(cli):
    cli.run("--dryrun", "-ppex", "-Pinvoker", "package", cli.project_folder)
    if runez.PY2:
        assert cli.failed
        assert "not supported any more with python2" in cli.logged
        return

    assert cli.succeeded
    assert "mpex" in cli.logged.stdout
    contents = runez.readlines(runez.log.project_path("requirements.txt"))
    if any(s.startswith("-e") for s in contents):
        return  # Skip actual pex test if we're running with '-e ...path...' in requirements.txt

    with patch.dict(os.environ, {"PEX_ROOT": os.path.join(os.getcwd(), ".pex")}):
        expected = "dist/pickley"
        cli.run("-ppex", "-Pinvoker", "package", cli.project_folder)
        assert cli.succeeded
        assert runez.is_executable(expected)

        r = runez.run(expected, "--version")
        version = r.output
        assert r.succeeded

        assert runez.run(expected, "diagnostics").succeeded

        r = runez.run(expected, "--debug", "auto-upgrade")
        assert r.succeeded
        assert "Bootstrapping pickley" in r.full_output

        assert runez.run(expected, "diagnostics").succeeded

        manifest = TrackedManifest.from_file(dot_meta("pickley/.manifest.json"))
        assert manifest.version == version


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
