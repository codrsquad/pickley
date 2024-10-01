import pytest
import runez

from pickley import bstrap, CFG, despecced, PackageSpec, pypi_name_problem, specced

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
  python: /dev/null/foo
  version_check_delay: 15

defaults:
  delivery: wrap
  install_timeout: 1800
  package_manager: {package_manager}
  version_check_delay: 300
"""


def grab_sample(name):
    path = runez.DEV.tests_path("samples", name)
    runez.copy(path, bstrap.DOT_META)
    CFG.set_cli("config.json", None, None, None, None)
    CFG.set_base(".")
    assert str(CFG.configs[0]) == "cli (0 values)"


def test_bogus_config(temp_cfg, logged):
    grab_sample("bogus-config")
    assert CFG.resolved_bundle("") == []
    assert CFG.resolved_bundle("foo") == ["foo"]
    assert CFG.resolved_bundle("bundle:dev") == ["tox", "mgit"]
    assert CFG.resolved_bundle("bundle:dev2") == ["tox", "mgit", "pipenv"]
    actual = CFG.represented().strip()
    expected = SAMPLE_CONFIG.strip().format(
        base=runez.short(CFG.base),
        meta=runez.short(CFG.meta),
        package_manager=bstrap.default_package_manager(),
    )
    assert actual == expected


def test_edge_cases():
    assert "intentionally refuses" in pypi_name_problem("0-0")
    assert pypi_name_problem("mgit") is None


def test_good_config(temp_cfg, monkeypatch):
    if bstrap.USE_UV:
        monkeypatch.setattr(CFG, "_uv_path", None)
        monkeypatch.setattr(runez.DEV, "project_folder", None)
        with pytest.raises(runez.system.AbortException, match="`uv` is not installed"):
            CFG.find_uv()

        tmp_uv = CFG.base.full_path("uv")
        runez.touch(tmp_uv)
        runez.make_executable(tmp_uv)
        assert CFG.find_uv() == tmp_uv

    grab_sample("good-config")

    assert CFG.resolved_bundle("bundle:dev") == ["tox", "mgit", "poetry", "pipenv"]

    mgit = PackageSpec("mgit==1.0.0")
    pickley = PackageSpec("pickley==1.0.0")
    assert mgit < pickley  # Ordering based on package name, then version
    assert str(mgit) == "mgit==1.0.0"


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
