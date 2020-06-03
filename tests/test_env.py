import os

import runez
from mock import patch

from pickley import PickleyConfig
from pickley.env import PythonFromPath, std_python_name


def test_standardizing():
    assert std_python_name(None) == "python"
    assert std_python_name("") == "python"
    assert std_python_name("2") == "python2"
    assert std_python_name("3") == "python3"
    assert std_python_name("py3") == "python3"
    assert std_python_name("python3") == "python3"
    assert std_python_name("python 3") == "python3"

    assert std_python_name("37") == "python3.7"
    assert std_python_name("3.7") == "python3.7"
    assert std_python_name("py37") == "python3.7"
    assert std_python_name("python37") == "python3.7"
    assert std_python_name("python 37") == "python3.7"

    assert std_python_name("377") == "python3.7.7"
    assert std_python_name("3.7.7") == "python3.7.7"
    assert std_python_name("py377") == "python3.7.7"
    assert std_python_name("python  377") == "python3.7.7"

    assert std_python_name("foo") == "foo"
    assert std_python_name("py 37") == "py 37"
    assert std_python_name("3777") == "3777"
    assert std_python_name("pyth37") == "pyth37"
    assert std_python_name("/foo/python2.7") == "/foo/python2.7"


def mk_python(path, version, executable=True):
    runez.write(path, "#!/bin/bash\necho %s\n" % version)
    if executable:
        runez.make_executable(path)


def test_searching(temp_folder):
    cfg = PickleyConfig()
    cfg.set_base(".")
    cfg.configs[0].values["pyenv"] = "pyenv-folder"

    # Simulate a few dummy python installations
    mk_python("p1/python", "2.5.0")
    mk_python("p2/python3", "2.9.1")  # picking an unlikely version, for testing
    mk_python("pyenv-folder/versions/2.9.2/bin/python", "2.9.2")
    mk_python("pyenv-folder/versions/2.9.3/bin/python", "2.9.3", executable=False)

    mk_python("dummy/python", "0.1.2")
    p = PythonFromPath("dummy/python")
    assert p.executable == "dummy/python"
    assert p.problem == "--version did not yield major version component"
    assert p.version == "0.1.2"

    runez.write("dummy/python2", "#!/bin/bash\nexit 1\n")
    runez.make_executable("dummy/python2")
    p = PythonFromPath("dummy/python2")
    assert p.executable == "dummy/python2"
    assert p.problem == "does not respond to --version"
    assert not p.version

    p = PythonFromPath("p1/python", version="3.7.1")  # Simulate 3.7.1
    assert p.needs_virtualenv

    with patch.dict(os.environ, {"PATH": "p1:p2"}, clear=True):
        invoker = cfg.find_python()
        assert cfg.find_python(None) is invoker
        assert cfg.find_python("python") is invoker
        assert invoker.is_invoker

        p1 = cfg.find_python("/usr/bin/python")
        p2 = cfg.find_python("/usr/bin/python")  # Python install references are cached
        assert p1 is p2
        assert cfg.find_python(p1) is p1
        assert str(p1) == "/usr/bin/python"
        assert p1.satisfies("/usr/bin/python")

        p = cfg.find_python("python2.9")
        assert not p.problem
        assert p.version == "2.9.2"
        p.satisfies("py31")
        p.satisfies("py-2.9.2")
        assert not p.is_invoker
        assert cfg.find_python("python2.9") is p  # Now cached
        assert cfg.find_python("py29") is p  # Standard name is tried too

        p = cfg.find_python("python2.9.9")
        assert p.executable == "python2.9.9"
        assert p.problem == "not available"
        assert cfg.find_python("python2.9.9") is p  # Now cached, even if problematic
