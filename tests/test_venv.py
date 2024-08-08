from unittest.mock import patch

import pytest
import runez

from pickley import PackageSpec, RawConfig
from pickley.package import PythonVenv

PIP_SHOW_OUTPUT = """
Name: ansible
Version: 1.0.0
Location: .
Files:
  ../bin/ansible
  ../bin/ansible_completer
  ansible.dist-info/metadata.json
  foo/__pycache__/bar.py
  foo/bar.py
  foo/bar.pyc
"""


def simulated_run(*args, **_):
    if "ansible-core" in args:
        return runez.program.RunResult(PIP_SHOW_OUTPUT, code=0)

    if "no-location" in args:
        return runez.program.RunResult("Files:\n  no-location.dist-info/metadata.json", code=0)

    return runez.program.RunResult("", code=1)


# def test_entry_points(temp_cfg):
#     with runez.CaptureOutput(dryrun=True):
#         pspec = PackageSpec(temp_cfg, "mgit")
#         contents = PackageContents(PythonVenv("", pspec, create=False))
#         assert str(contents) == "mgit [None]"
#         assert str(contents.bin) == "bin [1 files]"
#         assert contents.entry_points == {"mgit": "dryrun"}
#
#     runez.write("ansible.dist-info/metadata.json", '{"extensions": {"python.commands": {"wrap_console": ["ansible"]}}}')
#     with patch("runez.run", side_effect=simulated_run):
#         pspec = PackageSpec(temp_cfg, "ansible==5.0")  # Used to trigger ansible edge case
#         contents = PackageContents(PythonVenv("", pspec, create=False))
#         assert str(contents) == "ansible==5.0 [.]"
#         assert str(contents.bin) == "bin [0 files]"
#         assert str(contents.completers) == "bin [1 files]"
#         assert str(contents.dist_info) == "ansible.dist-info [1 files]"
#         assert contents.entry_points == ["ansible"]
#         assert str(contents.files) == " [1 files]"
#         assert contents.files.files.get("foo/bar.py")
#         assert contents.info == {"Name": "ansible", "Version": "1.0.0", "Location": "."}
#         assert contents.location == "."
#
#         contents = PackageContents(PythonVenv("", PackageSpec(temp_cfg, "no-location"), create=False))
#         assert contents.files is None
#         assert contents.entry_points is None
#
#         contents = PackageContents(PythonVenv("", PackageSpec(temp_cfg, "no-such-package"), create=False))
#         assert contents.files is None
#         assert contents.entry_points is None


# def test_pip_fail(temp_cfg, logged):
#     pspec = PackageSpec(temp_cfg, "bogus")
#     venv = PythonVenv("", pspec, create=False)
#     assert str(venv) == ""
#     with patch("pickley.package.PythonVenv.run_pip", return_value=runez.program.RunResult("", "some\nerror", code=1)):
#         with pytest.raises(SystemExit):
#             venv.pip_install("foo")
#
#         assert logged.stdout.pop() == "some\nerror"
#
#     r = runez.program.RunResult("", "foo\nNo matching distribution for ...\nYou should consider upgrading pip", code=1)
#     with patch("pickley.package.PythonVenv.run_pip", return_value=r):
#         with pytest.raises(SystemExit):
#             venv.pip_install("foo")
#
#         assert "No matching distribution for ..." in logged
#         assert "You should consider" not in logged
