import os

from mock import patch

from pickley import system
from pickley.pypi import latest_pypi_version, request_get
from pickley.settings import add_representation, DEFAULT_INSTALL_TIMEOUT, DEFAULT_VERSION_CHECK_SECONDS
from pickley.settings import DOT_PICKLEY, JsonSerializable, same_type, Settings
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
EXPECTED_REPRESENTATION = """
settings:
  base: {base}
  index: https://pypi.org/

  config:
    - cli: # empty
    - {base}/.pickley/config.json:
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
    - {base}/.pickley/custom.json:
      channel:
        alpha:
          virtualenv: 16.0.0
      include:
        - bogus.json
        - bogus2.json
        - bogus3.json
        - bogus4.json
        - bogus5.json
        - bogus6.json
        - bogus7.json
        - bogus8.json
        - bogus9.json
      install_timeout: 2
      select:
        virtualenv:
          delivery: wrap
          packager: pex
      version_check_seconds: 60
    - {base}/.pickley/bogus.json: # empty
    - {base}/.pickley/bogus2.json: # empty
    - {base}/.pickley/bogus3.json: # empty
    - {base}/.pickley/bogus4.json: # empty
    - {base}/.pickley/bogus5.json: # empty
    - {base}/.pickley/bogus6.json: # empty
    - {base}/.pickley/bogus7.json: # empty
    - {base}/.pickley/bogus8.json: # empty
    - {base}/.pickley/bogus9.json: # empty
    - defaults:
      default:
        channel: %s
        delivery: %s
        install_timeout: %s
        packager: %s
        python: %s
        version_check_seconds: %s
""" % (
    system.LATEST_CHANNEL,
    system.DEFAULT_DELIVERY,
    DEFAULT_INSTALL_TIMEOUT,
    system.VENV_PACKAGER,
    short(system.PYTHON),
    DEFAULT_VERSION_CHECK_SECONDS,
)


def test_custom_settings():
    s = Settings(sample_path("custom"))
    s.load_config()

    assert str(s) == "[11] base: %s" % short(s.base.path)
    assert str(s.defaults) == "defaults"
    assert str(s.base) == "base: %s" % short(s.base.path)
    assert s.get_definition("") is None
    assert s.resolved_definition("") is None
    assert s.resolved_value("foo") is None

    p = s.base.full_path("foo/bar")
    assert s.base.relative_path(p) == "foo/bar"

    d = s.resolved_definition("delivery", package_name="dict_sample")
    assert str(d) == "%s/config.json:delivery.copy" % short(s.meta.path)

    assert s.resolved_value("delivery", package_name="tox") == "venv"
    assert s.resolved_value("delivery", package_name="virtualenv") == "wrap"

    assert s.resolved_value("packager", package_name="tox") == system.VENV_PACKAGER
    assert s.resolved_value("packager", package_name="virtualenv") == "pex"

    assert s.resolved_packages("bundle:dev") == ["tox", "twine"]
    assert s.get_value("bundle.dev") == ["tox", "twine"]
    assert s.get_value("bundle.dev2") == ["tox", "twine", "pipenv"]

    expected = EXPECTED_REPRESENTATION.format(base=short(s.base.path)).strip()
    assert s.represented().strip() == expected

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
    system.PICKLEY_PROGRAM_PATH = os.path.join(base, DOT_PICKLEY, "pickley-1.0.0", "bin", "pickley")
    s = Settings()
    assert s.base.path == base

    # Convenience dev case
    base = sample_path(".venv", "bin", "pickley")
    system.PICKLEY_PROGRAM_PATH = base
    s = Settings()
    assert s.base.path == sample_path(".venv", "root")

    system.PICKLEY_PROGRAM_PATH = old_program


def test_same_type():
    assert same_type(None, None)
    assert not same_type(None, "")
    assert same_type("foo", "bar")
    assert same_type("foo", u"bar")
    assert same_type(["foo"], [u"bar"])


@patch("pickley.system.run_program", side_effect=Exception)
def test_pypi(_):
    assert latest_pypi_version(None, "") is None
    assert latest_pypi_version(None, "tox")

    with patch("pickley.pypi.request_get", return_value="{foo"):
        # 404
        assert latest_pypi_version(None, "foo") == "can't determine latest version from 'https://pypi.org/pypi/foo/json'"

    with patch("pickley.pypi.request_get", return_value=None):
        assert latest_pypi_version(None, "twine").startswith("can't determine latest version")

    with patch("pickley.pypi.request_get", return_value=LEGACY_SAMPLE):
        assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi", "twine") == "1.9.1"

    with patch("pickley.pypi.urlopen", side_effect=Exception):
        # GET fails, and fallback curl also fails
        assert request_get("") is None

        with patch("pickley.system.run_program", return_value="foo"):
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
    add_representation(r, "")
    assert not r
    add_representation(r, "foo")
    assert r == ["- foo"]


def test_serialization():
    j = JsonSerializable()
    assert str(j) == "no source"
    j.save()  # no-op
    j.set_from_dict({}, source="test")
    j.some_list = []
    j.some_string = []
    j.set_from_dict(dict(foo="bar", some_list="some_value", some_string="some_value"), source="test")
    assert not j.some_list
    assert not hasattr(j, "foo")
    assert not j.some_string == "some_value"
    j.reset()
    assert not j.some_string

    j = JsonSerializable.from_json("")
    assert str(j) == "no source"

    j = JsonSerializable.from_json("/dev/null/foo")
    assert str(j) == "/dev/null/foo"
    j.save()  # Warns: Couldn't save...


def test_duration():
    assert system.to_int("", default=60) == 60
    assert system.to_int("") is None
    assert system.to_int("foo") is None
    assert system.to_int("1m") is None

    assert system.to_int(50) == 50
    assert system.to_int("50") == 50
