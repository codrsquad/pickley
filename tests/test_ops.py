import os
import time
from unittest.mock import patch

import pytest
import runez
from runez.http import GlobalHttpCalls
from runez.pyenv import Version

from pickley import __version__, PackageSpec, PICKLEY, PickleyConfig, TrackedManifest, TrackedVersion
from pickley.cli import clean_compiled_artifacts, find_base, PackageFinalizer, Requirements, SoftLock, SoftLockException
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

    monkeypatch.delenv("PICKLEY_ROOT")
    assert find_base("/foo/.venv/bin/pickley") == "/foo/.venv/root"
    assert find_base(dot_meta("pickley-0.0.0/bin/pickley", parent="foo")) == "foo"
    assert find_base("foo/bar") == "foo"


def dummy_finalizer(cfg, dist, symlink="root:root/usr/local/bin"):
    p = PackageFinalizer("foo", dist, symlink, None, None, cfg=cfg)
    p.resolve()
    assert p.pspec.dashed == "foo"
    return p


def test_debian_mode(temp_cfg, logged):
    runez.write("foo/setup.py", "import setuptools\nsetuptools.setup(name='foo', version='1.0')")
    p = dummy_finalizer(temp_cfg, "root/apps")
    assert p.dist == "root/apps/foo"
    assert p.requirements == Requirements(requirement_files=[], additional_packages=None, project=runez.resolved_path("foo"))
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
        p = dummy_finalizer(temp_cfg, "root/apps")
        assert "debian mode" in logged.pop()
        assert p.dist == "/apps/foo"

    with patch("runez.run", return_value=runez.program.RunResult("usage: ...")):
        assert p.validate_sanity_check("foo", "--version") == "does not respond to --version"

    with patch("runez.run", return_value=runez.program.RunResult("failed")):
        with pytest.raises(SystemExit):
            p.validate_sanity_check("foo", "--version")

        assert "'foo' failed --version sanity check" in logged.pop()


def mock_latest_pypi_version(package_name, **_):
    if package_name in ("mgit", "pickley", "virtualenv"):
        return Version("100.0")


def test_dryrun(cli, monkeypatch):
    cli.run("-n config")
    assert cli.succeeded
    assert not cli.logged.stderr
    assert "cli:  # empty" in cli.logged.stdout
    assert "defaults:" in cli.logged.stdout

    cli.run("-n --color config")
    assert cli.succeeded

    cli.expect_success("-n auto-heal", "Auto-healed 0 / 0 packages")

    cli.expect_success("-n -Pfoo diagnostics", "desired python : foo", "foo [not available]", "sys.executable")
    cli.run("-n install git@github.com:zsimic/mgit.git")
    assert cli.succeeded
    checkout = dot_meta(".cache/checkout")
    assert f"pip install {checkout}/git-github-com-zsimic-mgit-git" in cli.logged

    cli.expect_success("-n list", "No packages installed")

    cli.expect_failure("-n package foo", "Folder ... does not exist")
    cli.expect_failure("-n package . -sfoo", "Invalid symlink specification")
    cli.expect_failure("-n package . -sroot:root/usr/local/bin", "No setup.py in ")

    runez.touch("setup.py")
    cli.expect_failure("-n package .", "Could not determine package name")
    runez.write("setup.py", "import sys\nfrom setuptools import setup\nif sys.argv[1]=='--version': sys.exit(1)\nsetup(name='foo')")
    cli.expect_failure("-n package .", "Could not determine package version")

    cli.run("-n", "package", cli.project_folder)
    assert cli.succeeded
    cli.match("Would run: ...pip...install -r requirements.txt")

    cli.expect_failure("-n uninstall", "Specify packages to uninstall, or --all")
    cli.expect_failure("-n uninstall pickley", "Run 'uninstall --all' if you wish to uninstall pickley itself")
    cli.expect_failure("-n uninstall mgit", "mgit was not installed with pickley")
    cli.expect_failure("-n uninstall mgit --all", "Either specify packages to uninstall, or --all (but not both)")
    cli.expect_success("-n uninstall --all", "pickley is now uninstalled")

    cli.expect_success("-n upgrade", "No packages installed, nothing to upgrade")
    cli.expect_failure("-n upgrade mgit", "'mgit' is not installed")

    with patch("runez.pyenv.PypiStd.latest_pypi_version", side_effect=mock_latest_pypi_version):
        cli.expect_failure("-n -Pfoo install bundle:bar", "No suitable python")

        cli.run("-n --debug auto-upgrade mgit")
        assert cli.succeeded
        assert "pip install mgit==100.0" in cli.logged
        assert "Would wrap mgit" in cli.logged
        runez.touch(dot_meta("mgit.lock"))
        cli.run("-n --debug auto-upgrade mgit")
        assert cli.succeeded
        assert "Lock file present, another installation is in progress" in cli.logged

        cli.expect_success("-n check", "No packages installed")
        cli.expect_failure("-n check foo+bar", "'foo+bar' is not a valid pypi package name")
        cli.expect_failure("-n check mgit pickley2-a", "not installed", "pickley2-a: does not exist")

        # Simulate an old entry point that was now removed
        runez.write(dot_meta("mgit.manifest.json"), '{"entrypoints": ["bogus-mgit"]}')
        cli.expect_failure("-n install mgit pickley2.a", "Would state: Installed mgit v", "'pickley2.a' is not pypi canonical")

        cli.expect_failure("check", "not installed")
        cli.expect_success("list", "mgit")
        cli.expect_success("list -fcsv", "mgit")
        cli.expect_success("list -fjson", "mgit")
        cli.expect_success("list -ftsv", "mgit")
        cli.expect_success("list -fyaml", "mgit")
        runez.delete(dot_meta("mgit"))

        cli.run("-n --virtualenv latest -Pinvoker install --no-binary :all: mgit==1.3.0")
        assert cli.succeeded
        assert " --no-binary :all: mgit==1.3.0" in cli.logged
        assert cli.match("Would wrap mgit -> %s" % dot_meta("mgit"))
        assert cli.match("Would save %s" % dot_meta("mgit.manifest.json"))
        assert cli.match("Would state: Installed mgit v1.3.0")

        cli.expect_failure("-n -dfoo install mgit", "Unknown delivery method 'foo'")


def test_dev_mode(cli, monkeypatch):
    with patch("runez.pyenv.PypiStd.latest_pypi_version", side_effect=mock_latest_pypi_version):
        cli.run("-n install pickley")
        assert cli.succeeded
        assert "Would run: .pk/pickley-100.0/bin/pip install -e " in cli.logged
        assert "Would wrap pickley -> .pk/pickley-100.0/bin/pickley" in cli.logged
        assert "Would state: Installed pickley v100.0 in "


def test_edge_cases(temp_cfg, logged):
    tv = TrackedVersion(version="1.2")
    assert str(tv) == "1.2"
    pspec = PackageSpec(temp_cfg, PICKLEY)

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


def test_facultative(cli):
    runez.save_json({"pinned": {"virtualenv": {"facultative": True}}}, dot_meta("config.json"))

    # Empty file -> proceed with install as if it wasn't there
    runez.touch("virtualenv")
    cli.expect_success("-n install virtualenv==1.0", "Would state: Installed virtualenv")

    # Simulate pickley wrapper
    runez.write("virtualenv", "echo installed by pickley")
    runez.make_executable("virtualenv")
    cli.expect_success("-n install virtualenv==1.0", "Would state: Installed virtualenv")

    # Unknown executable -> skip pickley installation (since facultative)
    runez.write("virtualenv", "echo foo")
    runez.make_executable("virtualenv")
    cli.expect_success("-n install virtualenv", "Skipping installation of virtualenv: not installed by pickley")
    cli.expect_success("-n check virtualenv", "skipped, not installed by pickley")

    # --force ignores 'facultative' setting
    with patch("runez.pyenv.PypiStd.latest_pypi_version", side_effect=mock_latest_pypi_version):
        cli.run("-n install --force virtualenv")
        assert cli.failed
        assert "virtualenv exists and was not installed by pickley" in cli.logged

        # Simulate pickley symlink delivery
        dummy_target = dot_meta("foo")
        runez.touch(dummy_target)
        runez.symlink(dummy_target, "virtualenv")
        cli.run("-n install virtualenv")
        assert cli.succeeded
        assert "Would state: Installed virtualenv" in cli.logged


def test_failure_cases(cli):
    cli.run("-n --packager pex install mgit==1.0")
    assert cli.failed
    assert "Installation with 'PexPackager' is not supported" in cli.logged

    with patch("runez.run", side_effect=Exception):
        cli.run("install git@github.com:zsimic/mgit.git")
        assert cli.failed


def check_is_wrapper(path, is_wrapper):
    if is_wrapper:
        assert not os.path.islink(path)
        contents = runez.readlines(path)
        assert WRAPPER_MARK in contents

    r = runez.run(path, "--version")
    assert r.succeeded


def check_install_from_pypi(cli, delivery, package, version, simulate_version=None):
    runez.write(".pk/.cache/mgit.latest", f'{{"version": "{version}"}}')
    cli.run("--debug", f"-d{delivery}", "install", package)
    assert cli.succeeded
    assert cli.match(f"Installed {package} v{version}")
    assert runez.is_executable(package)
    m = TrackedManifest.from_file(dot_meta(f"{package}.manifest.json"))
    assert str(m)
    assert m.entrypoints[package]
    assert m.install_info.args == runez.quoted(cli.args)
    assert m.install_info.timestamp
    assert m.install_info.vpickley == __version__
    assert m.settings.delivery == delivery
    assert m.settings.python
    assert m.version == version

    r = runez.run(f"./{package}", "--version")
    assert r.succeeded
    assert version in r.full_output

    cli.expect_success(f"--debug auto-upgrade {package}", "Skipping auto-upgrade, checked recently")
    cli.expect_success(f"install {package}", "is already installed")
    if simulate_version:
        # Edge case: simulated user manually deletes the installed wrapper or symlink
        assert os.path.exists(package)
        os.unlink(package)
        cli.run("--debug", f"-d{delivery}", "install", package)
        assert cli.succeeded
        assert cli.match(f"Installed {package} v{version}")

    cli.expect_success("check", " up-to-date")
    cli.expect_success("list", package)
    cli.expect_success("upgrade", "is already up-to-date")

    if simulate_version:
        installed_version = m.version
        m.version = simulate_version
        runez.save_json(m.to_dict(), dot_meta(f"{package}.manifest.json"))
        cli.expect_success("check", f"{installed_version} (currently {simulate_version})")


def test_install_pypi(cli):
    runez.touch(dot_meta("mgit-0.0.1/pyenv.cfg"))
    time.sleep(0.01)  # Ensure 0.0.1 is older than 0.0.2
    runez.touch(dot_meta("mgit-0.0.2/pyenv.cfg"))

    # Simulate the presence of an old entry point
    manifest_path = dot_meta("mgit.manifest.json")
    runez.save_json(dict(entrypoints=["mgit", "old-mgit-entrypoint"]), manifest_path)
    runez.touch("old-mgit-entrypoint")

    check_install_from_pypi(cli, "symlink", "mgit", "1.3.0")
    assert not os.path.exists("old-mgit-entrypoint")
    assert os.path.islink("mgit")
    assert os.path.exists(dot_meta("mgit.manifest.json"))
    assert not os.path.exists(dot_meta("mgit-0.0.1"))
    assert os.path.exists(dot_meta("mgit-0.0.2"))
    assert os.path.exists(dot_meta("mgit-1.3.0"))

    cli.run("-n auto-heal")
    assert cli.succeeded
    assert "mgit is healthy" in cli.logged
    assert "Auto-healed 0 / 1 packages" in cli.logged

    cfg = PickleyConfig()
    cfg.set_base(".")
    pspec = PackageSpec(cfg, "mgit==1.3.0")
    pspec.groom_installation(keep_for=0)
    assert not os.path.exists(dot_meta("mgit-0.0.2"))
    assert os.path.exists(dot_meta("mgit-1.3.0"))

    cli.expect_success("uninstall mgit", "Uninstalled mgit")
    assert not runez.is_executable("mgit")
    assert not os.path.exists(dot_meta("mgit.manifest.json"))
    assert not os.path.exists(dot_meta("mgit-1.3.0"))
    assert os.path.exists(dot_meta("audit.log"))

    check_install_from_pypi(cli, "wrap", "mgit", "1.3.0", simulate_version="0.0.0")
    check_is_wrapper("./mgit", True)

    runez.delete(dot_meta("mgit-1.3.0"))
    cli.run("-n auto-heal")
    assert cli.succeeded
    assert "Auto-healed 1 / 1 packages" in cli.logged


@GlobalHttpCalls.allowed
def test_invalid(cli):
    cli.run("--color install six")
    assert cli.failed
    assert "not a CLI" in cli.logged
    assert not os.path.exists(dot_meta("six.manifest.json"))

    cli.expect_failure("install mgit+foo")
    assert cli.failed
    assert "not a valid pypi package name" in cli.logged


def test_lock(temp_cfg, logged):
    pspec = PackageSpec(temp_cfg, "foo")
    lock_path = dot_meta("foo.lock")
    with SoftLock(pspec, give_up=600) as lock:
        assert str(lock) == "lock foo"
        assert os.path.exists(lock_path)
        with pytest.raises(SoftLockException) as e:
            # Try to grab same lock a seconds time, give up after 1 second
            with SoftLock(pspec, give_up=1, invalid=600):
                pass

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


def test_package_venv_with_additional_packages(cli):
    runez.delete("/tmp/pickley")
    cli.run("package", "-droot/tmp", "-sroot:root/usr/local/bin", cli.project_folder, "litecli")
    assert cli.succeeded
    assert "pip install -U pip" in cli.logged
    assert "pip install -r requirements.txt" in cli.logged
    assert "pip install litecli" in cli.logged
    assert runez.is_executable("/tmp/pickley/bin/pickley")
    assert runez.is_executable("/tmp/pickley/bin/litecli")
    r = runez.run("/tmp/pickley/bin/pickley", "--version")
    assert r.succeeded
    r = runez.run("/tmp/pickley/bin/litecli", "--version")
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
