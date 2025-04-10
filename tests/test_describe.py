import sys

import runez


def test_describe(cli, monkeypatch):
    monkeypatch.setenv("UV_VENV_SEED", "1")
    cli.run("describe pickley==1.0")
    assert cli.succeeded
    assert "pickley: version 1.0 (pinned)\n" in cli.logged.stdout
    assert "entry-points: pickley\n" in cli.logged.stdout

    runez.write(".pk/config.json", '{"bake_time": 300, "pinned": {"cowsay": {"python": "3.999"}}}', logger=None)
    cli.run("-vv describe mgit==1.3.0")
    assert cli.succeeded
    assert " -vv describe " in cli.logged.stdout
    assert "pip show mgit" in cli.logged.stdout
    assert "mgit: version 1.3.0" in cli.logged.stdout
    assert "Applying bake_time of 5 minutes" in cli.logged.stdout

    cli.run("-vv describe cowsay")
    assert cli.failed
    assert "Invalid python: 3.999 [not available]" in cli.logged

    runez.delete(".pk/config.json", logger=None)
    if sys.version_info[:2] >= (3, 10):
        cli.run("describe .")
        assert cli.failed
        assert "problem: " in cli.logged.stdout

        cli.run("-vv describe uv")
        assert cli.succeeded
        assert "pip show" not in cli.logged
        assert "bake_time" not in cli.logged
        assert "(package spec resolved by uv)" in cli.logged.stdout

        cli.run("-vv describe tox-uv")
        assert cli.succeeded
        assert "pip show" in cli.logged.stdout
        assert "tox-uv: version " in cli.logged.stdout
        assert "entry-points: tox\n" in cli.logged.stdout

        cli.run("describe https://github.com/codrsquad/pickley.git")
        assert cli.succeeded
        assert "entry-points: pickley" in cli.logged.stdout

        runez.write(".pk/config.json", '{"pinned": {"ansible": "10.4.0"}}', logger=None)
        cli.run("describe ansible")
        assert cli.succeeded
        assert "ansible: version 10.4.0 (pinned by configuration resolved by uv)\n" in cli.logged.stdout
        assert "entry-points: ansible, ansible-config, " in cli.logged.stdout

    cli.run("describe", cli.project_folder)
    assert cli.succeeded
    assert "pickley: version " in cli.logged.stdout
    assert "entry-points: pickley\n" in cli.logged.stdout

    cli.run("describe six")
    assert cli.failed
    assert "problem: not a CLI" in cli.logged.stdout
    assert "entry-points: -no entry points-" in cli.logged.stdout

    # Simulate overriding entry points determination
    runez.write(".pk/config.json", '{"entrypoints": {"six": "six"}}', logger=None)
    cli.run("describe six")
    assert cli.succeeded
    assert "entry-points: six" in cli.logged.stdout
