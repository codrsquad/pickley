import pytest
import runez
from runez.pyenv import PypiStd

from pickley import __version__, despecced, DOT_META, get_default_index, PackageSpec
from pickley import PickleyConfig, pypi_name_problem, specced


PYPI_CLIENT = PypiStd.default_pypi_client()

SAMPLE_CONFIG = """
base: {base}

cli:  # empty

{meta}/config.json:
  bundle:
    dev: tox mgit
    dev2: bundle:dev pipenv
  delivery: wrap
  include: custom.json
  index: https://pypi-mirror.mycompany.net/pypi
  pinned:
    mgit: 1.2.1
    tox:
      delivery: custom-delivery
      index: custom-index
      install_timeout: 42
      python: 2.8.1
      version: 3.2.1

{meta}/custom.json:
  delivery: wrap
  foo: bar
  include:
   - bogus.json
   - /dev/null/non-existent-config-file.json
  install_timeout: 250
  python: /dev/null, /dev/null/foo
  version_check_delay: 15

defaults:
  delivery: wrap
  install_timeout: 1800
  min_python: 3.6
  preferred_min_python: 3.7
  preferred_pythons: /usr/bin/python3,/usr/bin/python
  version_check_delay: 300
"""


def grab_sample(name):
    cfg = PickleyConfig()
    path = runez.DEV.tests_path("samples", name)
    runez.copy(path, DOT_META)
    cfg.set_cli("config.json", None, None, None, None)
    cfg.set_base(".")
    assert str(cfg.configs[0]) == "cli (0 values)"
    return cfg


def test_bogus_config(temp_folder, logged):
    cfg = grab_sample("bogus-config")
    assert cfg.resolved_bundle("") == []
    assert cfg.resolved_bundle("foo") == ["foo"]
    assert cfg.resolved_bundle("bundle:dev") == ["tox", "mgit"]
    assert cfg.resolved_bundle("bundle:dev2") == ["tox", "mgit", "pipenv"]
    actual = cfg.represented().strip()
    expected = SAMPLE_CONFIG.strip().format(
        base=runez.short(cfg.base),
        meta=runez.short(cfg.meta),
    )
    assert actual == expected

    p = cfg.find_python(pspec=None, fatal=False)
    assert p.executable == "/dev/null/foo"
    assert p.problem == "/dev/null/foo is not an executable"
    assert "skipped: /dev/null/foo is not an executable" in logged.pop()

    assert not logged
    p = PackageSpec(cfg, "mgit")
    with pytest.raises(SystemExit):
        _ = p.python  # Fails to resolve due to desired python configured to be /dev/null

    assert "No suitable python" in logged.pop()


def test_default_index(temp_folder, logged):
    assert get_default_index() == (None, None)

    # Verify that we try 'a' (no such file), then find a configured index in 'b'
    runez.write("b", "[global]\nindex-url = https://example.com/pypi", logger=False)
    assert get_default_index("a", "b") == ("b", "https://example.com/pypi")

    # Not logging, since default is pypi, and which index is used can be configured and seen via diagnostics command
    assert not logged


def test_edge_cases():
    cfg = PickleyConfig()
    assert str(cfg) == "<not-configured>"
    assert "intentionally refuses" in pypi_name_problem("0-0")
    assert pypi_name_problem("mgit") is None
    p = cfg.find_python(pspec=None)
    assert p is cfg.available_pythons.invoker


@PYPI_CLIENT.mock({
    "https://pypi-mirror.mycompany.net/pypi/foo/": {"info": {"version": "0.1.2"}}
})
def test_good_config(temp_folder, logged):
    cfg = grab_sample("good-config")

    assert cfg.resolved_bundle("bundle:dev") == ["tox", "mgit", "poetry", "pipenv"]

    mgit = PackageSpec(cfg, "mgit==1.0.0")
    pickley = PackageSpec(cfg, "pickley")
    assert mgit < pickley  # Ordering based on package name, then version
    assert str(mgit) == "mgit==1.0.0"
    assert str(pickley) == "pickley"
    assert mgit.index == "https://pypi-mirror.mycompany.net/pypi"
    logged.clear()

    assert pickley.desired_track.source == "current"
    assert pickley.desired_track.version == __version__

    assert mgit.desired_track.source == "explicit"
    assert mgit.desired_track.version == "1.0.0"

    # Verify latest when no pins configured
    p = PackageSpec(cfg, "foo")
    assert p.desired_track.version == "0.1.2"
    assert p.desired_track.source == "latest"

    # Verify pinned versions in samples/.../config.json are respected
    p = PackageSpec(cfg, "mgit")
    assert p.desired_track.version == "1.2.1"
    assert p.desired_track.source == "pinned"


def test_speccing():
    assert specced("mgit", "1.0.0") == "mgit==1.0.0"
    assert specced(" mgit ", " 1.0.0 ") == "mgit==1.0.0"
    assert specced("mgit", None) == "mgit"
    assert specced("mgit", "") == "mgit"
    assert specced(" mgit ", " ") == "mgit"

    assert despecced("mgit") == ("mgit", None)
    assert despecced("mgit==1.0.0") == ("mgit", "1.0.0")
    assert despecced(" mgit == 1.0.0 ") == ("mgit", "1.0.0")
    assert despecced("mgit==") == ("mgit", None)
    assert despecced(" mgit == ") == ("mgit", None)
