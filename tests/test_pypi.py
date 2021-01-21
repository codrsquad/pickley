import pytest
import runez
from mock import MagicMock, patch

from pickley import PackageSpec, TrackedManifest
from pickley.pypi import curl_get, PepVersion, PypiInfo, UrllibRequestor


LEGACY_SAMPLE = """
<html><head><title>Simple Index</title><meta name="api-version" value="2" /></head><body>

# 1.8.1 intentionally malformed
<a href="/pypi/shell-functools/shell_functools-1.8.1!1-py2.py3-none-any.whl9#">shell_functools-1.8.1-py2.py3-none-any.whl</a><br/>
<a href="/pypi/shell-functools/shell-functools-1.8.1!1.tar.gz#">shell-functools-1.8.1.tar.gz</a><br/>

<a href="/pypi/shell-functools/shell_functools-1.9.9+local-py2.py3-none-any.whl#sha...">shell_functools-1.9.9-py2.py3-none-any.whl</a><br/>
<a href="/pypi/shell-functools/shell-functools-1.9.9+local.tar.gz#sha256=ff...">shell-functools-1.9.9.tar.gz</a><br/>
<a href="/pypi/shell-functools/shell_functools-1.9.11-py2.py3-none-any.whl#sha256=...">shell_functools-1.9.11-py2.py3-none-any.whl</a><br/>
<a href="/pypi/shell-functools/shell-functools-1.9.11.tar.gz#sha256=ca...">shell-functools-1.9.11.tar.gz</a><br/>
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


def check_version(cfg, data, name, expected_version, index="https://mycompany.net/pypi/"):
    with patch("runez.FallbackChain.__call__", return_value=data):
        pspec = PackageSpec(cfg, name)
        i = PypiInfo(index, pspec)
        assert str(i) == "%s %s" % (name, expected_version)
        d = pspec.get_desired_version_info()
        assert d.version == expected_version
        assert str(d)
        m = pspec.get_manifest()
        assert str(m)


def test_pypi(temp_cfg, logged):
    with patch("pickley.PackageSpec.get_manifest", return_value=TrackedManifest(None, None, None, version="1.9.9")):
        check_version(temp_cfg, LEGACY_SAMPLE, "shell-functools", "1.9.11")

    check_version(temp_cfg, LEGACY_SAMPLE, "shell-functools", "1.9.11", index="https://mycompany.net/pypi/{name}")
    check_version(temp_cfg, PRERELEASE_SAMPLE, "black", "18.3a1")

    logged.clear()
    with patch("runez.FallbackChain.__call__", return_value='{"info": {"version": "1.0"}}'):
        assert str(PypiInfo(None, PackageSpec(temp_cfg, "foo"))) == "foo 1.0"

    assert not logged
    with patch("runez.FallbackChain.__call__", return_value=FUNKY_SAMPLE):
        i = PypiInfo(None, PackageSpec(temp_cfg, "some.proj"))
        assert str(i) == "some-proj 1.3.0"
        assert "not pypi canonical" in logged.pop()

    with patch("runez.FallbackChain.__call__", return_value="{foo"):
        i = PypiInfo(None, PackageSpec(temp_cfg, "foo"))
        assert "invalid json" in i.problem
        assert "Failed to parse pypi json" in logged.pop()

    with patch("runez.FallbackChain.__call__", return_value="empty"):
        i = PypiInfo(None, PackageSpec(temp_cfg, "foo"))
        assert "no versions published" in i.problem
        assert not logged


def test_pypi_chain(temp_cfg):
    if not runez.PY2:
        # Simulate urllib querying
        from urllib.error import HTTPError

        mocked_response = MagicMock()
        mocked_response.read.return_value = "empty"
        with patch("urllib.request.urlopen", return_value=mocked_response):
            i = PypiInfo(None, PackageSpec(temp_cfg, "foo"), pypi_get=runez.FallbackChain(UrllibRequestor()))
            assert "no versions published" in i.problem

        with patch("urllib.request.urlopen", side_effect=HTTPError("", 404, None, None, None)):
            i = PypiInfo(None, PackageSpec(temp_cfg, "foo"), pypi_get=runez.FallbackChain(UrllibRequestor()))
            assert "no data" in i.problem

        with patch("urllib.request.urlopen", side_effect=HTTPError("", 500, None, None, None)):
            with pytest.raises(Exception):
                PypiInfo(None, PackageSpec(temp_cfg, "foo"), pypi_get=runez.FallbackChain(UrllibRequestor()))

    # Exercise curl_get code path
    with patch("runez.run", return_value=runez.program.RunResult("empty", code=0)):
        assert curl_get("") == "empty"

    with patch("runez.run", return_value=runez.program.RunResult("failed")):
        with pytest.raises(Exception):
            curl_get("")


def test_version():
    foo = PepVersion("foo")
    assert str(foo) == "foo"
    assert not foo.components
    assert not foo.prerelease

    bogus = PepVersion("1.2.3.4.5")
    assert str(bogus) == "1.2.3.4.5"
    assert not bogus.components
    assert not bogus.prerelease

    vrc = PepVersion("1.0rc4-foo")
    vdev = PepVersion("1.0a4.dev5-foo")
    assert vrc < vdev
    assert str(vrc) == "1.0rc4"
    assert str(vdev) == "1.0a4.dev5"

    v11 = PepVersion("1.1.2.3")
    v12 = PepVersion("1.2.3")
    v12p = PepVersion("1.2.3.post4")
    v20 = PepVersion("2.0")
    v20d = PepVersion("2.0.dev1")
    v3 = PepVersion("3.0.1.2")
    assert v12 > v11
    assert v12p > v11
    assert v20 > v11
    assert v20d > v11
    assert v3 > v11
    assert v12p > v12
    assert v20 > v12
    assert v20d > v12
    assert v3 > v12
    assert v20 > v12p
    assert v20d > v12p
    assert v3 > v12p
    assert v20d > v20
    assert v3 > v20
    assert v3 > v20d
