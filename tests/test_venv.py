import os
import sys

import runez
from mock import patch

from pickley import CFG, PackageSpec
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
    venv = PythonVenv("", CFG.find_python(), None)
    runez.touch("dummy.whl")
    shebang = venv.get_shebang(".")
    assert shebang.endswith("python%s" % sys.version_info[0])
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
        venv = PythonVenv("", CFG.find_python(), None)
        assert venv.find_entry_points(pspec) == ["mgit"]

        pspec = PackageSpec(CFG, "bogus")
        venv = PythonVenv("", CFG.find_python(), None)
        assert venv.find_entry_points(pspec) is None
