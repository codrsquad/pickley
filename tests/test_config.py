import os

from pickley.settings import same_type, Settings


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

    assert s.get_value("channel") == "latest"
    assert s.get_definition("channel").source.path == "defaults"
    assert s.get_value("delivery") == "symlink"
    assert not s.index

    tox_channel = s.package_channel("tox")
    assert str(tox_channel) == "latest"
    assert repr(tox_channel) == "latest from defaults"
    tox_version = s.version("tox", channel=tox_channel)
    assert tox_version.value is None
    assert tox_version.channel == "latest"

    assert s.base.relative_path(__file__) == "test_config.py"
    assert s.base.full_path("test_config.py") == __file__

    assert s.defaults.get_value(None) is None

    # Invalid bundle type
    s.children[0].contents["bundle"] = "foo"
    assert s.get_value("bundle.foo") is None

    # Valid (parsed via set_contents()) bundle + channel setup
    s.children[0].set_contents(
        bundle=dict(foo="bar baz"),
        channels=dict(stable=dict(foo="1.2.3")),
    )
    assert s.get_value("bundle.foo") == ["bar", "baz"]
    assert s.get_definition("bundle.foo").source == s.children[0]
    assert s.resolved_packages("bundle:foo tox bar".split()) == ["bar", "baz", "tox"]

    assert str(s.package_channel("foo")) == "stable"
    version = s.version("foo")
    assert version.value == "1.2.3"
    assert version.channel == "stable"
