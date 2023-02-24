import logging
import os
import sys
from unittest.mock import patch

import pytest
import runez

from pickley import __version__, bstrap


def mocked_expanduser(path):
    if path and path.startswith("~/"):
        path = path[2:]

    return path


def mocked_logged_run(program, *args, **__):
    logging.info("Running %s %s" % (program, " ".join(args)))


def mocked_run(program, *_, **__):
    return program


def mocked_which(program):
    return None if program == "pickley" else program


def test_bootstrap(cli, monkeypatch):
    cli.run("-n base bootstrap-own-wrapper")
    assert cli.succeeded
    assert f"Would save .pickley/pickley/pickley-{__version__}/.manifest.json" in cli.logged

    with patch("pickley.bstrap.which", side_effect=mocked_which):
        with patch("pickley.bstrap.os.path.expanduser", side_effect=mocked_expanduser):
            runez.write(".local/bin/pickley", "#!/bin/sh\necho 0.1")  # Pretend we have an old pickley
            runez.make_executable(".local/bin/pickley")

            with patch("pickley.bstrap.get_python_version", return_value=(3, 6)):  # urllib fails
                monkeypatch.setenv("__PYVENV_LAUNCHER__", "oh apple, why?")  # macos oddity env var, should be removed
                monkeypatch.setenv("PATH", ".local/bin:%s" % os.environ["PATH"])
                cli.run("-n", main=bstrap.main)
                assert cli.succeeded
                assert "__PYVENV_LAUNCHER__" not in os.environ
                assert "Replacing older pickley 0.1" in cli.logged
                assert "Would run: python virtualenv-20.10.0.pyz -q --clear --pip 21.3.1 -p " in cli.logged
                assert "Would run: .local/bin/.pickley/pickley/pickley-" in cli.logged
                monkeypatch.undo()

            # Simulate multiple base candidates given
            cli.run("-n", "-b", "~/.local/bin:foo/bar", main=bstrap.main)
            assert cli.failed
            assert "Make sure ~/.local/bin is writeable and in your PATH variable." in cli.logged

            # Simulate seeding
            monkeypatch.setenv("PATH", ".local/bin:%s" % os.environ["PATH"])
            cli.run("0.1", "-b", "~/.local/bin", "-m", "my-mirror", "-c", '{"pyenv":"~/.pyenv"}', main=bstrap.main)
            assert cli.succeeded
            assert "Seeding .local/bin/.pickley/config.json with {'pyenv': '~/.pyenv'}" in cli.logged
            assert "Seeding .config/pip/pip.conf with my-mirror" in cli.logged
            assert "pickley version 0.1 is already installed" in cli.logged
            assert list(runez.readlines(".config/pip/pip.conf")) == ["[global]", "index-url = my-mirror"]
            assert list(runez.readlines(".local/bin/.pickley/config.json")) == ["{", '  "pyenv": "~/.pyenv"', "}"]

            monkeypatch.setenv("PATH", "foo/bar:%s" % os.environ["PATH"])
            runez.ensure_folder("foo/bar", logger=None)
            cli.run("-n", "-b", "bar/baz:foo/bar", main=bstrap.main)
            assert cli.succeeded
            assert "base: foo/bar" in cli.logged

            with patch("pickley.bstrap.built_in_download"):
                with patch("pickley.bstrap.get_python_version", return_value=(3, 10)):
                    with patch("pickley.bstrap.run_program", side_effect=mocked_logged_run):
                        # Verify that a python without ensurepip still works (via virtualenv)
                        cli.run("1.0", main=bstrap.main)
                        assert cli.succeeded
                        assert " -mvenv --clear " in cli.logged
                        assert "virtualenv-20.10.0.pyz -q --clear --download -p " in cli.logged

                        # When pip available, don't use virtualenv
                        pip = ".local/bin/.pickley/pickley/pickley-1.0/bin/pip3"
                        runez.touch(pip)
                        runez.make_executable(pip)
                        cli.run("1.0", main=bstrap.main)
                        assert cli.succeeded
                        assert "virtualenv" not in cli.logged
                        assert "pip -q install pickley==1.0" in cli.logged
                        assert "pickley base bootstrap-own-wrapper" in cli.logged

            with patch("pickley.bstrap.which", return_value=None):
                with patch("pickley.bstrap.is_executable", return_value=False):
                    # Simulate no python 3
                    cli.run("-n", main=bstrap.main)
                    assert cli.failed
                    assert "Could not find python3 on this machine" in cli.logged

            with patch("pickley.bstrap.get_python_version", return_value=(3, 9)):
                cli.run("-n", main=bstrap.main)
                assert cli.succeeded
                assert " -mvenv " in cli.logged


def test_edge_cases(temp_folder, monkeypatch, logged):
    monkeypatch.setattr(bstrap, "DRYRUN", True)
    cmd = bstrap.virtualenv_cmd("vv", (3, 6), "pyexe", "venv")
    assert cmd == [sys.executable, "vv", "-q", "--clear", "--pip", "21.3.1", "-p", "pyexe", "venv"]
    cmd = bstrap.virtualenv_cmd("vv", (3, 7), "pyexe", "venv")
    assert cmd == [sys.executable, "vv", "-q", "--clear", "--download", "-p", "pyexe", "venv"]

    monkeypatch.setattr(bstrap, "DRYRUN", False)
    assert bstrap.which("python3")  # Check that which() works

    monkeypatch.setattr(bstrap, "RUNNING_FROM_VENV", False)
    assert bstrap.find_python3() == sys.executable

    assert not logged
    with pytest.raises(SystemExit) as exc:
        bstrap.run_program(sys.executable, "--no-such-option")
    assert "'python' exited with code" in str(exc)
    assert "Running: python --no-such-option" in logged.pop()

    assert bstrap.run_program(sys.executable, "--version") == 0
    assert "Running: python --version" in logged.pop()

    with patch("pickley.bstrap.built_in_download", side_effect=Exception):  # urllib fails
        with patch("pickley.bstrap.run_program", side_effect=mocked_run):  # mocked_run() returns just the program name
            with patch("pickley.bstrap.which", side_effect=lambda x: "/bin/%s" % x if x in ("curl", "pickley") else None):
                assert bstrap.find_base(None) == "/bin"
                assert "Found existing /bin/pickley" in logged.pop()
                assert bstrap.download("test", "test") == "/bin/curl"

            with patch("pickley.bstrap.which", side_effect=lambda x: x if x == "wget" else None):
                assert bstrap.download("test", "test") == "wget"

            with patch("pickley.bstrap.which", return_value=None):
                with pytest.raises(SystemExit) as exc:
                    bstrap.download("test", "test")
                assert "No curl, nor wget" in str(exc)

    assert not logged
