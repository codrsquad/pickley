
from click.testing import CliRunner

from pickley.cli import main


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
        if message[0] == '!':
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
    expect_success('--version', "version ")
    expect_success('--help', "Package manager for python CLIs")
    expect_success('install --help', "Install a package")
