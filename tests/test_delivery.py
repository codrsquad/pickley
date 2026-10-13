import os

import runez

from pickley import CFG, LOG, PackageSpec
from pickley.delivery import DeliveryMethodWrap


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

    cli.run("--no-color -vv install uv")
    assert cli.succeeded
    assert "Manifest .pk/.manifest/uv.manifest.json is not present" in cli.logged
    assert "Touched .pk/.cache/uv.cooldown" in cli.logged
    assert "Installed uv v" in cli.logged

    # Simulate a manifest produced by pickley prior to v4.4 (no `auto_upgrade_spec`)
    mgit = PackageSpec("mgit")
    mgit.save_manifest()
    manifest = runez.read_json(mgit.manifest_path)
    del manifest["tracked_settings"]["auto_upgrade_spec"]
    runez.save_json(manifest, mgit.manifest_path)
    cli.run("-n -vv install mgit")
    assert cli.succeeded
    assert "Manifest .pk/.manifest/mgit.manifest.json is invalid" in cli.logged
    assert "Would wrap mgit -> .pk/mgit-" in cli.logged
    assert CFG.program_version("./mgit", logger=LOG.info)
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None

    # Simulate new version available
    mgit.resolved_info.version = CFG.parsed_version("10.0")
    mgit.save_manifest()
    cli.run("-n upgrade mgit")
    assert cli.succeeded
    assert "reason: new version available" in cli.logged

    cli.run("-n install mgit")
    assert cli.succeeded
    assert "Would state: Installed mgit v" in cli.logged
    assert "Would state: Would state" not in cli.logged
    assert "(reason: new version available, current version is 10.0)" in cli.logged

    # Simulate an entry point that the package does not provide anymore, it should get cleaned up on next installation
    manifest = runez.read_json(mgit.manifest_path)
    manifest["entrypoints"].append("mgit-old")
    runez.save_json(manifest, mgit.manifest_path, logger=None)
    runez.touch("mgit-old", logger=None)

    cli.run("-v -d symlink install -f mgit")
    assert cli.succeeded
    assert "Symlinked mgit -> .pk/mgit-" in cli.logged
    assert "Deleted mgit-old" in cli.logged
    assert not os.path.exists("mgit-old")
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


def test_delivery_failure(cli, monkeypatch):
    def simulated_failure(*_):
        raise OSError("simulated failure")

    monkeypatch.setattr(DeliveryMethodWrap, "_install", simulated_failure)
    cli.run("install mgit")
    assert cli.failed
    assert "Failed to wrap mgit: simulated failure" in cli.logged
