import os
import sys

import pytest
from mock import patch

from pickley import system
from pickley.cli import bootstrap
from pickley.context import CaptureOutput
from pickley.lock import SoftLockException


@patch("pickley.system.relaunch")
def test_bootstrap(_, temp_base):
    system.SETTINGS.cli.contents["delivery"] = "wrap"
    pickley = os.path.join(temp_base, system.PICKLEY)

    with CaptureOutput(dryrun=True) as logged:
        bootstrap(testing=True)
        assert "Would move " in logged
        assert "Would bootstrap pickley" in logged

    with CaptureOutput() as logged:
        bootstrap(testing=True)
        assert "Relocating venv " in logged
        assert "Bootstraped pickley" in logged

    # Verify it works
    output = system.run_program(pickley, "--version")
    assert "version " in output

    # A 2nd call to boostrap() should be a no-op
    with CaptureOutput() as logged:
        bootstrap(testing=True)
        assert not str(logged)


@patch("pickley.package.Packager.internal_install", side_effect=SoftLockException(".lock"))
def test_bootstrap_in_progress(_, temp_base):
    # No bootstrap unless delivery is wrap
    system.SETTINGS.cli.contents["delivery"] = "symlink"
    with CaptureOutput() as logged:
        assert bootstrap(testing=True) is None
        assert not str(logged)

    # Bootstrap attempted (but mocked out as can't acquire lock)
    system.SETTINGS.cli.contents["delivery"] = "wrap"
    with CaptureOutput() as logged:
        assert bootstrap(testing=True) is None
        assert not str(logged)

    # No bootstrap unless packager is venv (default)
    system.SETTINGS.cli.contents["packager"] = "pex"
    with CaptureOutput() as logged:
        assert bootstrap(testing=True) is None
        assert not str(logged)


@patch("pickley.system.run_program")
def test_relaunch(run_program):
    with pytest.raises(SystemExit):
        system.relaunch()
    # Restore system.State.output
    system.State.output = True
    assert run_program.call_count == 1
    assert list(run_program.call_args_list[0][0]) == sys.argv
