from mock import patch

from pickley import CFG, PackageSpec
from pickley.pypi import PepVersion, PypiInfo


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

FUNKY_SAMPLE = """
<html><head><title>Simple Index</title><meta name="api-version" value="2" /></head><body>
<a href="/pypi/packages/pypi-private/someproj/some.proj-1.3.0_custom-py3-none-any.whl#sha256=..."</a><br/>
<a href="/pypi/packages/pypi-private/someproj/some.proj-1.3.0_custom.tar.gz#sha256=...">someproj-1.3.0_custom.tar.gz</a><br/>
"""


def check_version(data, name, expected_version, index="https://mycompany.net/pypi/"):
    with patch("pickley.pypi.request_get", return_value=data):
        pspec = PackageSpec(CFG, name)
        i = PypiInfo(index, pspec)
        assert str(i) == "%s %s" % (name, expected_version)


def test_pypi(logged):
    check_version(LEGACY_SAMPLE, "shell-functools", "1.9.1")
    check_version(LEGACY_SAMPLE, "shell-functools", "1.9.1", index="https://mycompany.net/pypi/{name}")
    check_version(PRERELEASE_SAMPLE, "black", "18.3a1")
    assert not logged

    with patch("pickley.pypi.request_get", return_value='{"info": {"version": "1.0"}}'):
        assert str(PypiInfo(None, PackageSpec(CFG, "foo"))) == "foo 1.0"

    with patch("pickley.pypi.request_get", return_value=FUNKY_SAMPLE):
        i = PypiInfo(None, PackageSpec(CFG, "some.proj"))
        assert str(i) == "some-proj 1.3.0"
        assert "not pypi canonical" in logged.pop()

    with patch("requests.get", side_effect=IOError):
        i = PypiInfo(None, PackageSpec(CFG, "foo"))
        assert "no data for" in i.problem

    with patch("pickley.pypi.request_get", return_value="empty"):
        i = PypiInfo(None, PackageSpec(CFG, "foo"))
        assert "no versions published" in i.problem

    with patch("pickley.pypi.request_get", return_value="{foo"):
        i = PypiInfo(None, PackageSpec(CFG, "foo"))
        assert "invalid json" in i.problem
        assert "Failed to parse pypi json" in logged.pop()


def test_version():
    foo = PepVersion("foo")
    assert str(foo) == "foo"
    assert not foo.components
    assert not foo.prerelease

    bogus = PepVersion("1.2.3.4")
    assert str(bogus) == "1.2.3.4"
    assert not bogus.components
    assert not bogus.prerelease

    vrc = PepVersion("1.0rc4-foo")
    vdev = PepVersion("1.0a4.dev5-foo")
    assert vrc < vdev
    assert str(vrc) == "1.0rc4"
    assert str(vdev) == "1.0a4.dev5"

    v1 = PepVersion("1.2.3")
    v2 = PepVersion("1.2.3.post4")
    v3 = PepVersion("2.0")
    v4 = PepVersion("2.0.dev1")
    assert v2 > v1
    assert v3 > v2
    assert v4 > v3
