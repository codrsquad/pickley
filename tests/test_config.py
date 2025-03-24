import runez

from pickley import bstrap, CFG

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
  version_check_delay: 300
"""


def grab_sample(name):
    path = runez.to_path(runez.DEV.tests_path("samples", name))
    for item in path.iterdir():
        runez.copy(item, f"{bstrap.DOT_META}/{item.name}")

    CFG.set_cli("config.json", None, None, None, None)
    CFG.set_base(".")
    assert str(CFG.configs[0]) == "cli (0 values)"


def test_bogus_config(temp_cfg):
    grab_sample("bogus-config")
    assert CFG.resolved_bundle("") == []
    assert CFG.resolved_bundle("foo") == ["foo"]
    assert CFG.resolved_bundle("bundle:dev") == ["tox", "mgit"]
    assert CFG.resolved_bundle("bundle:dev2") == ["tox", "mgit", "pipenv"]
    assert CFG.pip_conf is None
    assert CFG.pip_conf_index == bstrap.DEFAULT_MIRROR
    actual = CFG.represented().strip()
    expected = SAMPLE_CONFIG.strip().format(
        base=runez.short(CFG.base),
        meta=runez.short(CFG.meta),
    )
    assert actual == expected


def test_good_config(cli):
    grab_sample("good-config")

    assert CFG.resolved_bundle("bundle:dev") == ["tox", "poetry", "mgit", "pipenv"]
    assert CFG.resolved_bundle("bundle:dev3") == ["mgit"]

    cli.run("diagnostics")
    assert cli.succeeded
    assert "pip.conf : -missing-" in cli.logged

    cli.run("config")
    assert cli.succeeded
    assert "dev2: bundle:dev3 pipenv" in cli.logged
    assert "mgit: 1.2.1" in cli.logged

    cli.run("-n install bundle:dev3")
    assert cli.succeeded
    assert "Would wrap mgit -> .pk/mgit-1.2.1/bin/mgit" in cli.logged

    cli.run("-n install bundle:foo")
    assert cli.failed
    assert "Can't install 'bundle:foo', not configured" in cli.logged


def test_despecced():
    assert CFG.despecced("mgit") == ("mgit", None)
    assert CFG.despecced("mgit==1.0.0") == ("mgit", "1.0.0")
    assert CFG.despecced(" mgit == 1.0.0 ") == ("mgit", "1.0.0")
    assert CFG.despecced("mgit==") == ("mgit", None)
    assert CFG.despecced(" mgit == ") == ("mgit", None)
