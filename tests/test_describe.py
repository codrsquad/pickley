import runez

from pickley import __version__, bstrap


def test_describe(cli):
    cli.run("describe uv")
    assert cli.succeeded
    assert "uv bootstrap" in cli.logged

    runez.write(".pk/config.json", '{"bake_time": 300}', logger=None)
    cli.run("describe mgit==1.3.0")
    assert cli.succeeded
    assert "mgit==1.3.0: mgit version " in cli.logged
    assert "Applying bake_time of 5 minutes" in cli.logged

    if bstrap.USE_UV:
        runez.write(".pk/config.json", '{"pinned": {"ansible": "10.4.0"}}', logger=None)
        cli.run("describe ansible")
        assert cli.succeeded
        assert "pip spec: ansible==10.4.0 (pinned by configuration resolved by uv)" in cli.logged
        assert "entry points: ansible, ansible-config, " in cli.logged

    cli.run("describe", cli.project_folder)
    assert cli.succeeded
    assert f"pickley version {__version__}" in cli.logged
    assert "pickley dev mode" in cli.logged

    cli.run("describe six")
    assert cli.succeeded
    assert "problem: not a CLI" in cli.logged