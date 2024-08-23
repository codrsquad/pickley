import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import runez

from pickley import PackageSpec
from pickley.bstrap import DOT_META
from pickley.package import PythonVenv


def simulated_run(*args, **_):
    if "show" in args:
        v = "1.0" if "ansible-base" in args else "9.0"
        return runez.program.RunResult(f"Version: {v}\nLocation: .", code=0)

    return runez.program.RunResult("", code=0)


def simulate_venv(path):
    py = Path(DOT_META) / path / "bin/python"
    runez.write(py, "echo ok")
    runez.make_executable(py)


@pytest.mark.skipif(sys.version_info[:2] <= (3, 7), reason="to keep test case simple (uv only)")
def test_entry_points(cli):
    simulate_venv("ansible-1.0")
    simulate_venv("ansible-9.0")
    simulate_venv("tox-uv-9.0")
    runez.write("ansible_base-1.0.dist-info/RECORD", "../../bin/ansible-b")
    runez.write("ansible_core-9.0.dist-info/RECORD", "../../bin/ansible-c")
    runez.write("tox_uv-9.0.dist-info/entry_points.txt", "[tox]\ntox-uv = tox_uv.plugin")
    with patch("runez.run", side_effect=simulated_run):
        cli.run("-n install ansible==1.0")
        assert cli.succeeded
        assert "Would wrap ansible-b -> .pk/ansible-1.0/bin/ansible-b" in cli.logged
        assert "Installed ansible v1.0" in cli.logged

        cli.run("-n install ansible==9.0")
        assert cli.succeeded
        assert "Would wrap ansible-c -> .pk/ansible-9.0/bin/ansible-c" in cli.logged
        assert "Installed ansible v9.0" in cli.logged

        cli.run("-n install tox-uv==9.0")
        assert cli.succeeded
        assert "Would wrap tox -> .pk/tox-uv-9.0/bin/tox" in cli.logged


def test_pip_fail(temp_cfg, logged):
    pspec = PackageSpec(temp_cfg, "bogus")
    venv = PythonVenv(".", pspec, use_pip=True)
    assert str(venv) == "."
    with patch("pickley.package.PythonVenv.run_python", return_value=runez.program.RunResult("", "some\nerror", code=1)):
        with pytest.raises(SystemExit):
            venv.pip_install("foo")

        assert logged.stdout.pop() == "some\nerror"

    r = runez.program.RunResult("", "foo\nNo matching distribution for ...\nYou should consider upgrading pip", code=1)
    with patch("pickley.package.PythonVenv.run_python", return_value=r):
        with pytest.raises(SystemExit):
            venv.pip_install("foo")

        assert "No matching distribution for ..." in logged
        assert "You should consider" not in logged
