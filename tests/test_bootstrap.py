import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import runez

from pickley import bstrap, Reporter
from pickley.cli import CFG


def test_bootstrap_command(cli, monkeypatch):
    cli.run("-n", "bootstrap", ".local/bin", cli.project_folder)
    assert cli.failed
    assert "Folder .local/bin does not exist" in cli.logged

    runez.ensure_folder(".local/bin", logger=None)
    cli.run("--no-color", "-vv", "bootstrap", ".local/bin", cli.project_folder)
    assert cli.succeeded
    assert "Saved .pk/.manifest/.bootstrap.json" in cli.logged
    assert "Installed pickley v" in cli.logged
    assert CFG.program_version(".local/bin/pickley")
    if bstrap.USE_UV:
        assert CFG._uv_bootstrap.freshly_bootstrapped == "uv not present"
        assert "Auto-bootstrapping uv, reason: uv not present" in cli.logged
        assert "[bootstrap] Saved .pk/.manifest/uv.manifest.json" in cli.logged
        assert CFG.program_version(".local/bin/uv")

        # Simulate an old uv semi-venv present
        runez.touch(".local/bin/.pk/uv-0.0.1/bin/uv", logger=None)
        monkeypatch.setenv("PICKLEY_ROOT", ".local/bin")
        cli.run("-vv", "install", "-f", "uv")
        assert cli.succeeded
        assert "Deleted .pk/uv-0.0.1" in cli.logged

    else:
        # Verify that no uv bootstrap took place
        assert "/uv" not in cli.logged
        assert CFG._uv_bootstrap is None


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

    # Now satisfy the existing base folder requirement
    runez.ensure_folder(".local/bin", logger=None)

    # Verify that uv is seeded even in dryrun mode
    uv_path = CFG.resolved_path(".local/bin/uv")
    assert not runez.is_executable(uv_path)  # Not seeded by conftest.py (it seeds ./uv)

    # Simulate bogus mirror, verify that we fail bootstrap in that case
    cli.run("-nvv", cli.project_folder, "-mhttp://localhost:12345")
    assert cli.succeeded
    assert "Would seed .config/pip/pip.conf with http://localhost:12345" in cli.logged
    assert "Setting PIP_INDEX_URL and UV_INDEX_URL to http://localhost:12345" in cli.logged
    assert cli.match("Would run: .../.local/bin/.pk/.cache/pickley-bootstrap-venv/bin/pickley -vv bootstrap")

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
    mirror = "https://pypi.org/simple"

    # Seeding is best-effort (bootstrap still succeeds)
    runez.touch(".config/pip", logger=None)
    runez.touch(".config/uv", logger=None)
    cli.run("-vv", cli.project_folder, "-m", mirror)
    assert cli.succeeded
    assert "Seeding ~/.config/pip/pip.conf failed" in cli.logged
    assert "Seeding ~/.config/uv/uv.toml failed" in cli.logged

    # Verify successful seeding (clean all files generated from above runs)
    runez.delete(".config", logger=None)
    runez.ensure_folder(".local/bin", clean=True, logger=None)
    sample_config = '"python-installations": "~/.my-pyenv/version/**"'
    cli.run("-vv", cli.project_folder, "-m", mirror, "-c", f"{{{sample_config}}}")
    assert cli.succeeded
    info = runez.read_json(".local/bin/.pk/.manifest/.bootstrap.json")
    assert info["vpickley"]
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

    assert "/1.0/" in bstrap.UvBootstrap.uv_url("1.0")

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
