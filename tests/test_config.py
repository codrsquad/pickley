import os

import pytest
import runez
from mock import MagicMock, patch

from pickley import __version__, DEFAULT_PYTHONS, despecced, get_default_index, inform, PackageSpec
from pickley import PickleyConfig, pypi_name_problem, specced
from pickley.cli import auto_upgrade_v1
from pickley.v1upgrade import V1Status


SAMPLE_CONFIG = """
base: {base}

cli:  # empty

{base}/.pickley/config.json:
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

{base}/.pickley/custom.json:
  delivery: wrap
  foo: bar
  include:
    - bogus.json
    - /dev/null/non-existent-config-file.json
  install_timeout: 2
  pyenv: /dev/null
  python: /dev/null, /dev/null/foo
  version_check_delay: 1

defaults:
  delivery: wrap
  install_timeout: 30
  python: {DEFAULT_PYTHONS}
  version_check_delay: 5
"""


def test_config(logged):
    cfg = PickleyConfig()
    assert str(cfg) == "<not-configured>"

    p = cfg.find_python(pspec=None)
    assert p == cfg.available_pythons.invoker

    sample = runez.log.tests_path("samples/custom-config")
    cfg.set_cli("config.json", None, None, None)
    cfg.set_base(sample)
    assert str(cfg) == runez.short(sample)
    assert str(cfg.configs[0]) == "cli (0 values)"
    assert cfg.base.path == sample
    assert cfg.pyenv() == "/dev/null"  # from custom.json
    assert cfg.resolved_bundle("") == []
    assert cfg.resolved_bundle("foo") == ["foo"]
    assert cfg.resolved_bundle("bundle:dev") == ["tox", "mgit"]
    assert cfg.resolved_bundle("bundle:dev2") == ["tox", "mgit", "pipenv"]
    actual = cfg.represented().strip()
    expected = SAMPLE_CONFIG.strip().format(base=runez.short(cfg.base), DEFAULT_PYTHONS=DEFAULT_PYTHONS)
    assert actual == expected

    assert not logged
    p = cfg.find_python(pspec=None)
    assert p.executable == "/dev/null/foo"
    assert p.problem == "not an executable"
    assert "was not usable, skipped" in logged.pop()


def test_default_index(temp_folder, logged):
    assert get_default_index() == (None, None)

    # Verify that we try 'a' (no such file), then find a configured index in 'b'
    runez.write("b", "[global]\nindex-url = https://example.com/pypi", logger=None)
    assert get_default_index("a", "b") == ("b", "https://example.com/pypi")

    # Not logging, since default is pypi, and which index is used can be configured and seen via diagnostics command
    assert not logged


def test_edge_cases():
    assert "intentionally" in pypi_name_problem("0-0")
    assert pypi_name_problem("mgit") is None


def test_desired_version(temp_folder, logged):
    cfg = PickleyConfig()
    sample = runez.log.tests_path("samples/custom-config")
    runez.copy(sample, "temp-base")
    cfg.set_base("temp-base")

    mgit = PackageSpec(cfg, "mgit == 1.0.0")
    pickley = PackageSpec(cfg, "pickley")
    assert mgit < pickley  # Ordering based on package name, then version
    assert str(mgit) == "mgit==1.0.0"
    assert str(pickley) == "pickley"
    assert mgit.index == "https://pypi-mirror.mycompany.net/pypi"
    assert mgit.cfg.install_timeout(mgit) == 2  # From custom.json
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

        p = PackageSpec(cfg, "tox")
        d = p.get_desired_version_info()
        assert d.version == "3.2.1"
        assert d.source == "pinned"
        assert p.settings.delivery == "custom-delivery"
        assert p.settings.index == "custom-index"
        assert p.settings.python == "2.8.1"
        assert p.cfg.install_timeout(p) == 42  # From tox specific pin in samples/.../config.json


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
    runez.copy(sample, ".pickley")
    status = V1Status(cfg)
    assert len(status.installed) == 2
    installed = sorted([str(s) for s in status.installed])
    assert installed == ["mgit", "pickley2-a"]

    # Add some files that should get cleaned up
    runez.touch(".pickley/_venvs/_py37/bin/pip")
    runez.touch(".pickley/foo/.ping")

    with patch("pickley.cli.perform_install", side_effect=mock_install):
        with pytest.raises(SystemExit):
            auto_upgrade_v1(cfg)

        assert "Auto-upgrading 2 packages" in logged
        assert "pickley2-a could not be upgraded, please reinstall it" in logged
        assert "Upgraded mgit" in logged
        assert "Deleted .pickley/_venvs" in logged

        assert os.path.exists(".pickley/README.md")  # untouched
        assert os.path.exists(".pickley/mgit/mgit-1.0/.manifest.json")
        assert os.path.isdir(".pickley/pickley")
        assert not os.path.exists(".pickley/_venvs")  # cleaned
        assert not os.path.exists(".pickley/foo")
        assert not os.path.exists(".pickley/pickley2-a")
