import os

from mock import patch

from pickley import short, system
from pickley.pypi import latest_pypi_version
from pickley.settings import add_representation, JsonSerializable, same_type, Settings

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
  meta: {base}/.pickley
  index: https://pypi.org/
  config:
    - cli: # empty
    - {base}/.pickley.json:
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
    - {base}/custom.json:
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
      install_timeout: 120
      select:
        virtualenv:
          delivery: wrap
          packager: pex
      version_check_delay: 60
    - {base}/bogus.json: # empty
    - {base}/bogus2.json: # empty
    - {base}/bogus3.json: # empty
    - {base}/bogus4.json: # empty
    - {base}/bogus5.json: # empty
    - {base}/bogus6.json: # empty
    - {base}/bogus7.json: # empty
    - {base}/bogus8.json: # empty
    - {base}/bogus9.json: # empty
    - defaults:
      default:
        channel: latest
        delivery: symlink
        install_timeout: 1800
        packager: venv
        python: {python}
        version_check_delay: 600
"""


def test_custom_settings():
    s = Settings(sample_path())
    s.add(system.config_paths(True))

    assert str(s) == "[11] base: %s" % short(s.base.path)
    assert str(s.defaults) == "defaults"
    assert str(s.base) == "base: %s" % short(s.base.path)
    assert s.get_definition(None) is None
    assert s.resolved_definition(None) is None
    assert s.resolved_value("foo") is None

    p = s.base.full_path("foo/bar")
    assert s.base.relative_path(p) == "foo/bar"

    d = s.resolved_definition("delivery", package_name="dict_sample")
    assert str(d) == "%s/.pickley.json:delivery.copy" % short(s.base.path)

    assert s.resolved_value("delivery", package_name="tox") == "venv"
    assert s.resolved_value("delivery", package_name="virtualenv") == "wrap"

    assert s.resolved_value("packager", package_name="tox") == system.DEFAULT_PACKAGER
    assert s.resolved_value("packager", package_name="virtualenv") == "pex"

    assert s.resolved_packages("bundle:dev") == ["tox", "twine"]
    assert s.get_value("bundle.dev") == ["tox", "twine"]
    assert s.get_value("bundle.dev2") == ["tox", "twine", "pipenv"]

    expected = EXPECTED_REPRESENTATION.format(base=short(s.base.path), python=short(system.PYTHON)).strip()
    assert s.represented().strip() == expected

    s.cli.contents["packager"] = "copy"
    d = s.resolved_definition("packager")
    assert d.value == "copy"
    assert d.source is s.cli
    d = s.get_definition("packager")
    assert d.value == "copy"
    assert d.source is s.cli

    assert s.install_timeout == 120
    assert s.version_check_delay == 60


def test_settings_base():
    old_program = system.PROGRAM

    # Verify that .pickley/... part of base gets ignored
    base = sample_path("foo")
    system.PROGRAM = os.path.join(base, ".pickley", "bar")
    s = Settings()
    assert s.base.path == base

    # Convenience dev case
    base = sample_path(".venv", "bin", "pickley")
    system.PROGRAM = base
    s = Settings()
    assert s.base.path == sample_path(".venv", "root")

    system.PROGRAM = old_program


def test_same_type():
    assert same_type(None, None)
    assert not same_type(None, "")
    assert same_type("foo", "bar")
    assert same_type("foo", u"bar")
    assert same_type(["foo"], [u"bar"])


def test_pypi():
    assert latest_pypi_version(None, None) is None
    assert latest_pypi_version(None, "tox")


@patch("pickley.pypi.request_get", return_value="{foo")
def test_pypi_bad_response(*_):
    assert latest_pypi_version(None, "foo") is None


@patch("pickley.pypi.request_get", return_value=LEGACY_SAMPLE)
def test_pypi_legacy(*_):
    assert latest_pypi_version("https://pypi-mirror.mycompany.net/pypi", "twine") == "1.9.1"


def test_add_representation():
    # Cover add_representation() edge cases
    r = []
    add_representation(r, None)
    assert not r
    add_representation(r, "foo")
    assert r == ["- foo"]


def test_serialization():
    j = JsonSerializable()
    assert str(j) == "no source"
    j.save()  # no-op
    j.set_from_dict(None, source="test")
    j.some_list = []
    j.some_string = []
    j.set_from_dict(dict(foo="bar", some_list="some_value", some_string="some_value"), source="test")
    assert not j.some_list
    assert not hasattr(j, "foo")
    assert not j.some_string == "some_value"
    j.reset()
    assert not j.some_string

    j = JsonSerializable.from_json(None)
    assert str(j) == "no source"

    j = JsonSerializable.from_json("/dev/null/foo")
    assert str(j) == "/dev/null/foo"
    j.save()  # Warns: Couldn't save...


def test_duration():
    assert system.to_int(None, default=60) == 60
    assert system.to_int("") is None
    assert system.to_int("foo") is None
    assert system.to_int("1m") is None

    assert system.to_int(50) == 50
    assert system.to_int("50") == 50
