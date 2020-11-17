import os

import pytest
import runez
from mock import MagicMock, patch

from pickley import __version__, DEFAULT_PYTHONS, despecced, DOT_META, get_default_index, inform, PackageSpec
from pickley import PickleyConfig, pypi_name_problem, specced
from pickley.cli import auto_upgrade_v1
from pickley.v1upgrade import V1Status

from .conftest import dot_meta


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
  pyenv: /dev/null
  python: /dev/null, /dev/null/foo
  version_check_delay: 15

defaults:
  delivery: wrap
  install_timeout: 1800
  python: {DEFAULT_PYTHONS}
  version_check_delay: 300
"""


def grab_sample(name):
    cfg = PickleyConfig()
    path = runez.log.tests_path("samples", name)
    runez.copy(path, DOT_META)
    cfg.set_cli("config.json", None, None, None)
    cfg.set_base(".")
    assert str(cfg.configs[0]) == "cli (0 values)"
    return cfg


def test_bogus_config(temp_folder, logged):
    cfg = grab_sample("bogus-config")
    assert cfg.pyenv() == "/dev/null"  # from custom.json
    assert cfg.resolved_bundle("") == []
    assert cfg.resolved_bundle("foo") == ["foo"]
    assert cfg.resolved_bundle("bundle:dev") == ["tox", "mgit"]
    assert cfg.resolved_bundle("bundle:dev2") == ["tox", "mgit", "pipenv"]
    actual = cfg.represented().strip()
    expected = SAMPLE_CONFIG.strip().format(
        base=runez.short(cfg.base),
        meta=runez.short(cfg.meta),
        DEFAULT_PYTHONS=DEFAULT_PYTHONS,
    )
    assert actual == expected

    p = cfg.find_python(pspec=None, fatal=False)
    assert p.executable == "/dev/null/foo"
    assert p.problem == "not an executable"
    assert "was not usable, skipped" in logged.pop()

    assert not logged
    with pytest.raises(SystemExit):
        _ = PackageSpec.from_text(cfg, "mgit == 1.0.0")

    assert "Python '/dev/null' was not usable, skipped: not an executable" in logged.pop()


def test_default_index(temp_folder, logged):
    assert get_default_index() == (None, None)

    # Verify that we try 'a' (no such file), then find a configured index in 'b'
    runez.write("b", "[global]\nindex-url = https://example.com/pypi", logger=False)
    assert get_default_index("a", "b") == ("b", "https://example.com/pypi")

    # Not logging, since default is pypi, and which index is used can be configured and seen via diagnostics command
    assert not logged


def test_edge_cases():
    assert "intentionally" in pypi_name_problem("0-0")
    assert pypi_name_problem("mgit") is None

    cfg = PickleyConfig()
    assert str(cfg) == "<not-configured>"

    p = cfg.find_python(pspec=None)
    assert p == cfg.available_pythons.invoker


def test_good_config(temp_folder, logged):
    cfg = grab_sample("good-config")

    assert cfg.resolved_bundle("bundle:dev") == ["tox", "mgit", "poetry", "pipenv"]

    mgit = PackageSpec(cfg, "mgit", "1.0.0")
    pickley = PackageSpec(cfg, "pickley")
    assert mgit < pickley  # Ordering based on package name, then version
    assert str(mgit) == "mgit==1.0.0"
    assert str(pickley) == "pickley"
    assert mgit.index == "https://pypi-mirror.mycompany.net/pypi"
    logged.clear()

    with patch("pickley.PypiInfo", return_value=MagicMock(problem=None, latest="0.1.2")):
        d = pickley.get_desired_version_info()
        assert d.source == "current"
        assert d.version == __version__

        d = mgit.get_desired_version_info()
        assert d.source == "explicit"
        assert d.version == "1.0.0"

        # Verify latest when no pins configured
        p = PackageSpec(cfg, "foo")
        d = p.get_desired_version_info()
        assert d.version == "0.1.2"
        assert d.source == "latest"

        # Verify pinned versions in samples/.../config.json are respected
        p = PackageSpec(cfg, "mgit")
        d = p.get_desired_version_info()
        assert d.version == "1.2.1"
        assert d.source == "pinned"


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


def mock_install(pspec, **_):
    if pspec.dashed == "pickley2-a":
        raise Exception("does not exist")

    inform("Upgraded %s" % pspec.dashed)
    entrypoints = [pspec.dashed]
    pspec.version = "1.0"
    pspec.save_manifest(entrypoints)
    return MagicMock(entrypoints=entrypoints)


def test_v1(temp_folder, logged):
    cfg = PickleyConfig()
    cfg.set_base(".")
    status = V1Status(cfg)
    assert not status.installed

    sample = runez.log.tests_path("samples/v1")
    runez.copy(sample, DOT_META)
    status = V1Status(cfg)
    assert len(status.installed) == 2
    installed = sorted([str(s) for s in status.installed])
    assert installed == ["mgit", "pickley2-a"]

    # Add some files that should get cleaned up
    runez.touch(dot_meta("_venvs/_py37/bin/pip"))
    runez.touch(dot_meta("foo/.ping"))

    with patch("pickley.cli.perform_install", side_effect=mock_install):
        with pytest.raises(SystemExit):
            auto_upgrade_v1(cfg)

        assert "Auto-upgrading 2 packages" in logged
        assert "pickley2-a could not be upgraded, please reinstall it" in logged
        assert "Upgraded mgit" in logged
        assert "Deleted %s" % dot_meta("_venvs") in logged

        assert os.path.exists(dot_meta("README.md"))  # untouched
        assert os.path.exists(dot_meta("mgit/mgit-1.0/.manifest.json"))
        assert os.path.isdir(dot_meta("pickley"))
        assert not os.path.exists(dot_meta("_venvs"))  # cleaned
        assert not os.path.exists(dot_meta("foo"))
        assert not os.path.exists(dot_meta("pickley2-a"))
