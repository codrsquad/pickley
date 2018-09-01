import os
import shutil
from tempfile import mkdtemp

import pytest
from click.testing import CliRunner

from pickley import short, system
from pickley.cli import main
from pickley.settings import SETTINGS


TESTS = system.parent_folder(__file__)
PROJECT = system.parent_folder(TESTS)


@pytest.fixture
def temp_base():
    old_base = SETTINGS.base
    old_config = SETTINGS.config
    old_cwd = os.getcwd()

    path = mkdtemp()
    os.chdir(path)
    SETTINGS.__init__(base=path, config=[])
    yield path

    os.chdir(old_cwd)
    SETTINGS.__init__(base=old_base, config=old_config)
    shutil.rmtree(path)


def run_cli(args):
    """
    :param str|list args: Command line args
    :return click.testing.Result:
    """
    runner = CliRunner()
    if not isinstance(args, list):
        args = args.split()
    result = runner.invoke(main, args=args)
    return result


def expect_messages(result, *messages):
    for message in messages:
        if message[0] == "!":
            assert message[1:] not in result
        else:
            assert message in result


def expect_success(args, *messages):
    result = run_cli(args)
    assert result.exit_code == 0
    expect_messages(result.output, *messages)


def expect_failure(args, *messages):
    result = run_cli(args)
    assert result.exit_code != 0
    expect_messages(result.output, *messages)


def test_help():
    expect_success("--help", "Package manager for python CLIs", "-q, --quiet",  "--base PATH")
    expect_success("check --help", "check [OPTIONS] [PACKAGES]...")
    expect_success("install --help", "install [OPTIONS] PACKAGES...")
    expect_success("package --help", "package [OPTIONS] FOLDER", "-b, --build", "-d, --dist", "--packager")


def test_version():
    expect_success("-q --version", "version ")


def test_settings():
    expect_success("settings -d", "settings:", "python interpreter: %s" % short(system.PYTHON), "base: %s" % short(SETTINGS.base.path))


def run_program(program, *args):
    return system.run_program(program, *args, fatal=False)


def test_install(temp_base):
    pickley = SETTINGS.base.full_path("pickley")
    tox = SETTINGS.base.full_path("tox")

    expect_success(["package", "-d", ".", PROJECT], "Packaged %s successfully" % short(PROJECT))
    assert system.is_executable(pickley)
    SETTINGS.__init__(base=temp_base, config=[])

    output = run_program(pickley, "--version")
    assert "version " in output

    expect_success(["--base", temp_base, "settings", "-d"], "base: %s" % short(temp_base), "cache: %s" % short(SETTINGS.base.full_path(system.DOT_PICKLEY)))
    expect_success(["--base", temp_base, "install", "tox"], "Installed tox")
    assert system.is_executable(tox)

    output = run_program(tox, "--version")
    assert "tox" in output
