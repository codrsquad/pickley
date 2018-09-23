import os

import runez
from click.testing import CliRunner
from mock import patch

from pickley import system
from pickley.cli import main
from pickley.lock import SoftLockException
from pickley.package import PACKAGERS
from pickley.settings import JsonSerializable
from pickley.system import short
from pickley.uninstall import find_uninstaller

from .conftest import PROJECT


def run_cli(args, **kwargs):
    """
    :param str|list args: Command line args
    :return click.testing.Result:
    """
    runner = CliRunner()
    if not isinstance(args, list):
        args = args.split()
    base = kwargs.pop("base", None)
    if base and "-b" not in args and "--base" not in args:
        args = ["-b", base] + args
    result = runner.invoke(main, args=args)
    if "--dryrun" in args:
        # Restore default non-dryrun state after a --dryrun test
        runez.DRYRUN = False
    return result


def expect_messages(result, *messages):
    for message in messages:
        if message[0] == "!":
            assert message[1:] not in result
        else:
            assert message in result


def expect_success(args, *messages, **kwargs):
    result = run_cli(args, **kwargs)
    assert result.exit_code == 0
    expect_messages(result.output, *messages)


def expect_failure(args, *messages, **kwargs):
    result = run_cli(args, **kwargs)
    assert result.exit_code != 0
    expect_messages(result.output, *messages)


def test_help():
    expect_success(
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
    expect_success("auto-upgrade --help", "auto-upgrade [OPTIONS] PACKAGE")
    expect_success("check --help", "check [OPTIONS] [PACKAGES]...", "-v, --verbose")
    expect_success("install --help", "install [OPTIONS] PACKAGES...", "-f, --force")
    expect_success("package --help", "package [OPTIONS] FOLDER", "-b, --build", "-d, --dist")


def test_version():
    expect_success("--version", "version ")


def test_settings():
    expect_success(
        "settings -d",
        "settings:",
        "base: %s" % short(system.SETTINGS.base.path)
    )


def run_program(program, *args):
    return runez.run_program(program, *args, fatal=False)


def test_package(temp_base):
    pickley = system.SETTINGS.base.full_path("pickley")

    # Package pickley as pex
    expect_success(["-ppex==1.4.5", "package", "-d", ".", PROJECT], "Packaged %s successfully" % short(PROJECT))

    # Verify that it packaged OK
    assert runez.is_executable(pickley)
    output = run_program(pickley, "--version")
    assert "version " in output
    assert runez.first_line(pickley) == "#!/usr/bin/env python"


def test_bogus_install(temp_base):
    expect_failure("-b foo/bar settings", "Can't use", "as base", "folder does not exist")

    expect_failure("auto-upgrade foo", "not currently installed")
    expect_failure("package foo/bar", "Folder", "does not exist")
    expect_failure(["package", temp_base], "No setup.py")
    runez.touch(os.path.join(temp_base, "setup.py"))
    expect_failure(["package", temp_base], "Could not determine package name")

    expect_success("check", "No packages installed", base=temp_base)
    expect_success("list", "No packages installed", base=temp_base)

    expect_success("settings -d", "base: %s" % short(temp_base))


def test_install(temp_base):
    tox = system.SETTINGS.base.full_path("tox")
    assert not os.path.exists(tox)
    assert runez.first_line(tox) is None

    expect_success("--dryrun --delivery wrap install tox", "Would wrap", "Would install tox", base=temp_base)
    expect_success("--dryrun --delivery symlink install tox", "Would symlink", "Would install tox", base=temp_base)
    expect_failure("--dryrun --delivery foo install tox", "invalid choice: foo", base=temp_base)

    expect_success("--dryrun uninstall /dev/null --force", "Nothing to uninstall")

    runez.touch("foo")
    assert os.path.exists("foo")
    expect_failure("uninstall foo", "foo was not installed with pickley")
    expect_success("uninstall foo --force", "Uninstalled foo")

    assert not os.path.exists("foo")
    assert runez.ensure_folder("foo", folder=True) == 1
    expect_failure("uninstall foo --force", "Can't automatically uninstall")

    expect_failure("check tox foo/bar", "is not installed", "can't determine latest version", base=temp_base)
    expect_failure("install six", "'six' is not a CLI", base=temp_base)

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
    runez.write_contents(system.SETTINGS.meta.full_path("tox", ".entry-points.json"), '["tox-old-entrypoint1", "tox-old-entrypoint2"]\n')
    expect_success("--delivery wrap install tox", "Installed tox", base=temp_base)

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

    expect_success("auto-upgrade tox", "Skipping auto-upgrade", base=temp_base)
    runez.delete(system.SETTINGS.meta.full_path("tox", ".ping"))
    expect_success("auto-upgrade tox", "already installed", base=temp_base)

    version = output.partition(" ")[0]
    expect_success("copy .pickley/tox/tox-%s tox-copy" % version, "Copied")
    expect_success("move tox-copy tox-relocated", "Moved")

    # Verify that older versions and removed entry-points do get cleaned up
    JsonSerializable.save_json({"install_timeout": 0}, "custom-timeout.json")
    expect_success("-ccustom-timeout.json install tox", "already installed", base=temp_base)

    # All cleaned up when enough time went by
    assert not os.path.exists(tep10)
    assert not os.path.exists(tep11)
    assert not os.path.exists(t00)
    assert not os.path.exists(tfoo)

    expect_success("check", "tox", "is installed", base=temp_base)
    expect_success(
        "check --verbose",
        "tox",
        "is installed (as %s wrap, channel: " % system.VENV_PACKAGER,
        base=temp_base
    )

    p = PACKAGERS.get(system.VENV_PACKAGER)("tox")
    p.refresh_latest()
    p.latest.version = "10000.0"
    p.latest.save()
    expect_failure("check", "tox", "can be upgraded to 10000.0", base=temp_base)

    expect_success("-ppex install twine", "Installed twine", base=temp_base)

    expect_success("list", "tox", "twine", base=temp_base)
    expect_success("list --verbose", "tox", "twine", base=temp_base)

    tmp = os.path.realpath(temp_base)
    assert find_uninstaller(os.path.join(tmp, "tox"))
    assert find_uninstaller(os.path.join(tmp, "twine"))

    expect_success("uninstall twine", "Uninstalled twine", base=temp_base)

    p.refresh_current()
    runez.delete(p.current._path)
    runez.touch(p.current._path)
    expect_failure("check", "tox", "Invalid json file", "is not installed", base=temp_base)

    expect_success("uninstall tox", "Uninstalled tox", "entry points", base=temp_base)


@patch("pickley.package.VersionMeta.valid", return_value=True)
@patch("pickley.package.Packager.internal_install", side_effect=SoftLockException(".lock"))
def test_auto_upgrade_locked(*_):
    expect_failure("--dryrun install foo", "installed by another process")
    expect_success("--dryrun auto-upgrade foo", "installed by another process")
