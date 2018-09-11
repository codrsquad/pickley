import os
import sys

import pytest
from mock import patch

from pickley import system
from pickley.cli import bootstrap
from pickley.context import CaptureOutput
from pickley.lock import PingLockException
from pickley.settings import SETTINGS


@patch("pickley.system.relaunch")
def test_bootstrap(_, temp_base):
    SETTINGS.cli.contents["delivery"] = "wrap"
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


@patch("pickley.package.Packager.internal_install", side_effect=PingLockException(".ping"))
def test_bootstrap_in_progress(_):
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
