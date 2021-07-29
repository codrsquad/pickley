import os
import sys
from unittest.mock import patch

import pytest
import runez

from pickley import bstrap


def mocked_expanduser(path):
    if path and path.startswith("~/"):
        path = path[2:]

    return path


def mocked_run(program, *_, **__):
    return program


def test_bootstrap(cli, monkeypatch):
    with patch("pickley.bstrap.os.path.expanduser", side_effect=mocked_expanduser):
        runez.write(".local/bin/pickley", "#!/bin/sh\necho 0.1")  # Pretend we have an old pickley
        runez.make_executable(".local/bin/pickley")

        monkeypatch.setenv("__PYVENV_LAUNCHER__", "oh apple, why?")  # macos oddity env var, should be removed
        cli.run("-n", main=bstrap.main)
        assert cli.succeeded
        assert "__PYVENV_LAUNCHER__" not in os.environ
        assert "Replacing older pickley 0.1" in cli.logged
        assert "Would run: python virtualenv.pyz" in cli.logged
        assert "Would run: .local/bin/.pickley/pickley/pickley-" in cli.logged

        # Simulate seeding
        cli.run("0.1", "-m", "my-mirror", "-c", '{"pyenv":"~/.pyenv"}', main=bstrap.main)
        assert cli.succeeded
        assert "Seeding .local/bin/.pickley/config.json with {'pyenv': '~/.pyenv'}" in cli.logged
        assert "Seeding .config/pip/pip.conf with my-mirror" in cli.logged
        assert "pickley version 0.1 is already installed" in cli.logged
        assert runez.readlines(".config/pip/pip.conf") == ["[global]", "index-url = my-mirror"]
        assert runez.readlines(".local/bin/.pickley/config.json") == ["{", '  "pyenv": "~/.pyenv"', "}"]

        with patch("pickley.bstrap.is_executable", return_value=False):
            # Simulate no python 3
            cli.run("-n", main=bstrap.main)
            assert cli.failed
            assert "Could not find python3 on this machine" in cli.logged


def test_edge_cases(temp_folder, monkeypatch):
    bstrap.DRYRUN = False
    assert bstrap.which("python")  # Check that which() works

    monkeypatch.setattr(sys, "base_prefix", sys.prefix)  # Pretend we don't run from a venv
    assert bstrap.find_python3() == sys.executable

    with pytest.raises(SystemExit) as exc:
        bstrap.run_program(sys.executable, "--no-such-option")
    assert "'python' exited with code" in str(exc)

    with patch("pickley.bstrap.built_in_download", side_effect=Exception):  # urllib fails
        with patch("pickley.bstrap.run_program", side_effect=mocked_run):  # mocked_run() returns just the program name
            with patch("pickley.bstrap.which", side_effect=lambda x: x if x == "curl" else None):
                assert bstrap.download("test", "test") == "curl"

            with patch("pickley.bstrap.which", side_effect=lambda x: x if x == "wget" else None):
                assert bstrap.download("test", "test") == "wget"

            with patch("pickley.bstrap.which", return_value=None):
                with pytest.raises(SystemExit) as exc:
                    bstrap.download("test", "test")
                assert "No curl, nor wget" in str(exc)
