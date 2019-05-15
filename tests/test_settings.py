import os
import sys

import pytest
import runez
from mock import patch

import pickley.settings
from pickley import system
from pickley.pypi import latest_pypi_version, request_get
from pickley.settings import Settings, short

from .conftest import sample_path


LEGACY_SAMPLE = """
<html><head><title>Simple Index</title><meta name="api-version" value="2" /></head><body>

# 1.8.1 intentionally malformed
<a href="/pypi/shell-functools/shell_functools-1.8.1!1-py2.py3-none-any.whl9#">shell_functools-1.8.1-py2.py3-none-any.whl</a><br/>
<a href="/pypi/shell-functools/shell-functools-1.8.1!1.tar.gz#">shell-functools-1.8.1.tar.gz</a><br/>

<a href="/pypi/shell-functools/shell_functools-1.9.0+local-py2.py3-none-any.whl#sha...">shell_functools-1.9.0-py2.py3-none-any.whl</a><br/>
<a href="/pypi/shell-functools/shell-functools-1.9.0+local.tar.gz#sha256=ff...">shell-functools-1.9.0.tar.gz</a><br/>
<a href="/pypi/shell-functools/shell_functools-1.9.1-py2.py3-none-any.whl#sha256=d3...">shell_functools-1.9.1-py2.py3-none-any.whl</a><br/>
<a href="/pypi/shell-functools/shell-functools-1.9.1.tar.gz#sha256=ca...">shell-functools-1.9.1.tar.gz</a><br/>
</body></html>
"""

PRERELEASE_SAMPLE = """
<html><head><title>Simple Index</title><meta name="api-version" value="2" /></head><body>
<a href="/pypi/packages/pypi-public/black/black-18.3a0-py3-none-any.whl#sha256=..."</a><br/>
<a href="/pypi/packages/pypi-public/black/black-18.3a0.tar.gz#sha256=...">black-18.3a0.tar.gz</a><br/>
<a href="/pypi/packages/pypi-public/black/black-18.3a1-py3-none-any.whl#sha256=..."
"""

UNKNOWN_VERSIONING_SAMPLE = """
<html><head><title>Simple Index</title><meta name="api-version" value="2" /></head><body>
<a href="/pypi/packages/pypi-private/someproj/someproj-1.3.0_custom-py3-none-any.whl#sha256=..."</a><br/>
<a href="/pypi/packages/pypi-private/someproj/someproj-1.3.0_custom.tar.gz#sha256=...">someproj-1.3.0_custom.tar.gz</a><br/>
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
          dict-sample: this is just for testing dict() lookup
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


def check_specs(text, expected):
    packages = system.resolved_package_specs(text)
    names = [p.dashed for p in packages]
    assert names == expected


def test_custom_settings(temp_base):
    system.SETTINGS.set_base(sample_path("custom"))
    stgs = system.SETTINGS
    assert isinstance(stgs, Settings)
    system.SETTINGS.load_config()

    check_specs("bundle:dev", ["tox", "twine"])
    check_specs("bundle:dev2", ["tox", "twine", "pipenv"])
    check_specs("bundle:dev bundle:dev2", ["tox", "twine", "pipenv"])
    check_specs("pipenv bundle:dev bundle:dev2", ["pipenv", "tox", "twine"])

    assert str(stgs) == "[4] base: %s" % short(stgs.base.path)
    assert str(stgs.defaults) == "defaults"
    assert str(stgs.base) == "base: %s" % short(stgs.base.path)
    assert stgs.get_definition("") is None
    assert stgs.resolved_definition("") is None
    assert stgs.resolved_value("foo") is None

    p = stgs.base.full_path("foo/bar")
    assert stgs.base.relative_path(p) == "foo/bar"

    d = stgs.resolved_definition("delivery", package_spec=system.PackageSpec("dict_sample"))
    assert str(d) == ".pickley/config.json:delivery.copy"

    assert stgs.resolved_value("delivery", package_spec=system.PackageSpec("tox")) == "venv"
    assert stgs.resolved_value("delivery", package_spec=system.PackageSpec("virtualenv")) == "wrap"

    assert stgs.resolved_value("packager", package_spec=system.PackageSpec("tox")) == system.VENV_PACKAGER
    assert stgs.resolved_value("packager", package_spec=system.PackageSpec("virtualenv")) == "pex"

    with runez.Anchored(system.SETTINGS.meta.path):
        old_width = pickley.settings.REPRESENTATION_WIDTH
        pickley.settings.REPRESENTATION_WIDTH = 40
        actual = stgs.represented(include_defaults=False).replace(short(stgs.base.path), "{base}")
        assert actual == EXPECTED_REPRESENTATION.strip()
        pickley.settings.REPRESENTATION_WIDTH = old_width

    stgs.cli.contents["packager"] = "copy"
    d = stgs.resolved_definition("packager")
    assert d.value == "copy"
    assert d.source is stgs.cli
    d = stgs.get_definition("packager")
    assert d.value == "copy"
    assert d.source is stgs.cli

    assert stgs.install_timeout == 2
    assert stgs.version_check_seconds == 60


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

    with patch("pickley.settings.get_user_index", return_value="https://example.net/pypi"):
        s = pickley.settings.Settings()
        assert s.base.path == sample_path(".venv", "root")
        assert s.index == "https://example.net/pypi"

    system.PICKLEY_PROGRAM_PATH = old_program

    with pytest.raises(Exception):
        system.PackageSpec("some.bogus name")


@patch("runez.get_lines", return_value=None)
@patch("runez.run", side_effect=Exception)
def test_pypi(*_):
    pyyaml = system.PackageSpec("PyYAML.Yandex==1.0")
    assert pyyaml.dashed == "pyyaml-yandex"
    assert pyyaml.specced == "pyyaml-yandex==1.0"
    assert pyyaml.pythonified == "PyYAML_Yandex"
    assert pyyaml.original == "PyYAML.Yandex"
    assert pyyaml.version_part("PyYAML.Yandex-3.11.1.tar.gz") == "3.11.1.tar.gz"
    assert pyyaml.version_part("PyYAML_Yandex-1.2.whl") == "1.2.whl"
    assert pyyaml.version_part("pyyaml_Yandex-1.2.whl") == "1.2.whl"
    assert pyyaml.version_part("PyYAML.Yandex-3.11nikicat.tar.gz") == "3.11nikicat.tar.gz"
    assert pyyaml.version_part("PyYAML.Yandex-3") == "3"
    assert pyyaml.version_part("pyyaml-yandex-3") == "3"
    assert pyyaml.version_part("PyYAML.Yandex-") is None
    assert pyyaml.version_part("PyYAML.Yandex-foo-3.11") is None

    tox = system.PackageSpec("tox")
    foo = system.PackageSpec("foo")
    black = system.PackageSpec("black")
    twine = system.PackageSpec("twine")
    shell_functools = system.PackageSpec("shell-functools")

    assert latest_pypi_version(None, tox)

    with patch("pickley.pypi.request_get", return_value="{foo"):
        # 404
        assert latest_pypi_version(None, foo).startswith("error: ")

    with patch("pickley.pypi.request_get", return_value='{"info": {"version": "1.0"}}'):
        assert latest_pypi_version(None, foo) == "1.0"

    with patch("pickley.pypi.request_get", return_value=None):
        assert latest_pypi_version(None, twine).startswith("error: ")

    with patch("pickley.pypi.request_get", return_value="foo"):
        assert latest_pypi_version(None, twine).startswith("error: ")

    with patch("pickley.pypi.request_get", return_value=LEGACY_SAMPLE):
        assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi", shell_functools) == "1.9.1"
        assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi/{name}", shell_functools) == "1.9.1"

    with patch("pickley.pypi.request_get", return_value=PRERELEASE_SAMPLE):
        assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi", black).startswith("error: ")

    with patch("pickley.pypi.request_get", return_value=UNKNOWN_VERSIONING_SAMPLE):
        # Unknown version: someproj-1.3.0_custom
        assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi", black).startswith("error: ")

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
