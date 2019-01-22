import os
import sys

import runez
from mock import patch

import pickley.settings
from pickley import system
from pickley.pypi import DEFAULT_PYPI, latest_pypi_version, pypi_url, request_get
from pickley.system import short

from .conftest import sample_path


LEGACY_SAMPLE = """
<html><head><title>Simple Index</title><meta name="api-version" value="2" /></head><body>

# 1.8.1 intentionally malformed
<a href="/pypi/packages/pypi-public/twine/twine-1.8.1!1-py2.py3-none-any.whl9#">twine-1.8.1-py2.py3-none-any.whl</a><br/>
<a href="/pypi/packages/pypi-public/twine/twine-1.8.1!1.tar.gz#">twine-1.8.1.tar.gz</a><br/>

<a href="/pypi/packages/pypi-public/twine/twine-1.9.0+local-py2.py3-none-any.whl#sha256=ac...">twine-1.9.0-py2.py3-none-any.whl</a><br/>
<a href="/pypi/packages/pypi-public/twine/twine-1.9.0+local.tar.gz#sha256=ff...">twine-1.9.0.tar.gz</a><br/>
<a href="/pypi/packages/pypi-public/twine/twine-1.9.1-py2.py3-none-any.whl#sha256=d3...">twine-1.9.1-py2.py3-none-any.whl</a><br/>
<a href="/pypi/packages/pypi-public/twine/twine-1.9.1.tar.gz#sha256=ca...">twine-1.9.1.tar.gz</a><br/>
</body></html>
"""

PRERELEASE_SAMPLE = """
<html><head><title>Simple Index</title><meta name="api-version" value="2" /></head><body>
<a href="/pypi/packages/pypi-public/black/black-18.3a0-py3-none-any.whl#sha256=..."</a><br/>
<a href="/pypi/packages/pypi-public/black/black-18.3a0.tar.gz#sha256=...">black-18.3a0.tar.gz</a><br/>
<a href="/pypi/packages/pypi-public/black/black-18.3a1-py3-none-any.whl#sha256=..."
"""

EXPECTED_REPRESENTATION = """
settings:
  base: {base}
  index: https://pypi.org/

  config:
    - cli: # empty
    - config.json:
      bundle:
        dev: [tox, twine]
        dev2: [tox, twine, pipenv]
      channel:
        stable:
          tox: 3.2.1
          twine: 1.9.0
      delivery:
        copy:
          dict_sample: this is just for testing dict() lookup
        venv: tox pipenv
      include: [custom.json]
      index: https://pypi.org/
    - custom.json:
      channel:
        alpha:
          virtualenv: 16.0.0
      include:
        - bogus.json
        - non-existent-config-file.json
      install_timeout: 2
      select:
        virtualenv:
          delivery: wrap
          packager: pex
      version_check_delay: 1
    - bogus.json: # empty
    - non-existent-config-file.json: # empty
"""


def test_custom_settings():
    s = pickley.settings.Settings(sample_path("custom"))
    s.load_config()

    assert str(s) == "[4] base: %s" % short(s.base.path)
    assert str(s.defaults) == "defaults"
    assert str(s.base) == "base: %s" % short(s.base.path)
    assert s.get_definition("") is None
    assert s.resolved_definition("") is None
    assert s.resolved_value("foo") is None

    p = s.base.full_path("foo/bar")
    assert s.base.relative_path(p) == "foo/bar"

    d = s.resolved_definition("delivery", package_name="dict_sample")
    assert str(d) == "config.json:delivery.copy"

    assert s.resolved_value("delivery", package_name="tox") == "venv"
    assert s.resolved_value("delivery", package_name="virtualenv") == "wrap"

    assert s.resolved_value("packager", package_name="tox") == system.VENV_PACKAGER
    assert s.resolved_value("packager", package_name="virtualenv") == "pex"

    assert s.resolved_packages("bundle:dev") == ["tox", "twine"]
    assert s.get_value("bundle.dev") == ["tox", "twine"]
    assert s.get_value("bundle.dev2") == ["tox", "twine", "pipenv"]

    old_width = pickley.settings.REPRESENTATION_WIDTH
    pickley.settings.REPRESENTATION_WIDTH = 40
    actual = s.represented(include_defaults=False).replace(short(s.base.path), "{base}")
    assert actual == EXPECTED_REPRESENTATION.strip()
    pickley.settings.REPRESENTATION_WIDTH = old_width

    s.cli.contents["packager"] = "copy"
    d = s.resolved_definition("packager")
    assert d.value == "copy"
    assert d.source is s.cli
    d = s.get_definition("packager")
    assert d.value == "copy"
    assert d.source is s.cli

    assert s.install_timeout == 2
    assert s.version_check_seconds == 60


def test_settings_base():
    old_program = system.PICKLEY_PROGRAM_PATH

    # Verify that .pickley/... part of base gets ignored
    base = sample_path("foo")
    system.PICKLEY_PROGRAM_PATH = os.path.join(base, pickley.settings.DOT_PICKLEY, "pickley-1.0.0", "bin", "pickley")
    s = pickley.settings.Settings()
    assert s.base.path == base

    # Convenience dev case
    base = sample_path(".venv", "bin", "pickley")
    system.PICKLEY_PROGRAM_PATH = base
    s = pickley.settings.Settings()
    assert s.base.path == sample_path(".venv", "root")

    system.PICKLEY_PROGRAM_PATH = old_program


@patch("runez.get_lines", return_value=None)
@patch("runez.run", side_effect=Exception)
def test_pypi(*_):
    assert latest_pypi_version(None, "") is None
    assert latest_pypi_version(None, "tox")

    with patch("pickley.pypi.request_get", return_value="{foo"):
        # 404
        assert latest_pypi_version(None, "foo").startswith("error: ")

    with patch("pickley.pypi.request_get", return_value='{"info": {"version": "1.0"}}'):
        assert latest_pypi_version(None, "foo") == "1.0"

    with patch("pickley.pypi.request_get", return_value=None):
        assert latest_pypi_version(None, "twine").startswith("error: ")

    with patch("pickley.pypi.request_get", return_value="foo"):
        assert latest_pypi_version(None, "twine").startswith("error: ")

    with patch("pickley.pypi.request_get", return_value=LEGACY_SAMPLE):
        assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi", "twine") == "1.9.1"
        assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi/{name}", "twine") == "1.9.1"

    with patch("pickley.pypi.request_get", return_value=PRERELEASE_SAMPLE):
        assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi", "black").startswith("error: ")

    with patch("pickley.pypi.urlopen", side_effect=Exception):
        # GET fails, and fallback curl also fails
        assert request_get("") is None

        with patch("runez.run", return_value="foo"):
            # GET fails, but curl succeeds
            assert request_get("") == "foo"

    e = Exception()
    e.code = 404
    with patch("pickley.pypi.urlopen", side_effect=e):
        # With explicit 404 we don't fallback to curl
        assert request_get("") is None

    with patch("runez.file.get_lines", return_value=["foo"]):
        assert pypi_url() == DEFAULT_PYPI

    with patch("runez.file.get_lines", return_value="[global]\nindex-url = foo".splitlines()):
        assert pypi_url() == "foo"


def test_add_representation():
    # Cover add_representation() edge cases
    r = []
    pickley.settings.add_representation(r, "")
    assert not r
    pickley.settings.add_representation(r, "foo")
    assert r == ["- foo"]


def simulated_is_executable(path):
    if not path:
        return False

    if path in ("/test/python3.6/bin/python", "/test/python3"):
        return True

    return os.path.isfile(path) and os.access(path, os.X_OK)


def simulated_which(program, *args, **kwargs):
    if program == "python3.6":
        return "/test/python3.6/bin/python"

    if program == "python3":
        return "/test/python3"

    return None


def simulated_run(program, *args, **kwargs):
    if program.startswith(sys.real_prefix) or program == sys.executable:
        return "Python 2.7.10"

    if program == "/usr/bin/python":
        return "Python 2.7.10"

    return None


@patch("runez.is_executable", side_effect=simulated_is_executable)
@patch("runez.which", side_effect=simulated_which)
@patch("runez.run", side_effect=simulated_run)
def test_python_installation(_, __, ___, temp_base):

    system.DESIRED_PYTHON = "/dev/null/foo"
    p = system.target_python(fatal=False)
    assert not p.is_valid
    assert p.shebang() == "/dev/null/foo"

    system.DESIRED_PYTHON = None
    assert system.target_python(fatal=False).is_valid

    assert not system.PythonInstallation("").is_valid

    p = system.PythonInstallation("foo")
    assert str(p) == "python 'foo'"
    assert not p.is_valid
    assert p.problem == "No python installation 'foo' found"
    assert p.program_name == "python 'foo'"

    p = system.PythonInstallation("pythonx")
    assert not p.is_valid
    # assert p.problem == "pythonx is not installed"
    assert p.program_name == "pythonx"

    p = system.PythonInstallation("/usr/bin/python")
    assert str(p) == "/usr/bin/python [2.7]"
    assert p.is_valid
    assert p.problem is None
    assert p.program_name == "python2.7"
    assert p.short_name == "py27"
    assert p.executable == "/usr/bin/python"
    assert p.shebang(universal=True) == "/usr/bin/env python"
    assert p.shebang() == "/usr/bin/python"

    p = system.PythonInstallation("3.6")
    assert str(p) == "/test/python3.6/bin/python [3.6]"
    assert p.is_valid
    assert p.problem is None
    assert p.program_name == "python3.6"
    assert p.short_name == "py36"
    assert p.executable == "/test/python3.6/bin/python"
    assert p.shebang() == "/usr/bin/env python3.6"

    system.SETTINGS.cli.contents["python_installs"] = temp_base
    runez.touch("foo")
    runez.touch("python3.5")
    runez.touch("python3.7")

    p = system.PythonInstallation("python3")
    assert not p.is_valid
    assert p.problem == "'/test/python3' is not a valid python installation"
    assert p.program_name == "python3"

    p = system.PythonInstallation("py3.7")
    assert not p.is_valid
    assert p.problem == "python3.7 is not installed"

    runez.delete("python3.7")
    runez.touch("3.7.0/bin/python")
    runez.make_executable("3.7.0/bin/python")
    p = system.PythonInstallation("3.7")
    assert p.is_valid
    assert p.short_name == "py37"
    assert p.executable == os.path.join(temp_base, "3.7.0/bin/python")
