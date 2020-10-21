import os

import pytest
import runez
from mock import patch

from pickley import PackageSpec
from pickley.cli import CFG
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


def test_shebang(temp_folder, logged):
    # Exercise shebang
    pspec = PackageSpec(CFG, "foo")
    venv = PythonVenv(pspec, folder="", python=CFG.find_python())
    assert str(venv) == ""
    runez.touch("dummy.whl")
    runez.ensure_folder(".", clean=True)
    assert "Cleaned 1 file from" in logged.pop()
    assert not os.path.exists("dummy.whl")


def simulated_run(*args, **_):
    if args[-1] == "mgit":
        return runez.program.RunResult(MGIT_PIP_SHOW, code=0)

    if args[-1] == "bogus":
        return runez.program.RunResult(BOGUS_PIP_SHOW, code=0)


def test_entry_points(temp_folder):
    runez.write("mgit/metadata.json", MGIT_PIP_METADATA)
    with patch("runez.run", side_effect=simulated_run):
        pspec = PackageSpec(CFG, "mgit")
        venv = PythonVenv(pspec, folder="")
        assert venv.find_entry_points(pspec) == ["mgit"]

        pspec = PackageSpec(CFG, "bogus")
        venv = PythonVenv(pspec, folder="")
        assert venv.find_entry_points(pspec) is None


def test_pip_fail(logged):
    pspec = PackageSpec(CFG, "bogus")
    venv = PythonVenv(pspec, folder="", python=CFG.available_pythons.invoker)
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
