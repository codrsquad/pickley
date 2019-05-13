import os
import sys

import runez
from mock import patch

from pickley import system
from pickley.lock import SoftLockException
from pickley.package import PACKAGERS
from pickley.settings import short
from pickley.uninstall import find_uninstaller

from .conftest import PROJECT


IS_PYTHON3 = sys.version >= "3"


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
    cli.expect_success("--version")


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
    cli.expect_success("settings -d", "base: %s" % short(cli.context))
    cli.expect_success("check", "No packages installed")
    cli.expect_success("list", "No packages installed")

    cli.expect_failure("-b foo/bar settings", "Can't use foo/bar as base: folder does not exist")

    cli.expect_failure("--dryrun check -- -this-package-does-not-exist ", "can't determine latest version")

    cli.expect_failure("--dryrun --delivery foo install tox", "invalid choice: foo")

    cli.expect_failure("--dryrun uninstall --force /dev/null", "is not a valid pypi package name")
    cli.expect_success("--dryrun uninstall --force -- -this-package-does-not-exist ", "Nothing to uninstall")

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
    tox = system.SETTINGS.base.full_path("tox")
    foldl = system.SETTINGS.base.full_path("foldl")

    p = PACKAGERS.resolved(system.PackageSpec("tox"))
    p.refresh_desired()
    tox_version = p.desired.version
    assert not os.path.exists(tox)
    assert not os.path.exists(foldl)

    cli.expect_success("--dryrun -b{base} --delivery wrap install tox", "Would wrap", "Would install tox", base=cli.context)
    cli.expect_success("--dryrun -b{base} --delivery symlink install tox", "Would symlink", "Would install tox", base=cli.context)

    cli.expect_failure("-b{base} check tox", "is not installed", base=cli.context)
    cli.expect_failure("-b{base} install six", "'six' is not a CLI", base=cli.context)
    if IS_PYTHON3:
        cli.expect_failure("-b{base} check shell-functools", "is not installed", base=cli.context)

    # Install tox, but add a few files + a bogus previous entry point to test cleanup
    wep1 = system.SETTINGS.base.full_path("tox-old-entrypoint1")
    tep10 = system.SETTINGS.meta.full_path("tox", "tox-old-entrypoint1-1.0")
    tep11 = system.SETTINGS.meta.full_path("tox", "tox-old-entrypoint1-1.1")
    t00 = system.SETTINGS.meta.full_path("tox", "tox-0.0.0")
    tfoo = system.SETTINGS.meta.full_path("tox", "tox-foo")
    sfoldl = system.SETTINGS.meta.full_path("shell_functools", "shell_functools-1.0.0", "bin", "foldl")
    runez.touch(wep1)
    runez.touch(tep10)
    runez.touch(tep11)
    runez.touch(t00)
    runez.touch(tfoo)
    eppath = system.SETTINGS.meta.full_path("tox", ".entry-points.json")
    runez.write(eppath, '["tox-old-entrypoint1", "tox-old-entrypoint2"]\n')
    cli.expect_success("-b{base} --delivery wrap install tox", "Installed tox", base=cli.context)
    if IS_PYTHON3:
        runez.touch(sfoldl)
        assert os.path.exists(sfoldl)
        cli.expect_success("-b{base} --delivery wrap install shell-functools", "Installed shell-functools", base=cli.context)
        assert runez.is_executable(foldl)
        output = run_program(foldl, "--help")
        assert "foldl" in output
        assert not os.path.exists(sfoldl)

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

    cli.expect_success("copy .pickley/tox/tox-%s tox-copy" % tox_version, "Copied")
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

    p = PACKAGERS.get(system.VENV_PACKAGER)(system.PackageSpec("tox"))
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
    if IS_PYTHON3:
        cli.expect_success("-b{base} uninstall shell-functools", "Uninstalled shell-functools", "entry points", base=cli.context)
    assert not os.path.exists(tox)
    assert not os.path.exists(foldl)


def test_auto_upgrade_locked(cli):
    with patch("pickley.package.VersionMeta.valid", return_value=True):
        with patch("pickley.package.Packager.internal_install", side_effect=SoftLockException(".lock")):
            cli.expect_failure("--dryrun install foo", "installed by another process")
            cli.expect_success("--dryrun auto-upgrade foo", "installed by another process")
