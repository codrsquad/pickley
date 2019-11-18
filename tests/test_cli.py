import os
import sys

import runez
from mock import patch

from pickley import system
from pickley.lock import SoftLockException
from pickley.settings import short
from pickley.uninstall import find_uninstaller

from .conftest import PROJECT


IS_PYTHON3 = sys.version >= "3"


def test_invocation(cli):
    cli.expect_success("--version")

    cli.expect_success(
        "--help",  # Run --help
        "--version",  # Verify that below flags are mentioned in output
        "--debug",
        "-n, --dryrun",
        "-b, --base PATH",
        "-c, --config PATH",
        "-P, --python PATH",
        "-d, --delivery",
        "-p, --packager",
    )
    cli.expect_success("auto-upgrade --help", "auto-upgrade [OPTIONS] PACKAGE")
    cli.expect_success("check --help", "check [OPTIONS] [PACKAGES]..", "-v, --verbose")
    cli.expect_success("install --help", "install [OPTIONS] PACKAGES..", "-f, --force")
    cli.expect_success("package --help", "package [OPTIONS] FOLDER", "-b, --build", "-d, --dist")

    cli.expect_success("settings -d", "settings:", "base: %s" % os.getcwd())

    cli.expect_failure("uninstall", "Specify packages to uninstall")
    cli.expect_failure("uninstall foo --all", "not both")
    cli.expect_failure("uninstall pickley", "if you wish to uninstall pickley itself")


def test_auto_upgrade_locked(cli):
    with patch("pickley.package.VersionMeta.valid", return_value=True):
        with patch("pickley.package.Packager.internal_install", side_effect=SoftLockException(".lock")):
            cli.expect_failure("--dryrun install foo", "installed by another process")
            cli.expect_success("--dryrun auto-upgrade foo", "installed by another process")


def run_program(program, *args):
    if not os.path.isabs(program):
        program = os.path.abspath(program)
    return runez.run(program, *args, fatal=False)


def test_package(cli):
    expected_version = system.run_python(os.path.join(PROJECT, "setup.py"), "--version")

    # Package pickley as venv
    cli.expect_success(["package", "-d", "dist", PROJECT], "Packaged %s successfully" % short(PROJECT))

    # Verify that it packaged OK, and is relocatable
    pickley = os.path.abspath("dist/pickley/bin/pickley")
    assert runez.is_executable(pickley)
    assert run_program(pickley, "--version") == expected_version
    assert runez.first_line(pickley).startswith("#!/usr/bin/env python")


def test_bogus_install(cli):
    cli.expect_success("settings -d", "base: %s" % short(cli.context))
    cli.expect_success("check", "No packages installed")
    cli.expect_success("list", "No packages installed")

    cli.expect_failure("-b foo/bar settings", "Can't use foo/bar as base: folder does not exist")

    cli.expect_failure("--dryrun check -- -bogus", "not a valid pypi package name")
    cli.expect_failure("--dryrun check this-package-does-not-exist", "can't determine latest version")

    cli.expect_failure("--dryrun --delivery foo install tox", "invalid choice: foo")

    cli.expect_failure("--dryrun uninstall --force /dev/null", "is not a valid pypi package name")
    cli.expect_success("--dryrun uninstall --force -- this-package-does-not-exist ", "Nothing to uninstall")

    runez.touch("foo")
    assert os.path.exists("foo")
    cli.expect_failure("uninstall foo", "foo was not installed with pickley")
    cli.expect_success("uninstall foo --force", "Uninstalled foo")
    assert not os.path.exists("foo")
    assert runez.ensure_folder("foo", folder=True) == 1
    cli.expect_failure("uninstall foo --force", "Can't automatically uninstall")

    cli.expect_failure("auto-upgrade foo", "not currently installed")

    cli.expect_failure("package foo/bar", "Folder", "does not exist")
    cli.expect_failure(["package", cli.context], "No setup.py")
    runez.touch(os.path.join(cli.context, "setup.py"))
    cli.expect_failure(["package", cli.context], "Could not determine package name")

    cli.expect_success("check", "No packages installed")
    cli.expect_success("list", "No packages installed")
    cli.expect_success("settings -d", "base: %s" % short(cli.context))


def test_install(cli):
    cli.expect_success("--dryrun --delivery wrap install tox", "Would wrap", "Would install tox")
    cli.expect_success("--dryrun --delivery symlink install tox", "Would symlink", "Would install tox")
    assert not os.path.exists(".pickley/audit.log")

    cli.expect_failure("check tox", "is not installed")
    cli.expect_failure("install six", "'six' is not a CLI")

    # Install tox, but add a few files + a bogus previous entry point to test cleanup
    runez.write(".pickley/tox/.entry-points.json", '["tox-old1", "tox-old2"]\n')
    runez.touch("tox-old1")
    runez.touch(".pickley/tox/tox-0.1/bin")
    runez.touch(".pickley/tox/tox-0.2/bin")
    runez.touch(".pickley/tox/tox-0.3/bin")
    runez.touch(".pickley/tox/tox-old1-0.1")
    runez.touch(".pickley/tox/tox-old1-0.2")
    cli.expect_success("--delivery wrap install tox", "Installed tox")

    # Old entry point removed immediately
    assert not os.path.exists("tox-old1")

    # Only 1 cleaned up immediately (latest + 1 kept)
    assert not os.path.exists(".pickley/tox/tox-0.1")
    assert not os.path.exists(".pickley/tox/tox-0.2")
    assert os.path.exists(".pickley/tox/tox-0.3")
    assert not os.path.exists(".pickley/tox/tox-old1-0.1")
    assert os.path.exists(".pickley/tox/tox-old1-0.2")

    assert runez.is_executable("tox")
    output = run_program("tox", "--version")
    assert "tox" in output

    cli.expect_success("auto-upgrade tox", "Skipping auto-upgrade")
    runez.delete(system.SETTINGS.meta.full_path("tox", ".ping"))
    cli.expect_success("auto-upgrade tox", "already installed")

    cli.expect_success("copy .pickley/tox tox-copy", "Copied")
    cli.expect_success("move tox-copy tox-relocated", "Moved")
    runez.delete("tox-relocated")

    # Verify that older versions and removed entry-points do get cleaned up
    runez.save_json({"install_timeout": 0}, "custom-timeout.json")
    cli.expect_success("-ccustom-timeout.json install tox", "already installed")

    # All cleaned up when enough time went by
    assert not os.path.exists(".pickley/tox/tox-0.3")
    assert not os.path.exists(".pickley/tox/tox-old1-0.2")

    cli.expect_success("check", "tox", "is installed")
    cli.expect_success("check --verbose", "tox", "is installed (as %s wrap, channel: " % system.VENV_PACKAGER)

    # Simulate new version available
    latest = runez.read_json(".pickley/tox/.latest.json")
    latest["version"] = "10000.0"
    runez.save_json(latest, ".pickley/tox/.latest.json")
    cli.expect_failure("check", "tox", "can be upgraded to 10000.0")

    # Latest twine 2.0 requires py3
    cli.expect_success("-ppex install twine==1.14.0", "Installed twine")

    cli.expect_success("list", "tox", "twine")
    cli.expect_success("list --verbose", "tox", "twine")

    assert find_uninstaller("tox")
    assert find_uninstaller("twine")

    cli.expect_success("uninstall twine", "Uninstalled twine")

    runez.write(".pickley/tox/.current.json", "")
    cli.expect_failure("check", "tox", "Couldn't read", "is not installed")

    cli.expect_success("uninstall --all", "Uninstalled tox", "entry points")
    assert not os.path.exists("tox")
    assert not os.path.exists(".pickley")
