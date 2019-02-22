import os

import runez
from mock import patch

from pickley import __version__, system
from pickley.lock import SoftLockException
from pickley.package import PACKAGERS
from pickley.system import short
from pickley.uninstall import find_uninstaller

from .conftest import PROJECT


def test_help(cli):
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


def test_version(cli):
    cli.expect_success("--version", __version__)


def test_settings(cli):
    cli.expect_success("settings -d", "settings:", "base: %s" % short(system.SETTINGS.base.path))


def run_program(program, *args):
    return runez.run(program, *args, fatal=False)


def test_package(cli):
    pickley = system.SETTINGS.base.full_path("dist", "pickley", "bin", "pickley")
    expected_version = system.run_python(os.path.join(PROJECT, "setup.py"), "--version")

    # Package pickley as venv
    cli.expect_success(["package", "-d", "dist", PROJECT], "Packaged %s successfully" % short(PROJECT))

    # Verify that it packaged OK, and is relocatable
    assert runez.is_executable(pickley)
    assert run_program(pickley, "--version") == expected_version
    assert runez.first_line(pickley).startswith("#!/usr/bin/env python")


def test_bogus_install(cli):
    cli.expect_failure("-b foo/bar settings", "Can't use", "as base", "folder does not exist")

    cli.expect_failure("auto-upgrade foo", "not currently installed")
    cli.expect_failure("package foo/bar", "Folder", "does not exist")
    cli.expect_failure(["package", cli.context], "No setup.py")
    runez.touch(os.path.join(cli.context, "setup.py"))
    cli.expect_failure(["package", cli.context], "Could not determine package name")

    cli.expect_success("-b{base} check", "No packages installed", base=cli.context)
    cli.expect_success("-b{base} list", "No packages installed", base=cli.context)

    cli.expect_success("settings -d", "base: %s" % short(cli.context))


def test_install(cli):
    tox = system.SETTINGS.base.full_path("tox")
    p = PACKAGERS.resolved("tox")
    p.refresh_desired()
    tox_version = p.desired.version
    assert not os.path.exists(tox)
    assert runez.first_line(tox) is None

    cli.expect_success("--dryrun -b{base} --delivery wrap install tox", "Would wrap", "Would install tox", base=cli.context)
    cli.expect_success("--dryrun -b{base} --delivery symlink install tox", "Would symlink", "Would install tox", base=cli.context)
    cli.expect_failure("--dryrun -b{base} --delivery foo install tox", "invalid choice: foo", base=cli.context)

    cli.expect_success("--dryrun uninstall /dev/null --force", "Nothing to uninstall")

    runez.touch("foo")
    assert os.path.exists("foo")
    cli.expect_failure("uninstall foo", "foo was not installed with pickley")
    cli.expect_success("uninstall foo --force", "Uninstalled foo")

    assert not os.path.exists("foo")
    assert runez.ensure_folder("foo", folder=True) == 1
    cli.expect_failure("uninstall foo --force", "Can't automatically uninstall")

    cli.expect_failure("-b{base} check tox foo/bar", "is not installed", "can't determine latest version", base=cli.context)
    cli.expect_failure("-b{base} install six", "'six' is not a CLI", base=cli.context)

    # Install tox, but add a few files + a bogus previous entry point to test cleanup
    wep1 = system.SETTINGS.base.full_path("tox-old-entrypoint1")
    tep10 = system.SETTINGS.meta.full_path("tox", "tox-old-entrypoint1-1.0")
    tep11 = system.SETTINGS.meta.full_path("tox", "tox-old-entrypoint1-1.1")
    t00 = system.SETTINGS.meta.full_path("tox", "tox-0.0.0")
    tfoo = system.SETTINGS.meta.full_path("tox", "tox-foo")
    runez.touch(wep1)
    runez.touch(tep10)
    runez.touch(tep11)
    runez.touch(t00)
    runez.touch(tfoo)
    eppath = system.SETTINGS.meta.full_path("tox", ".entry-points.json")
    runez.write(eppath, '["tox-old-entrypoint1", "tox-old-entrypoint2"]\n')
    cli.expect_success("-b{base} --delivery wrap install tox", "Installed tox", base=cli.context)

    # Old entry point removed immediately
    assert not os.path.exists(wep1)

    # Only 1 cleaned up immediately (latest + 1 kept)
    assert not os.path.exists(tep10)
    assert os.path.exists(tep11)
    assert not os.path.exists(t00)
    assert os.path.exists(tfoo)

    assert runez.is_executable(tox)
    output = run_program(tox, "--version")
    assert "tox" in output
    assert tox_version in output

    cli.expect_success("-b{base} auto-upgrade tox", "Skipping auto-upgrade", base=cli.context)
    runez.delete(system.SETTINGS.meta.full_path("tox", ".ping"))
    cli.expect_success("-b{base} auto-upgrade tox", "already installed", base=cli.context)

    version = output.partition(" ")[0]
    cli.expect_success("copy .pickley/tox/tox-%s tox-copy" % version, "Copied")
    cli.expect_success("move tox-copy tox-relocated", "Moved")

    # Verify that older versions and removed entry-points do get cleaned up
    runez.save_json({"install_timeout": 0}, "custom-timeout.json")
    cli.expect_success("-b{base} -ccustom-timeout.json install tox", "already installed", base=cli.context)

    # All cleaned up when enough time went by
    assert not os.path.exists(tep10)
    assert not os.path.exists(tep11)
    assert not os.path.exists(t00)
    assert not os.path.exists(tfoo)

    cli.expect_success("-b{base} check", "tox", "is installed", base=cli.context)
    cli.expect_success(
        "-b{base} check --verbose",
        "tox",
        "is installed (as %s wrap, channel: " % system.VENV_PACKAGER,
        base=cli.context,
    )

    p = PACKAGERS.get(system.VENV_PACKAGER)("tox")
    p.refresh_latest()
    p.latest.version = "10000.0"
    p.latest.save()
    cli.expect_failure("-b{base} check", "tox", "can be upgraded to 10000.0", base=cli.context)

    cli.expect_success("-b{base} -ppex install twine", "Installed twine", base=cli.context)

    cli.expect_success("-b{base} list", "tox", "twine", base=cli.context)
    cli.expect_success("-b{base} list --verbose", "tox", "twine", base=cli.context)

    tmp = os.path.realpath(cli.context)
    assert find_uninstaller(os.path.join(tmp, "tox"))
    assert find_uninstaller(os.path.join(tmp, "twine"))

    cli.expect_success("-b{base} uninstall twine", "Uninstalled twine", base=cli.context)

    runez.delete(p.current._path)
    runez.touch(p.current._path)
    cli.expect_failure("-b{base} check", "tox", "Couldn't read", "is not installed", base=cli.context)

    cli.expect_success("-b{base} uninstall tox", "Uninstalled tox", "entry points", base=cli.context)


def test_auto_upgrade_locked(cli):
    with patch("pickley.package.VersionMeta.valid", return_value=True):
        with patch("pickley.package.Packager.internal_install", side_effect=SoftLockException(".lock")):
            cli.expect_failure("--dryrun install foo", "installed by another process")
            cli.expect_success("--dryrun auto-upgrade foo", "installed by another process")
