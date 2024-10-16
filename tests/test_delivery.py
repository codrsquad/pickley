from pickley import bstrap, CFG, LOG


def test_alternate_wrapper(cli):
    """Check that flip-flopping between symlink/wrapper works"""
    cli.run("-d foo install mgit")
    assert cli.failed
    assert "Unknown delivery method 'foo'" in cli.logged

    mgit_path = CFG.resolved_path("mgit")
    cli.run("-v install mgit")
    assert cli.succeeded
    assert "Wrapped mgit -> .pk/mgit-" in cli.logged
    assert CFG.program_version("./mgit", logger=LOG.info)
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None

    cli.run("install mgit")
    assert cli.succeeded
    assert "is already installed" in cli.logged
    assert CFG.program_version("./mgit", logger=LOG.info)
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None

    if bstrap.USE_UV:
        cli.run("--no-color -vv install uv")
        assert cli.succeeded
        assert "Manifest .pk/.manifest/uv.manifest.json is not present" in cli.logged
        assert "Move .pk/.cache/uv-" in cli.logged
        assert "Touched .pk/.cache/uv.cooldown" in cli.logged
        assert "Installed uv v" in cli.logged

    cli.run("-d symlink install mgit")
    assert cli.succeeded
    assert "is already installed" in cli.logged
    assert CFG.program_version("./mgit", logger=LOG.info)
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None

    cli.run("-v -d symlink install -f mgit")
    assert cli.succeeded
    assert "Symlinked mgit -> .pk/mgit-" in cli.logged
    assert CFG.program_version("./mgit", logger=LOG.info)
    assert CFG.wrapped_canonical_name(mgit_path) is None
    assert CFG.symlinked_canonical(CFG.resolved_path(mgit_path)) == "mgit"

    cli.run("-v -d wrap install -f mgit")
    assert cli.succeeded
    assert "Wrapped mgit -> .pk/mgit-" in cli.logged
    assert CFG.program_version("./mgit", logger=LOG.info)
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None
