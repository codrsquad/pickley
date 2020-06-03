import runez
from runez.conftest import resource_path

from pickley import despecced, PackageSpec, PickleyConfig, pypi_name_problem, specced, TrackedSettings
from pickley.v1upgrade import V1Status


SAMPLE_CONFIG = """
base: {base}

cli:  # empty

{base}/.p/config.json:
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

{base}/.p/custom.json:
  delivery: wrap
  include:
    - bogus.json
    - /dev/null/non-existent-config-file.json
  install_timeout: 2
  pyenv: /dev/null
  version_check_delay: 1

defaults:
  delivery: wrap
  install_timeout: 30
  version_check_delay: 5
"""


def test_config():
    cfg = PickleyConfig()
    assert str(cfg) == "<not-configured>"

    sample = resource_path("samples/custom-config")
    cfg.set_base(sample, cli=TrackedSettings(None, None, None))
    assert str(cfg) == runez.short(sample)
    assert str(cfg.configs[0]) == "cli (0 values)"
    assert cfg.base.path == sample
    assert cfg.pyenv() == "/dev/null"  # from custom.json
    assert cfg.resolved_bundle("") == []
    assert cfg.resolved_bundle("foo") == ["foo"]
    assert cfg.resolved_bundle("bundle:dev") == ["tox", "mgit"]
    assert cfg.resolved_bundle("bundle:dev2") == ["tox", "mgit", "pipenv"]
    actual = cfg.represented().strip()
    expected = SAMPLE_CONFIG.strip().format(base=runez.short(cfg.base))
    assert actual == expected


def test_edge_cases():
    assert "intentionally" in pypi_name_problem("0-0")
    assert pypi_name_problem("mgit") is None


def test_speccing():
    cfg = PickleyConfig()
    sample = resource_path("samples/custom-config")
    cfg.set_base(sample, cli=TrackedSettings(None, None, None))

    p1 = PackageSpec(cfg, "mgit == 1.0.0")
    p2 = PackageSpec(cfg, "pickley")
    assert p1 < p2  # Ordering based on package name, then version
    assert str(p1) == "mgit==1.0.0"
    assert str(p2) == "pickley"

    d = p1.get_desired_version_info()
    assert d.source == "desired"
    assert d.version == "1.0.0"

    # Verify pinned versions in samples/.../config.json are respected
    p = PackageSpec(cfg, "mgit")
    d = p.get_desired_version_info()
    assert d.version == "1.2.1"
    assert p.cfg.install_timeout(p) == 2  # From custom.json

    p = PackageSpec(cfg, "tox")
    d = p.get_desired_version_info()
    assert d.version == "3.2.1"
    assert p.settings.delivery == "custom-delivery"
    assert p.settings.index == "custom-index"
    assert p.settings.python == "2.8.1"
    assert p.cfg.install_timeout(p) == 42  # From tox specific pin in samples/.../config.json

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


def test_v1(temp_folder):
    cfg = PickleyConfig()
    cfg.set_base(".")
    s = V1Status(cfg)
    assert not s.installed

    sample = resource_path("samples/v1")
    runez.copy(sample, ".pickley")
    s = V1Status(cfg)
    assert len(s.installed) == 2
    installed = sorted([str(s) for s in s.installed])
    assert installed == ["mgit", "pickley2-a"]
