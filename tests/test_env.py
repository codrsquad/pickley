import os

import runez
from mock import patch

from pickley import PickleyConfig
from pickley.env import InvokerPython, PythonFromPath, std_python_name, UnknownPython
from pickley.package import virtualenv_zipapp


def mocked_invoker(**sysattrs):
    major = sysattrs.pop("major", 3)
    sysattrs.setdefault("base_prefix", None)
    sysattrs.setdefault("real_prefix", None)
    sysattrs.setdefault("version_info", (major, 7, 1))
    with patch("pickley.env.sys") as mocked:
        for k, v in sysattrs.items():
            setattr(mocked, k, v)

        return InvokerPython()


def test_invoker():
    # Linux case with py3
    with patch("runez.is_executable", return_value=True):
        p = mocked_invoker(base_prefix="/usr")
        assert p.executable == "/usr/bin/python3"
        assert p.major == 3
        assert "2.7" not in virtualenv_zipapp(p)

    # Linux case without py3
    with patch("runez.is_executable", return_value=True):
        p = mocked_invoker(major=2, real_prefix="/usr")
        assert p.executable == "/usr/bin/python2"
        assert p.major == 2
        assert "2.7" in virtualenv_zipapp(p)

    # Linux case without py3 or py2 (but only /usr/bin/python)
    with patch("runez.is_executable", side_effect=lambda x: "python2" not in x):
        p = mocked_invoker(major=2, real_prefix="/usr")
        assert p.executable == "/usr/bin/python"
        assert p.major == 2

    # Use sys.executable when prefix can't be used to determine invoker
    with patch("runez.is_executable", return_value=False):
        p = mocked_invoker(major=2, executable="/foo")
        assert p.executable == "/foo"
        assert p.major == 2

    # Bogus prefix: fall back to sys.executable
    with patch("runez.is_executable", return_value=False):
        p = mocked_invoker(real_prefix="/dev/null", executable="/foo")
        assert p.executable == "/foo"
        assert p.major == 3

    # OSX py2 case
    p = mocked_invoker(major=2, real_prefix="/System/Library/Frameworks/Python.framework/Versions/2.7")
    assert p.executable == "/usr/bin/python"
    assert p.major == 2

    # OSX py3 case
    p = mocked_invoker(base_prefix="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.7")
    assert p.executable == "/usr/bin/python3"
    assert p.major == 3


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
    mk_python("pythonrc", "Python 2.7.18rc1")
    p = PythonFromPath("pythonrc")
    assert p.major == 2
    assert p.minor == 7
    assert p.patch == 18
    assert p.needs_virtualenv

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

    ap = cfg.available_pythons
    with patch.dict(os.environ, {"PATH": "p1:p2"}, clear=True):
        invoker = ap.find_python()
        assert ap.find_python(None) is invoker
        assert ap.find_python("python") is invoker
        assert invoker.is_invoker

        p1 = ap.find_python("/usr/bin/python")
        p2 = ap.find_python("/usr/bin/python")  # Python install references are cached
        assert p1 != invoker
        assert p1 is p2
        assert p1 == p2
        assert ap.find_python(p1) is p1
        assert str(p1) == "/usr/bin/python"
        assert p1.satisfies("/usr/bin/python")

        p29 = ap.find_python("python2.9")
        assert not p29.problem
        assert p29.version == "2.9.2"
        p29.satisfies("py31")
        p29.satisfies("py-2.9.2")
        assert not p29.is_invoker
        assert ap.find_python("python2.9") is p29  # Now cached
        assert ap.find_python("py29") is p29  # Standard name is tried too

        p299 = ap.find_python("python2.9.9")
        assert p299.executable == "python2.9.9"
        assert p299.problem == "not available"
        assert ap.find_python("python2.9.9") is p299  # Now cached, even if problematic
        assert p29 != p299

        # Edge cases
        pb1 = UnknownPython("b1")
        pb2 = UnknownPython("b2")
        assert pb1 != "foo"
        assert pb1 != pb2
        pb1.executable = None
        assert pb1 != pb2
        pb2.executable = None
        assert pb1 == pb2
