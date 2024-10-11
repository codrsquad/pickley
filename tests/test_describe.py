import sys

import runez


def test_describe(cli):
    runez.write(".pk/config.json", '{"bake_time": 300}', logger=None)
    cli.run("-vv describe mgit==1.3.0")
    assert cli.succeeded
    assert " -vv describe " in cli.logged.stdout
    assert "pip show mgit" in cli.logged.stdout
    assert "mgit==1.3.0: mgit version " in cli.logged.stdout
    assert "Applying bake_time of 5 minutes" in cli.logged.stdout

    runez.delete(".pk/config.json", logger=None)
    if sys.version_info[:2] >= (3, 10):
        cli.run("describe .")
        assert cli.failed
        assert "problem: " in cli.logged.stdout

        cli.run("describe uv")
        assert cli.succeeded
        assert "pip show" not in cli.logged
        assert "bake_time" not in cli.logged
        assert "package spec resolved" in cli.logged.stdout

        cli.run("-v describe tox-uv")
        assert cli.succeeded
        assert "pip show" in cli.logged.stdout
        assert "entry points: tox" in cli.logged.stdout

        cli.run("describe https://github.com/codrsquad/pickley.git")
        assert cli.succeeded
        assert "pip spec: git+https://" in cli.logged.stdout
        assert "entry points: pickley" in cli.logged.stdout

        runez.write(".pk/config.json", '{"pinned": {"ansible": "10.4.0"}}', logger=None)
        cli.run("describe ansible")
        assert cli.succeeded
        assert "pip spec: ansible==10.4.0 (pinned by configuration resolved by uv)" in cli.logged.stdout
        assert "entry points: ansible, ansible-config, " in cli.logged.stdout

    cli.run("describe", cli.project_folder)
    assert cli.succeeded
    assert ": pickley version " in cli.logged.stdout
    assert "entry points: pickley\n" in cli.logged.stdout

    cli.run("describe six")
    assert cli.failed
    assert "problem: not a CLI" in cli.logged.stdout
