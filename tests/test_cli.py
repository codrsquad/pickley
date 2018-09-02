from click.testing import CliRunner

from pickley import capture_output, short, system
from pickley.cli import main
from pickley.settings import SETTINGS


TESTS = system.parent_folder(__file__)
PROJECT = system.parent_folder(TESTS)


def run_cli(args, **kwargs):
    """
    :param str|list args: Command line args
    :return click.testing.Result:
    """
    runner = CliRunner()
    if not isinstance(args, list):
        args = args.split()
    if "--no-user-config" not in args:
        args = ["--no-user-config"] + args
    base = kwargs.pop("base", None)
    if base and "-b" not in args and "--base" not in args:
        args = ["-b", base] + args
    if "--debug" not in args:
        args = ["--debug"] + args
    with capture_output() as logged:
        result = runner.invoke(main, args=args)
        result.logged = logged.to_string()
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
    expect_messages("%s\n%s" % (result.output, result.logged), *messages)


def expect_failure(args, *messages, **kwargs):
    result = run_cli(args, **kwargs)
    assert result.exit_code != 0
    expect_messages("%s\n%s" % (result.output, result.logged), *messages)


def test_help():
    expect_success("--help", "Package manager for python CLIs", "-q, --quiet",  "-b, --base PATH")
    expect_success("check --help", "check [OPTIONS] [PACKAGES]...")
    expect_success("install --help", "install [OPTIONS] PACKAGES...")
    expect_success("package --help", "package [OPTIONS] FOLDER", "-b, --build", "-d, --dist", "--packager")


def test_version():
    expect_success("-q --version", "version ")


def test_settings():
    expect_success("settings -d", "settings:", "python interpreter: %s" % short(system.PYTHON), "base: %s" % short(SETTINGS.base.path))


def run_program(program, *args):
    return system.run_program(program, *args, fatal=False)


def test_package(temp_base):
    pickley = SETTINGS.base.full_path("pickley")

    # Package pickley as pex
    expect_success(["package", "-d", ".", PROJECT], "Packaged %s successfully" % short(PROJECT))

    # Verify that it packaged OK
    assert system.is_executable(pickley)
    output = run_program(pickley, "--version")
    assert "version " in output
    assert system.first_line(pickley) == "#!/usr/bin/env python"


def test_install(temp_base):
    tox = SETTINGS.base.full_path("tox")

    expect_success("settings -d", "base: %s" % short(temp_base), "cache: %s" % short(SETTINGS.base.full_path(system.DOT_PICKLEY)), base=temp_base)

    expect_success("-n -cdelivery=wrap install tox", "Would wrap", "Would install tox", base=temp_base)
    expect_success("-n -cdelivery=symlink install tox", "Would symlink", "Would install tox", base=temp_base)
    expect_failure("-n -cdelivery=foo install tox", "Unknown delivery type 'foo'", base=temp_base)

    expect_failure("install six", "'six' is not a CLI", base=temp_base)

    expect_success("install tox", "Installed tox", base=temp_base)
    assert system.is_executable(tox)
    output = run_program(tox, "--version")
    assert "tox" in output

    expect_success("install tox", "already installed", base=temp_base)
    expect_success("install twine -ppex", "Installed twine", base=temp_base)

    expect_success("list", "tox", "twine", base=temp_base)
    expect_success("list --verbose", "tox", "twine", base=temp_base)

    expect_success("check", "tox", "is installed", base=temp_base)
    expect_success("check --verbose", "tox", "is installed (as venv, channel: ", base=temp_base)
