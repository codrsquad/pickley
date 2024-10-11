import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
import runez

from pickley import bstrap, program_version
from pickley.cli import CFG

from .conftest import dot_meta


def mocked_expanduser(path):
    if path and path.startswith("~/"):
        path = path[2:]

    return path


def test_bootstrap_command(cli, monkeypatch):
    cli.run("-n", "bootstrap", ".local/bin", cli.project_folder)
    assert cli.failed
    assert "Folder .local/bin does not exist" in cli.logged

    runez.ensure_folder(".local/bin", logger=None)
    cli.run("-vv", "bootstrap", ".local/bin", cli.project_folder)
    assert cli.succeeded
    if bstrap.USE_UV:
        assert "Installed uv v" in cli.logged
        assert program_version(".local/bin/uv")

    assert "Installed pickley v" in cli.logged
    assert program_version(".local/bin/pickley")


def test_bootstrap_script(cli, monkeypatch):
    # Ensure changes to bstrap.py globals are restored
    monkeypatch.setattr(bstrap, "DRYRUN", False)
    monkeypatch.setattr(bstrap, "Reporter", bstrap._Reporter)
    monkeypatch.setattr(bstrap, "VERBOSITY", 0)
    monkeypatch.setattr(cli, "main", bstrap.main)
    monkeypatch.setattr(bstrap.os.path, "expanduser", mocked_expanduser)
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "foo")

    # Verify that we report base not writeable correctly
    cli.run("-nvv")
    assert cli.failed
    assert "Unsetting env var __PYVENV_LAUNCHER__" in cli.logged.stdout
    assert "Make sure '.local/bin' is writeable" in cli.logged

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
    cli.run(cli.project_folder, "-m", mirror, "-c", f"{{{sample_config}}}")
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
        assert r.succeeded

        # Verify that a bogus uv config file fails the run...
        runez.write(uv_config, "[pip]\nindex-url = http://foo", logger=None)
        r = runez.run(uv_path, "venv", "exercise-venv", fatal=False, logger=None)
        assert r.failed
        assert "Failed to parse" in r.error

    def mocked_run(program, *args, **__):
        if args[0] == "--version":
            return "0.0"

        if "-mvenv" in args:
            return 1

        logging.info("Running %s %s", program, " ".join(str(x) for x in args))


@pytest.mark.skipif(not bstrap.USE_UV, reason="Applies only to uv")
def test_download_uv(temp_cfg, monkeypatch):
    assert bstrap.uv_url(None)

    monkeypatch.setattr(bstrap, "DRYRUN", True)
    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    assert bstrap.find_uv(temp_cfg.base) == temp_cfg.base / ".pk/.cache/uv/bin/uv"

    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    runez.write("sample-uv", "#!/bin/sh\necho uv 0.0.1", logger=None)
    runez.make_executable("sample-uv", logger=None)
    runez.symlink("sample-uv", "uv", logger=None)
    assert bstrap.find_uv(temp_cfg.base) == temp_cfg.base / "uv"

    # Simulate bad uv download
    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    runez.write("sample-uv", "#!/bin/sh\nexit 1", logger=None)
    assert bstrap.find_uv(temp_cfg.base) == temp_cfg.base / ".pk/.cache/uv/bin/uv"


def test_edge_cases(temp_cfg, logged, monkeypatch):
    # For coverage
    monkeypatch.setattr(bstrap, "Reporter", bstrap._Reporter)
    assert bstrap.which("python3")
    assert bstrap.run_program(sys.executable, "--version") == 0
    with pytest.raises(SystemExit) as exc:
        bstrap.run_program(sys.executable, "--no-such-option")
    assert " exited with code" in str(exc)
    assert "python --no-such-option" in logged.pop()

    def mocked_run(program, *_, **__):
        return program

    with patch("pickley.bstrap.built_in_download", side_effect=Exception):  # urllib fails
        with patch("pickley.bstrap.Request", side_effect=Exception):
            with pytest.raises(SystemExit, match="Failed to fetch http://foo"):
                bstrap.http_get("http://foo")

        with patch("pickley.bstrap.run_program", side_effect=mocked_run):
            with patch("pickley.bstrap.which", side_effect=lambda x: "/bin/%s" % x if x in ("curl", "pickley") else None):
                assert bstrap.download("test", "test") == "/bin/curl"

            with patch("pickley.bstrap.which", side_effect=lambda x: x if x == "wget" else None):
                assert bstrap.download("test", "test") == "wget"

            with patch("pickley.bstrap.which", return_value=None):
                with pytest.raises(SystemExit) as exc:
                    bstrap.download("test", "test")
                assert "No `curl` nor `wget`" in str(exc)


def test_failure(cli, monkeypatch):
    # Ensure changes to bstrap.py globals are restored
    monkeypatch.setattr(bstrap, "DRYRUN", False)
    monkeypatch.setattr(bstrap, "Reporter", bstrap._Reporter)
    monkeypatch.setattr(bstrap, "VERBOSITY", 0)
    monkeypatch.setattr(cli, "main", bstrap.main)

    with patch("pickley.bstrap.Request", side_effect=HTTPError("url", 404, "msg", None, None)):
        cli.run("--base .", main=bstrap.main)
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
