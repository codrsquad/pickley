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


def test_bootstrap_command(cli):
    cli.run("-n", "bootstrap", ".local/bin", cli.project_folder)
    assert cli.failed
    assert "Folder .local/bin does not exist" in cli.logged

    runez.ensure_folder(".local/bin", logger=None)
    cli.run("--no-color", "-vv", "bootstrap", ".local/bin", cli.project_folder)
    assert cli.succeeded
    if bstrap.USE_UV:
        assert "Auto-bootstrapping uv, reason: uv not present" in cli.logged
        assert CFG.program_version(".local/bin/uv")

    else:
        assert CFG._uv_bootstrap is None

    assert "Installed pickley v" in cli.logged
    assert CFG.program_version(".local/bin/pickley")


def test_bootstrap_script(cli, monkeypatch):
    # Ensure changes to bstrap.py globals are restored
    cli.main = bstrap.main
    monkeypatch.setattr(bstrap, "DRYRUN", False)  # Protect global variable changes from test runs (avoid polluting other tests)
    monkeypatch.setattr(bstrap, "Reporter", bstrap._Reporter)
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "foo")

    # Verify that we report base not writeable correctly
    cli.run("-nvv")
    assert cli.failed
    assert "Unsetting env var __PYVENV_LAUNCHER__" in cli.logged.stdout
    assert "Make sure '~/.local/bin' exists and is writeable" in cli.logged
    monkeypatch.delenv("__PYVENV_LAUNCHER__")

    runez.ensure_folder(".local/bin", logger=None)
    # Verify that uv is seeded even in dryrun mode
    uv_path = CFG.resolved_path(".local/bin/uv")
    assert not runez.is_executable(uv_path)  # Not seed by conftest.py (it seeds ./uv)
    cli.run("-n", cli.project_folder)
    assert cli.succeeded
    assert ".local/bin/.pk/.cache/pickley-bootstrap-venv/bin/pickley bootstrap " in cli.logged
    if bstrap.USE_UV:
        assert runez.is_executable(uv_path)  # Seeded by bootstrap command run above
        assert ".local/bin/uv -q pip install -e " in cli.logged

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
    sample_config = '"python-installations": "~/.my-pyenv/version/**"'
    monkeypatch.setenv("PATH", ".local/bin:%s" % os.environ["PATH"])
    mirror = "https://pypi.org/simple"
    cli.run("-vv", cli.project_folder, "-m", mirror, "-c", f"{{{sample_config}}}")
    assert cli.succeeded
    assert f"Seeding .config/pip/pip.conf with {mirror}" in cli.logged
    assert list(runez.readlines(".config/pip/pip.conf")) == ["[global]", f"index-url = {mirror}"]
    assert list(runez.readlines(".local/bin/.pk/config.json")) == ["{", f"  {sample_config}", "}"]

    if bstrap.USE_UV:
        uv_config = ".config/uv/uv.toml"
        assert f"Seeding {uv_config} with {mirror}" in cli.logged
        assert list(runez.readlines(uv_config)) == ["[pip]", f'index-url = "{mirror}"']

        # Now verify that uv works with the seeded file
        monkeypatch.setenv("UV_CONFIG_FILE", uv_config)
        r = runez.run(uv_path, "venv", "exercise-venv", fatal=False, logger=None)
        assert r.succeeded, f"uv venv failed: {r.full_output}"

        # Verify that a bogus uv config file fails the run...
        runez.write(uv_config, f"[pip]\nindex-url = {bstrap.DEFAULT_MIRROR}", logger=None)  # Missing double quotes
        r = runez.run(uv_path, "venv", "exercise-venv", fatal=False, logger=None)
        assert r.failed
        assert "Failed to " in r.error


def test_edge_cases(temp_cfg, monkeypatch):
    # For coverage
    monkeypatch.setattr(bstrap, "DRYRUN", False)  # Protect global variable changes from test runs (avoid polluting other tests)
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


@pytest.mark.skipif(not bstrap.USE_UV, reason="Applies to uv only")
def test_uv_bootstrap(temp_cfg):
    # initially present, seeded by conftest.py
    b = bstrap.UvBootstrap(temp_cfg.base)
    assert b.bootstrap_reason() is None

    # Simulate missing uv
    runez.delete("uv", logger=None)
    assert not b.uv_path.exists()
    assert b.bootstrap_reason() == "uv not present"

    # Simulate symlinked uv
    runez.write("some-uv", "#!/bin/sh\necho uv 0.0.1", logger=None)
    runez.make_executable("some-uv", logger=None)
    runez.symlink("some-uv", "uv", logger=None)
    assert b.bootstrap_reason() == "invalid uv file"

    # Simulate wrapped uv
    runez.move("some-uv", "uv", overwrite=True, logger=None)
    assert b.bootstrap_reason() == "replacing uv wrapper"
