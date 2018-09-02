import os

from mock import patch

from pickley import system
from pickley.pypi import latest_pypi_version
from pickley.settings import same_type, Settings

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


def test_custom_settings():
    config = sample_path("pickley.json")
    s = Settings(".", config=[config])

    d = s.version("tox")
    assert d.channel == "stable"
    assert d.value == "3.2.1"

    d = s.version("virtualenv")
    assert d.channel == "alpha"
    assert d.value == "16.0.0"

    assert s.resolved_value("delivery", package_name="tox") == system.DEFAULT_DELIVERY
    assert s.resolved_value("delivery", package_name="virtualenv") == "wrap"

    assert s.resolved_value("packager", package_name="tox") == system.DEFAULT_PACKAGER
    assert s.resolved_value("packager", package_name="virtualenv") == "pex"

    print()


def test_same_type():
    assert same_type(None, None)
    assert not same_type(None, "")
    assert same_type("foo", "bar")
    assert same_type("foo", u"bar")
    assert same_type(["foo"], [u"bar"])


def test_settings():
    s = Settings(base=os.path.dirname(__file__))

    assert str(s).endswith("tests")
    assert repr(s).startswith("[0]")
    assert not s.children
    assert str(s.defaults) == "defaults"

    s.add([])
    assert len(s.children) == 0

    s.add(["a", "b", "a"])
    s.add(["b", "a", "b"])
    assert len(s.children) == 2

    assert s.resolved_value("channel") == system.DEFAULT_CHANNEL
    assert s.resolved_definition("channel").source.path == "defaults"
    assert s.resolved_value("delivery") == system.DEFAULT_DELIVERY
    assert not s.index

    tox_channel = s.package_channel("tox")
    assert str(tox_channel) == system.DEFAULT_CHANNEL
    assert repr(tox_channel) == "latest from defaults:default.channel"
    tox_version = s.version("tox", channel=tox_channel)
    assert tox_version.value is None
    assert tox_version.channel == system.DEFAULT_CHANNEL

    assert s.base.relative_path(__file__) == "test_config.py"
    assert s.base.full_path("test_config.py") == __file__

    assert s.defaults.get_definition(None) is None

    # Invalid bundle type
    s.children[0].contents["bundle"] = "foo"
    assert s.get_value("bundle.foo") is None

    # Valid (parsed via set_contents()) bundle + channel setup
    s.children[0].set_contents(
        bundle=dict(foo="bar baz"),
        channel=dict(stable=dict(foo="1.2.3")),
    )
    assert s.get_value("bundle.foo") == ["bar", "baz"]
    assert s.get_definition("bundle.foo").source == s.children[0]
    assert s.resolved_packages("bundle:foo tox bar".split()) == ["bar", "baz", "tox"]

    assert str(s.package_channel("foo")) == "stable"
    version = s.version("foo")
    assert version.value == "1.2.3"
    assert version.channel == "stable"


def test_pypi():
    assert latest_pypi_version(None, None) is None
    assert latest_pypi_version(None, "tox")


@patch("pickley.pypi.request_get", return_value="{foo")
def test_pypi_bad_response(*_):
    assert latest_pypi_version(None, "foo") is None


@patch("pickley.pypi.request_get", return_value=LEGACY_SAMPLE)
def test_pypi_legacy(*_):
    assert latest_pypi_version(None, "twine") == "1.9.1"
