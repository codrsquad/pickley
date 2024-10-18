import runez

from pickley import bstrap, CFG, LOG, PackageSpec


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
        assert "Touched .pk/.cache/uv.cooldown" in cli.logged
        assert "Installed uv v" in cli.logged

    # Simulate an incomplete manifest
    mgit = PackageSpec("mgit")
    mgit.settings.auto_upgrade_spec = None
    mgit.save_manifest()
    cli.run("-n install mgit")
    assert cli.succeeded
    assert "reason: incomplete manifest" in cli.logged
    assert CFG.program_version("./mgit", logger=LOG.info)
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None

    # Simulate new version available
    mgit.resolved_info.version = CFG.parsed_version("10.0")
    mgit.save_manifest()
    cli.run("-n upgrade mgit")
    assert cli.succeeded
    assert "reason: new version available" in cli.logged

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

    # Simulate a problem with resolution
    mgit.resolved_info.problem = "oops"
    runez.save_json(mgit.resolved_info.to_dict(), mgit.resolution_cache_path, logger=None)
    cli.run("-n upgrade mgit")
    assert cli.failed
    assert "Can't upgrade mgit: oops" in cli.logged


def test_auto_upgrade(cli):
    # Simulate another upgrade already in progress
    CFG.set_base(".")
    runez.write(CFG.soft_lock_path("mgit"), "f", logger=None)
    cli.run("-n auto-upgrade mgit")
    assert cli.succeeded
    assert "another installation is in progress" in cli.logged
