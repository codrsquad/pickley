import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
import runez

from pickley import bstrap, Reporter
from pickley.cli import CFG

from .conftest import dot_meta


def test_bootstrap_command(cli, monkeypatch):
    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    monkeypatch.setattr(CFG, "_uv_path", None)
    cli.run("-n", "bootstrap", ".local/bin", cli.project_folder)
    assert cli.failed
    assert "Folder .local/bin does not exist" in cli.logged

    runez.ensure_folder(".local/bin", logger=None)
    cli.run("--no-color", "-vv", "bootstrap", ".local/bin", cli.project_folder)
    assert cli.succeeded
    if bstrap.USE_UV:
        assert "Authoritative auto-upgrade spec 'uv' v" in cli.logged
        assert "Bootstrapped uv v" in cli.logged
        assert CFG.program_version(".local/bin/uv")

    else:
        assert bstrap._UV_PATH is None

    assert "Installed pickley v" in cli.logged
    assert CFG.program_version(".local/bin/pickley")


def test_bootstrap_script(cli, monkeypatch):
    # Ensure changes to bstrap.py globals are restored
    cli.main = bstrap.main
    monkeypatch.setattr(bstrap, "Reporter", bstrap._Reporter)
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "foo")

    # Verify that we report base not writeable correctly
    cli.run("-nvv")
    assert cli.failed
    assert "Unsetting env var __PYVENV_LAUNCHER__" in cli.logged.stdout
    assert "Make sure '~/.local/bin' exists and is writeable" in cli.logged
    monkeypatch.delenv("__PYVENV_LAUNCHER__")

    runez.ensure_folder(".local/bin", logger=None)
    cli.run("-n", cli.project_folder)
    assert cli.succeeded
    assert "bin/pickley bootstrap " in cli.logged
    if bstrap.USE_UV:
        assert "uv -q pip install -e " in cli.logged

    else:
        assert " -mvenv --clear " in cli.logged

    cli.run("-n --package-manager foo")
    assert cli.failed
    assert "Unsupported package manager 'foo'" in cli.logged

    cli.run("-n -cfoo")
    assert cli.failed
    assert "--config must be a serialized json object" in cli.logged

    # Simulate multiple base candidates given, verify that -c triggers PATH env var check
    cli.run("-n --check-path")
    assert cli.failed
    assert " is in your PATH environment variable." in cli.logged

    # Full bootstrap run, with seeding
    uv_config = ".config/uv/uv.toml"
    sample_config = '"python-installations": "~/.my-pyenv/version/**"'
    monkeypatch.setenv("PATH", ".local/bin:%s" % os.environ["PATH"])
    mirror = "https://pypi.org/simple"
    cli.run("-vv", cli.project_folder, "-m", mirror, "-c", f"{{{sample_config}}}")
    assert cli.succeeded
    assert f"Seeding .config/pip/pip.conf with {mirror}" in cli.logged
    assert f"Seeding {uv_config} with {mirror}" in cli.logged
    assert list(runez.readlines(".config/pip/pip.conf")) == ["[global]", f"index-url = {mirror}"]
    assert list(runez.readlines(uv_config)) == ["[pip]", f'index-url = "{mirror}"']
    assert list(runez.readlines(f".local/bin/{dot_meta('config.json')}")) == ["{", f"  {sample_config}", "}"]

    if bstrap.USE_UV:
        # Now verify that uv works with the seeded file
        monkeypatch.setenv("UV_CONFIG_FILE", uv_config)
        uv_path = bstrap.find_uv(CFG.base)
        r = runez.run(uv_path, "venv", "exercise-venv", fatal=False, logger=None)
        assert r.succeeded, f"uv venv failed: {r.full_output}"

        # Verify that a bogus uv config file fails the run...
        runez.write(uv_config, f"[pip]\nindex-url = {bstrap.DEFAULT_MIRROR}", logger=None)  # Missing double quotes
        r = runez.run(uv_path, "venv", "exercise-venv", fatal=False, logger=None)
        assert r.failed
        assert "Failed to " in r.error


@pytest.mark.skipif(not bstrap.USE_UV, reason="Applies only to uv")
def test_download_uv(temp_cfg, monkeypatch):
    assert bstrap.uv_url(None)

    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    tmp_uv = temp_cfg.base / bstrap.DOT_META / ".cache/uv/bin/uv"
    assert bstrap.find_uv(temp_cfg.base) == tmp_uv

    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    runez.write("sample-uv", "#!/bin/sh\necho uv 0.0.1", logger=None)
    runez.make_executable("sample-uv", logger=None)
    runez.symlink("sample-uv", "uv", logger=None)
    assert bstrap.find_uv(temp_cfg.base) == temp_cfg.base / "uv"

    # Simulate bad uv download
    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    runez.write("sample-uv", "#!/bin/sh\nexit 1", logger=None)
    assert bstrap.find_uv(temp_cfg.base) == tmp_uv


def test_edge_cases(temp_cfg, monkeypatch):
    # For coverage
    monkeypatch.setattr(bstrap, "Reporter", Reporter)
    monkeypatch.setenv("PATH", "test-programs")

    assert bstrap.run_program(sys.executable, "--version") == 0
    with pytest.raises(runez.system.AbortException, match=" exited with code"):
        bstrap.run_program(sys.executable, "--no-such-option")

    runez.touch("test-programs/curl", logger=None)
    runez.touch("test-programs/wget", logger=None)
    runez.make_executable("test-programs/curl", logger=None)
    runez.make_executable("test-programs/wget", logger=None)

    monkeypatch.setattr(bstrap, "run_program", lambda p, *_, **__: str(p))
    with patch("pickley.bstrap.built_in_download", side_effect=Exception):  # urllib fails
        assert bstrap.download(Path("test"), "test") == "test-programs/curl"

        runez.delete("test-programs/curl", logger=None)
        assert bstrap.download(Path("test"), "test") == "test-programs/wget"

        runez.delete("test-programs/wget", logger=None)
        with pytest.raises(runez.system.AbortException, match="No `curl` nor `wget`"):
            bstrap.download(Path("test"), "test")


def test_failure(cli, monkeypatch):
    # Ensure changes to bstrap.py globals are restored
    cli.main = bstrap.main
    monkeypatch.setattr(bstrap, "Reporter", bstrap._Reporter)

    runez.ensure_folder(".local/bin", logger=None)
    with patch("pickley.bstrap.Request", side_effect=Exception):
        cli.run("")
        assert cli.failed
        assert "Failed to fetch https://pypi.org/pypi/pickley/json" in cli.logged

    with patch("pickley.bstrap.Request", side_effect=HTTPError("url", 404, "msg", None, None)):
        cli.run("--base .")
        assert cli.failed
        assert "Failed to determine latest pickley version" in cli.logged

    with patch("pickley.bstrap.Request", side_effect=HTTPError("url", 500, "msg", None, None)):
        cli.run("--base .")
        assert cli.failed
        assert "Failed to fetch https://pypi.org/pypi/pickley/json: HTTP Error 500: msg" in cli.logged

    with patch("pickley.bstrap.Request", side_effect=URLError("foo")):
        cli.run("--base .")
        assert cli.failed
        assert "Failed to fetch https://pypi.org/pypi/pickley/json: <urlopen error foo>" in cli.logged

    def mocked_run(program, *args, **__):
        if program != "wget":
            print(f"Running {program} {runez.joined(args)}")
            return

        with open(args[2], "w") as fh:
            json.dump({"info": {"version": "1.0"}}, fh)

    # Simulate a py3.6 ssl issue, fallback to using curl or wget to figure out latest pickley version
    with patch("pickley.bstrap.Request", side_effect=URLError("ssl issue")):
        with patch("pickley.bstrap.which", side_effect=lambda x: x if x == "wget" else None):
            with patch("pickley.bstrap.run_program", side_effect=mocked_run):
                cli.run("-n --base .")
                assert cli.succeeded
                assert "pip install pickley==1.0"


def test_legacy(cli, monkeypatch):
    cli.main = bstrap.main
    cli.run("--base . -mhttp://localhost:12345/simple")
    assert cli.failed
    assert "Failed to fetch http://localhost:12345/pypi/pickley/json:" in cli.logged
    assert runez.to_path(".config/pip/pip.conf").exists()
    assert "mirror: http://localhost:12345/simple" in cli.logged
    assert "index-url = http://localhost:12345/simple" in runez.readlines(".config/pip/pip.conf")
    runez.delete(".config", logger=None)

    runez.write("pickley", "#!/bin/sh\necho 1.0", logger=None)
    runez.make_executable("pickley", logger=None)

    def mocked_run(program, *args, **kwargs):
        if "-mvenv" in args:
            return 1

        kwargs.setdefault("dryrun", True)
        r = runez.run(program, *args, **kwargs)
        return r.exit_code if kwargs.get("fatal") else r.full_output

    with patch("pickley.bstrap.run_program", side_effect=mocked_run):
        cli.run("-nvv --base .")
        assert cli.succeeded
        if bstrap.USE_UV:
            assert "virtualenv" not in cli.logged

        else:
            assert "python .pk/.cache/virtualenv.pyz" in cli.logged

        assert "pickley base bootstrap-own-wrapper" in cli.logged
        assert "Replacing older pickley v1.0" in cli.logged


def test_pip_conf(temp_cfg, logged):
    assert bstrap.globally_configured_pypi_mirror([]) == (bstrap.DEFAULT_MIRROR, None)

    # Verify that we try reading all stated pip.conf files in order, and user the value from the first valid one
    runez.write("a", "ouch, this is not a valid config file", logger=None)  # Invalid file
    runez.write("b", "[global]\nindex-url = /", logger=None)  # Valid, but mirror not actually configured
    runez.write("c", "[global]\nindex-url = https://example.com/simple//", logger=None)
    runez.write("d", "[global]\nindex-url = https://example.com/pypi2", logger=None)  # Not needed, previous one wins
    assert bstrap.globally_configured_pypi_mirror(["no-such-file.conf", "a", "b", "c", "d"]) == ("https://example.com/simple", Path("c"))

    # Keep chatter to a minimum
    assert "no-such-file.conf" not in logged  # No chatter about non-existing files
    assert "Could not read 'a'" in logged.pop()
