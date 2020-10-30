import os

import pytest
import runez
from mock import patch

from pickley import PackageSpec
from pickley.package import PythonVenv


BOGUS_PIP_SHOW = """
Files:
  bogus/metadata.json
"""

MGIT_PIP_SHOW = """
Name: mgit
Version: 1.0.0
Location: .
Files:
  mgit/metadata.json
"""

MGIT_PIP_METADATA = """
{"extensions": {"python.commands": {"wrap_console": ["mgit"]}}}
"""


def test_edge_cases(temp_cfg):
    pspec = PackageSpec(temp_cfg, "foo")
    with runez.CaptureOutput(dryrun=True) as logged:
        pspec.cfg._bundled_virtualenv_path = None
        venv = PythonVenv(pspec, "myvenv")
        assert str(venv) == "myvenv"
        assert not pspec.is_healthily_installed()
        assert "virtualenv.pyz myvenv" in logged

    with runez.CaptureOutput() as logged:
        runez.touch("dummy.whl")
        runez.ensure_folder(".", clean=True)
        assert "Cleaned 1 file from" in logged.pop()
        assert not os.path.exists("dummy.whl")


def simulated_run(*args, **_):
    if "--version" in args:
        return runez.program.RunResult("0.0.0", code=0)

    if "mgit" in args:
        return runez.program.RunResult(MGIT_PIP_SHOW, code=0)

    if "bogus" in args:
        return runez.program.RunResult(BOGUS_PIP_SHOW, code=0)


def test_entry_points(temp_cfg):
    runez.write("mgit/metadata.json", MGIT_PIP_METADATA)
    with patch("runez.run", side_effect=simulated_run):
        pspec = PackageSpec(temp_cfg, "mgit")
        venv = PythonVenv(pspec, folder="")
        assert venv.find_entry_points(pspec) == ["mgit"]

        pspec = PackageSpec(temp_cfg, "bogus")
        venv = PythonVenv(pspec, folder="")
        assert venv.find_entry_points(pspec) is None


def test_pip_fail(temp_cfg, logged):
    pspec = PackageSpec(temp_cfg, "bogus")
    venv = PythonVenv(pspec, folder="", python=temp_cfg.available_pythons.invoker)
    assert str(venv) == ""
    with patch("pickley.package.PythonVenv._run_pip", return_value=runez.program.RunResult("", "some\nerror", code=1)):
        with pytest.raises(SystemExit):
            venv.pip_install("foo")

        assert "some\nerror" == logged.stdout.pop()

    r = runez.program.RunResult("", "foo\nNo matching distribution for ...\nYou should consider upgrading pip", code=1)
    with patch("pickley.package.PythonVenv._run_pip", return_value=r):
        with pytest.raises(SystemExit):
            venv.pip_install("foo")

        assert not logged.stderr
        assert "No matching distribution for ..." in logged
        assert "You should consider" not in logged
