from pickley import CFG, program_version


def test_alternate_wrapper(cli):
    """Check that flip-flopping between symlink/wrapper works"""
    cli.run("-d foo install mgit")
    assert cli.failed
    assert "Unknown delivery method 'foo'" in cli.logged

    cli.run("install mgit")
    assert cli.succeeded
    assert "Wrapped mgit -> .pk/mgit-" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name("mgit") == "mgit"
    assert CFG.symlinked_canonical("mgit") is None

    cli.run("install mgit")
    assert cli.succeeded
    assert "is already installed" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name("mgit") == "mgit"
    assert CFG.symlinked_canonical("mgit") is None

    cli.run("-d symlink install mgit")
    assert cli.succeeded
    assert "is already installed" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name("mgit") == "mgit"
    assert CFG.symlinked_canonical("mgit") is None

    cli.run("-d symlink install -f mgit")
    assert cli.succeeded
    assert "Symlinked mgit -> .pk/mgit-" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name("mgit") is None
    assert CFG.symlinked_canonical("mgit") == "mgit"

    cli.run("-d wrap install -f mgit")
    assert cli.succeeded
    assert "Wrapped mgit -> .pk/mgit-" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name("mgit") == "mgit"
    assert CFG.symlinked_canonical("mgit") is None
