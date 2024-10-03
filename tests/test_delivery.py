import sys

import pytest

from pickley import CFG, program_version


@pytest.mark.skipif(sys.version_info[:2] < (3, 10), reason="pkg_resource issue with mgit")
def test_alternate_wrapper(cli):
    """Check that flip-flopping between symlink/wrapper works"""
    cli.run("-d foo install mgit")
    assert cli.failed
    assert "Unknown delivery method 'foo'" in cli.logged

    mgit_path = CFG.resolved_path("mgit")
    cli.run("install mgit")
    assert cli.succeeded
    assert "Wrapped mgit -> .pk/mgit-" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None

    cli.run("install mgit")
    assert cli.succeeded
    assert "is already installed" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None

    cli.run("-d symlink install mgit")
    assert cli.succeeded
    assert "is already installed" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None

    cli.run("-d symlink install -f mgit")
    assert cli.succeeded
    assert "Symlinked mgit -> .pk/mgit-" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name(mgit_path) is None
    assert CFG.symlinked_canonical(CFG.resolved_path(mgit_path)) == "mgit"

    cli.run("-d wrap install -f mgit")
    assert cli.succeeded
    assert "Wrapped mgit -> .pk/mgit-" in cli.logged
    assert program_version("./mgit")
    assert CFG.wrapped_canonical_name(mgit_path) == "mgit"
    assert CFG.symlinked_canonical(mgit_path) is None
