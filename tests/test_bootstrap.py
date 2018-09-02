import os

from mock import patch

from pickley import capture_output, system
from pickley.cli import bootstrap, get_packager
from pickley.settings import Definition, SETTINGS

from .conftest import verify_abort


def test_bootstrap(temp_base):
    SETTINGS.cli.contents["delivery"] = "wrap"
    pickley = os.path.join(temp_base, system.PICKLEY)
    with capture_output() as logged:
        bootstrap(testing=True)
        assert "Bootstraped pickley" in logged

    # Verify it works
    output = system.run_program(pickley, "--version")
    assert "version " in output


@patch("pickley.package.VenvPackager.is_within", return_value=True)
def test_second_bootstrap(_, temp_base):
    # Simulate a 2nd bootstrap, this will have to use a relocatable venv
    SETTINGS.cli.contents["delivery"] = "wrap"
    pickley = os.path.join(temp_base, system.PICKLEY)

    system.DRYRUN = True
    with capture_output() as logged:
        bootstrap(testing=True)
        assert "Would move " in logged
        assert "Would bootstrap pickley" in logged

    system.DRYRUN = False
    with capture_output() as logged:
        bootstrap(testing=True)
        assert "Moving " in logged
        assert "Bootstraped pickley" in logged

    # Verify it still works
    output = system.run_program(pickley, "--version")
    assert "version " in output


@patch("pickley.settings.SETTINGS.version", return_value=Definition(None, None, None))
def test_bootstrap_no_version(_):
    assert "Can't bootstrap" in verify_abort(bootstrap, testing=True)


@patch("pickley.package.PACKAGERS.resolved", return_value=Definition(None, None, None))
def test_packager_unknown(_):
    assert "Unknown packager 'None'" in verify_abort(get_packager, None)


@patch("pickley.package.PACKAGERS.resolved", return_value=None)
def test_packager_missing(_):
    assert "No packager configured" in verify_abort(get_packager, None)


@patch("pickley.package.PACKAGERS.get", return_value=Definition)
def test_packager_bogus(_):
    assert "Invalid packager implementation" in verify_abort(get_packager, None)
