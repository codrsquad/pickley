import sys

import runez


def test_describe(cli):
    runez.write(".pk/config.json", '{"bake_time": 300}', logger=None)
    cli.run("-vv describe mgit==1.3.0")
    assert cli.succeeded
    assert " -vv describe " in cli.logged
    assert "pip show mgit" in cli.logged
    assert "mgit==1.3.0: mgit version " in cli.logged
    assert "Applying bake_time of 5 minutes" in cli.logged

    if sys.version_info[:2] >= (3, 10):
        cli.run("describe .")
        assert cli.failed
        assert "problem: " in cli.logged

        cli.run("describe uv")
        assert cli.succeeded
        assert "pip show" not in cli.logged
        assert "uv bootstrap" in cli.logged

        cli.run("-v describe tox-uv")
        assert cli.succeeded
        assert "pip show" in cli.logged
        assert "entry points: tox" in cli.logged

        cli.run("describe https://github.com/codrsquad/pickley.git")
        assert cli.succeeded
        assert "pip spec: git+https://" in cli.logged
        assert "entry points: pickley" in cli.logged

        runez.write(".pk/config.json", '{"pinned": {"ansible": "10.4.0"}}', logger=None)
        cli.run("describe ansible")
        assert cli.succeeded
        assert "pip spec: ansible==10.4.0 (pinned by configuration resolved by uv)" in cli.logged
        assert "entry points: ansible, ansible-config, " in cli.logged

    cli.run("describe", cli.project_folder)
    assert cli.succeeded
    assert ": pickley version " in cli.logged
    assert "entry points: pickley\n" in cli.logged

    cli.run("describe six")
    assert cli.failed
    assert "problem: not a CLI" in cli.logged
