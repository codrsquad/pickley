import os
import time
from unittest.mock import patch

import pytest
import runez
from runez.pyenv import Version, PypiStd

from pickley import __version__, bstrap, PackageSpec, PickleyConfig, TrackedManifest
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

    runez.ensure_folder("temp-base", logger=None)
    assert find_base() == runez.resolved_path("temp-base")

    monkeypatch.delenv("PICKLEY_ROOT")
    assert find_base("/foo/.venv/bin/pickley") == "/foo/.venv/root"
    assert find_base(dot_meta("pickley-0.0.0/bin/pickley", parent="foo")) == "foo"
    assert find_base("foo/bar") == "foo"


def dummy_finalizer(dist, symlink="root:root/usr/local/bin"):
    p = PackageFinalizer("foo", dist, symlink, None, None)
    p.resolve()
    assert p.pspec.canonical_name == "foo"
    return p


def test_debian_mode(temp_cfg, logged):
    runez.write("foo/setup.py", "import setuptools\nsetuptools.setup(name='foo', version='1.0')", logger=None)
    p = dummy_finalizer("root/apps")
    assert p.dist == "root/apps/foo"
    assert p.requirements == Requirements(requirement_files=[], additional_packages=None, project=runez.resolved_path("foo"))
    assert "Using python:" in logged.pop()

    # Symlink not created unless source effectively exists
    p.symlink.apply("root/foo")
    assert "skipping symlink" in logged.pop()
    assert not os.path.isdir("root/usr/local/bin")

    foo = runez.resolved_path("root/foo")
    runez.touch(foo, logger=None)
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


class MockRunner:
    """Intercept calls to `runez.run()` to simulate package resolution"""
    last_project_ref = None
    last_name = None
    last_version = None

    def reset(self):
        self.last_project_ref = None
        self.last_name = None
        self.last_version = None

    def run(self, program, *args, logger=runez.UNSET, dryrun=runez.UNSET, **popen_args):
        args = runez.flattened(args, shellify=True)
        audit = runez.program.RunAudit(program, args, popen_args)
        r = runez.program.RunResult(code=0, audit=audit)
        description = audit.run_description()
        if runez.log.hdry("run: %s" % description, dryrun=dryrun, logger=logger):
            audit.dryrun = True
            r.output = "[dryrun] %s" % description  # Properly simulate a successful run
            return r

        print("Running: %s" % description)
        if "venv" in args or "-mvenv" in args:
            self.reset()
            return r

        if "install" in args:
            what = args[-1]
            if what == "pickley2.a":
                r.exit_code = 1
                r.error = "line 1\nline 2\nline 3\nline 4\nline 5"
                return r

            if "==" in what:
                self.last_name, _, self.last_version = what.rpartition("==")
                return r

            if what.startswith("/"):
                self.last_project_ref = what
                self.last_name = os.path.basename(what)
                if self.last_name == "pickley":
                    self.last_version = __version__

                return r

            if PypiStd.std_package_name(what) == what:
                self.last_name = what
                self.last_version = "102.0"
                return r

            if what.startswith("git+"):
                self.last_name = os.path.basename(what).replace(".git", "")
                self.last_version = "103.0"
                return r

            self.last_name = what
            self.last_version = "100.0"
            return r

        if "freeze" in args:
            if not self.last_version:
                r.output = "oops\npip freeze failed!\n"

            else:
                r.output = str(self)

            return r

        if "show" in args:
            r.output = f"Name: {self.last_name}\nLocation: ...\n"
            if self.last_version:
                r.output += f"Version: {self.last_version}\n"

            return r

        return r

    def __repr__(self):
        if self.last_project_ref:
            return f"{self.last_name} @ {self.last_project_ref}"

        return f"{self.last_name}=={self.last_version}"


MOCK_RUNNER = MockRunner()


def test_dryrun(cli, monkeypatch):
    monkeypatch.setattr(runez, "run", MOCK_RUNNER.run)
    # monkeypatch.setattr(runez.program, "run", MOCK_RUNNER.run)
    # monkeypatch.setattr(runez.pyenv, "run", MOCK_RUNNER.run)

    cli.run("-n config")
    assert cli.succeeded
    assert not cli.logged.stderr
    assert "cli:  # empty" in cli.logged.stdout
    assert "defaults:" in cli.logged.stdout

    cli.run("-n --color config")
    assert cli.succeeded

    cli.expect_success("-n auto-heal", "Auto-healed 0 / 0 packages")

    cli.run("-n", "install", runez.DEV.project_folder)
    assert cli.succeeded
    assert "pip install -e " in cli.logged
    assert "Would wrap pickley -> .pk/pickley-dev/bin/pickley" in cli.logged
    assert f"Would state: Installed pickley v{__version__}" in cli.logged

    cli.run("-n install uv==0.3.1")
    assert cli.succeeded
    assert "Would wrap uv -> .pk/uv-0.3.1/bin/uv" in cli.logged
    assert "Would wrap uvx -> .pk/uv-0.3.1/bin/uvx" in cli.logged

    cli.run("-n install https://github.com/codrsquad/portable-python.git")
    assert cli.succeeded
    assert "pip install git+https://github.com/codrsquad/portable-python.git" in cli.logged
    assert "Would wrap portable-python -> .pk/portable-python-103.0/bin/portable-python" in cli.logged

    cli.run("-n -Pfoo diagnostics")
    assert "preferred python : foo [not available]" in cli.logged
    assert "pip.conf : -missing-" in cli.logged

    cli.expect_success("-n list", "No packages installed")

    cli.expect_failure("-n package foo", "Folder ... does not exist")
    cli.expect_failure("-n package . -sfoo", "Invalid symlink specification")

    runez.touch("tmp-project/setup.py", logger=None)
    cli.run("-n package ./tmp-project")
    assert cli.failed
    assert "Could not determine package name" in cli.logged

    cli.run("-n", "package", cli.project_folder, "mgit")
    assert cli.succeeded
    assert "pip install -U pip setuptools" in cli.logged
    cli.match("Would run: ...pip...install -r requirements.txt")
    cli.match("Would run: ...pip...install mgit")

    cli.expect_failure("-n uninstall", "Specify packages to uninstall, or --all")
    cli.expect_failure("-n uninstall pickley", "Run 'uninstall --all' if you wish to uninstall pickley itself")
    cli.expect_failure("-n uninstall mgit", "mgit was not installed with pickley")
    cli.expect_failure("-n uninstall mgit --all", "Either specify packages to uninstall, or --all (but not both)")
    cli.expect_success("-n uninstall --all", "pickley is now uninstalled")

    cli.expect_success("-n upgrade", "No packages installed, nothing to upgrade")
    cli.expect_failure("-n upgrade mgit", "'mgit' is not installed")

    cli.run("-n -Pfoo install bundle:bar")
    assert cli.failed
    assert "Invalid python: foo" in cli.logged

    cli.run("-n --package-manager=uv install uv")
    assert cli.succeeded
    assert "Would download https://github.com/astral-sh/uv/releases/download/" in cli.logged
    assert "Would wrap uv -> .pk/uv-" in cli.logged

    runez.touch(dot_meta("mgit.lock"), logger=None)
    cli.run("-nv --debug auto-upgrade mgit")
    assert cli.succeeded
    assert "Lock file present, another installation is in progress" in cli.logged

    cli.run("-n check")
    assert cli.succeeded
    assert "No packages installed" in cli.logged

    cli.run("-n check mgit pickley2.a")
    assert cli.failed
    assert "mgit: 102.0 not installed" in cli.logged
    assert "pickley2.a: line 1\nline 2\nline 3\n" in cli.logged
    assert "line 4" not in cli.logged

    # Simulate mgit installed
    runez.write(dot_meta("mgit.manifest.json"), '{"entrypoints": ["bogus-mgit"],"version":"1.0"}', logger=None)
    cli.run("-n check mgit")
    assert cli.succeeded
    assert "mgit: 102.0 (currently 1.0 unhealthy)" in cli.logged

    # Simulate an old entry point that was now removed
    cli.run("-n install mgit")
    assert cli.succeeded
    assert "Would state: Installed mgit v102.0" in cli.logged

    cli.run("list")
    cli.expect_success("list", "mgit")
    cli.expect_success("list -fcsv", "mgit")
    cli.expect_success("list -fjson", "mgit")
    cli.expect_success("list -ftsv", "mgit")
    cli.expect_success("list -fyaml", "mgit")
    runez.delete(dot_meta("mgit"), logger=None)

    cli.run("-n -Pinvoker install --no-binary :all: mgit==1.3.0")
    assert cli.succeeded
    assert " --no-binary :all: mgit==1.3.0" in cli.logged
    assert cli.match("Would wrap mgit -> %s" % dot_meta("mgit"))
    assert cli.match("Would save %s" % dot_meta("mgit.manifest.json"))
    assert cli.match("Would state: Installed mgit v1.3.0")

    cli.expect_failure("-n -dfoo install mgit", "Unknown delivery method 'foo'")


def test_dev_mode(cli):
    cli.run("-n", "install", runez.DEV.project_folder, logger=None)
    assert cli.succeeded
    assert "pip install -e " in cli.logged
    assert "Would wrap pickley -> .pk/pickley-dev/bin/pickley" in cli.logged
    assert "Would state: Installed pickley v" in cli.logged


def test_edge_cases(temp_cfg, logged):
    with pytest.raises(NotImplementedError):
        Packager.package(None, None, None, None, False)

    runez.touch("share/python-wheels/some-wheel.whl", logger=None)
    runez.touch("__pycache__/some_module.py", logger=None)
    runez.touch("some_module.pyc", logger=None)
    logged.pop()
    clean_compiled_artifacts(".")
    assert "Deleted 3 compiled artifacts" in logged.pop()
    assert not os.path.exists("share/python-wheels")
    assert os.path.isdir("share")


def test_facultative(cli):
    runez.save_json({"pinned": {"virtualenv": {"facultative": True}}}, dot_meta("config.json"), logger=None)

    # Empty file -> proceed with install as if it wasn't there
    runez.touch("virtualenv", logger=None)
    cli.expect_success("-n install virtualenv==1.0", "Would state: Installed virtualenv")

    # Simulate pickley wrapper
    runez.write("virtualenv", "echo installed by pickley", logger=None)
    runez.make_executable("virtualenv", logger=None)
    cli.expect_success("-n install virtualenv==1.0", "Would state: Installed virtualenv")

    # Unknown executable -> skip pickley installation (since facultative)
    runez.write("virtualenv", "echo foo", logger=None)
    runez.make_executable("virtualenv", logger=None)
    cli.expect_success("-n install virtualenv", "Skipping installation of virtualenv: not installed by pickley")
    cli.expect_success("-n check virtualenv", "skipped, not installed by pickley")


def check_is_wrapper(path, is_wrapper):
    if is_wrapper:
        assert not os.path.islink(path)
        contents = runez.readlines(path)
        assert WRAPPER_MARK in contents

    r = runez.run(path, "--version")
    assert r.succeeded


def check_install_from_pypi(cli, delivery, package, version=None, simulate_version=None):
    if version:
        runez.write(f".pk/.cache/{package}.latest", f'{{"version": "{version}"}}', logger=None)

    cli.run(f"-v -d{delivery} install {package}")
    assert cli.succeeded
    expected = f"Installed {package} v"
    if version:
        expected += version

    assert cli.match(expected)
    assert runez.is_executable(package)
    m = TrackedManifest.from_file(dot_meta(f"{package}.manifest.json"))
    assert str(m)
    assert package in m.entrypoints
    assert m.install_info.args == runez.quoted(cli.args)
    assert m.install_info.timestamp
    assert m.install_info.vpickley == __version__
    assert m.settings.delivery == delivery
    assert m.settings.python
    if version:
        assert m.version == version

    r = runez.run(f"./{package}", "--version")
    assert r.succeeded
    if version:
        assert version in r.full_output

    cli.run(f"--debug auto-upgrade {package}")
    assert cli.succeeded
    assert "Skipping auto-upgrade, checked recently" in cli.logged
    cli.run(f"install {package}")
    assert cli.succeeded
    assert "is already installed" in cli.logged
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
        runez.save_json(m.to_dict(), dot_meta(f"{package}.manifest.json"), logger=None)
        cli.expect_success("check", f"{installed_version} (currently {simulate_version} unhealthy)")


@pytest.mark.skipif(not bstrap.USE_UV, reason="to keep test case simple (uv only)")
def test_install_pypi(cli):
    check_install_from_pypi(cli, "symlink", "uv")

    runez.touch(dot_meta("mgit-0.0.1/pyenv.cfg"), logger=None)
    time.sleep(0.01)  # Ensure 0.0.1 is older than 0.0.2
    runez.touch(dot_meta("mgit-0.0.2/pyenv.cfg"), logger=None)

    # Simulate the presence of an old entry point
    manifest_path = dot_meta("mgit.manifest.json")
    runez.save_json({"entrypoints": ["mgit", "old-mgit-entrypoint"]}, manifest_path, logger=None)
    runez.touch("old-mgit-entrypoint", logger=None)

    check_install_from_pypi(cli, "symlink", "mgit", version="1.3.0")
    assert not os.path.exists("old-mgit-entrypoint")
    assert os.path.islink("mgit")
    assert os.path.exists(dot_meta("mgit.manifest.json"))
    assert not os.path.exists(dot_meta("mgit-0.0.1"))
    assert os.path.exists(dot_meta("mgit-0.0.2"))
    assert os.path.exists(dot_meta("mgit-1.3.0"))

    cli.run("-n auto-heal")
    assert cli.succeeded
    assert "mgit is healthy" in cli.logged
    assert "Auto-healed 0" in cli.logged

    cfg = PickleyConfig()
    cfg.set_base(".")
    pspec = PackageSpec("mgit==1.3.0")
    pspec.groom_installation(keep_for=0)
    assert not os.path.exists(dot_meta("mgit-0.0.2"))
    assert os.path.exists(dot_meta("mgit-1.3.0"))

    cli.expect_success("uninstall mgit", "Uninstalled mgit")
    assert not runez.is_executable("mgit")
    assert not os.path.exists(dot_meta("mgit.manifest.json"))
    assert not os.path.exists(dot_meta("mgit-1.3.0"))
    assert os.path.exists(dot_meta("audit.log"))

    check_install_from_pypi(cli, "wrap", "mgit", version="1.3.0", simulate_version="0.0.0")
    check_is_wrapper("./mgit", True)

    runez.delete(dot_meta("mgit-1.3.0"), logger=None)
    cli.run("-n auto-heal")
    assert cli.succeeded
    assert "Auto-healed 1 / 2 packages" in cli.logged


@pytest.mark.skipif(not bstrap.USE_UV, reason="to keep test case simple (uv only)")
def test_invalid(cli):
    cli.run("--color install six")
    assert cli.failed
    assert "not a CLI" in cli.logged
    assert not os.path.exists(dot_meta("six.manifest.json"))

    cli.expect_failure("install mgit+foo")
    assert cli.failed
    assert "'pip install mgit+foo' failed" in cli.logged


def test_lock(temp_cfg):
    pspec = PackageSpec("foo==1.0")
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
    runez.write(lock_path, "0\nbar\n", logger=None)
    with SoftLock(pspec, give_up=600):
        lines = list(runez.readlines(lock_path))
        assert lines[0] == str(os.getpid())  # Lock file replaced with correct stuff

    assert not os.path.exists(lock_path)  # Lock released


def test_main(cli):
    cli.exercise_main("-mpickley", "src/pickley/bstrap.py")


def test_package_venv(cli):
    # TODO: retire the `package` command, not worth the effort to support it
    # Verify that "debian mode" works as expected, with -droot/tmp <-> /tmp
    runez.delete("/tmp/pickley", logger=None)
    cli.run("package", cli.project_folder, "-droot/tmp", "--no-compile", "--sanity-check=--version", "-sroot:root/usr/local/bin")
    assert cli.succeeded
    assert "--version" in cli.logged
    assert runez.is_executable("/tmp/pickley/bin/pip3")
    assert runez.is_executable("/tmp/pickley/bin/pickley")
    r = runez.run("/tmp/pickley/bin/pickley", "--version")
    assert r.succeeded
    runez.delete("/tmp/pickley", logger=None)


def test_package_venv_with_additional_packages(cli):
    # TODO: retire the `package` command, not worth the effort to support it
    runez.delete("/tmp/pickley", logger=None)
    cli.run("package", "-droot/tmp", "-sroot:root/usr/local/bin", cli.project_folder)
    assert cli.succeeded
    assert "pip install -r requirements.txt" in cli.logged
    assert runez.is_executable("/tmp/pickley/bin/pip3")
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
