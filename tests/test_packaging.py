import os
import time

from mock import patch

from pickley import CaptureOutput, system
from pickley.install import PexRunner
from pickley.package import DeliveryCopy, DeliveryMethod, DeliverySymlink, DeliveryWrap, PexPackager, VenvPackager, VersionMeta
from pickley.settings import Definition, SETTINGS

from .conftest import INEXISTING_FILE, verify_abort


def test_delivery(temp_base):
    # Test copy folder
    deliver = DeliveryCopy("tox")
    target = os.path.join(temp_base, "t1")
    source = os.path.join(temp_base, "t1-source")
    source_file = os.path.join(source, "foo")
    system.touch(source_file)
    deliver.install(target, source)
    assert os.path.isdir(target)
    assert os.path.isfile(os.path.join(target, "foo"))

    # Test copy file
    deliver = DeliveryCopy("tox")
    target = os.path.join(temp_base, "t2")
    source = os.path.join(temp_base, "t2-source")
    system.touch(source)
    deliver.install(target, source)
    assert os.path.isfile(target)

    p = VenvPackager("tox")
    assert str(p) == "venv tox"
    target = os.path.join(temp_base, "tox")
    source = os.path.join(temp_base, "tox-source")
    system.touch(source)
    deliver = DeliveryWrap("tox")
    deliver.install(target, source)
    assert system.is_executable(target)

    # Cover edge case for make_executable():
    system.make_executable(target)
    assert system.is_executable(target)


def test_bogus_delivery():
    deliver = DeliveryMethod("foo")
    assert "does not exist" in verify_abort(deliver.install, None, INEXISTING_FILE)

    deliver = DeliverySymlink("foo")
    assert "Failed symlink" in verify_abort(deliver.install, None, __file__)


def test_version_meta():
    v = VersionMeta("foo")
    v2 = VersionMeta("foo")
    assert v.equivalent(v2)
    v2.packager = "bar"
    assert not v.equivalent(v2)

    assert not v.equivalent(None)
    assert str(v) == "foo: no version"
    assert v.representation(verbose=True) == "foo: no version"
    v.invalidate("some problem")
    assert str(v) == "foo: some problem"
    assert v.problem == "some problem"

    v3 = VersionMeta("foo")
    v3.version = "1.0"
    v3.timestamp = time.time()
    assert v3.still_valid
    v3.timestamp = "foo"
    assert not v3.still_valid


@patch("pickley.package.latest_pypi_version", return_value=None)
@patch("pickley.package.DELIVERERS.resolved", return_value=None)
def test_versions(_, __, temp_base):
    p = PexPackager("foo")
    p.refresh_desired()
    assert p.desired.representation(verbose=True) == "foo: can't determine latest version (as pex, channel: latest, source: pypi)"

    SETTINGS.cli.contents["channel"] = "stable"
    p.refresh_desired()
    assert p.desired.representation() == "foo: can't determine stable version"

    assert "can't determine stable version" in verify_abort(p.install)

    # Without a build fodler
    assert p.get_entry_points(p.build_folder, "0.0.0") is None

    # With an empty build fodler
    system.ensure_folder(p.build_folder, folder=True)
    assert p.get_entry_points(p.build_folder, "0.0.0") is None

    # With a bogus wheel
    with CaptureOutput() as logged:
        whl = os.path.join(p.build_folder, "foo-0.0.0-py2.py3-none-any.whl")
        system.touch(whl)
        assert p.get_entry_points(p.build_folder, "0.0.0") is None
        assert "Can't read wheel" in logged
        system.delete_file(whl)

    # Ambiguous package() call
    assert "Need either source_folder or version in order to package" in verify_abort(p.package)

    # Package bogus folder without a setup.py
    p.source_folder = temp_base
    assert "No setup.py" in verify_abort(p.package)

    # Package with a bogus setup.py
    setup_py = os.path.join(temp_base, "setup.py")
    system.touch(setup_py)
    assert "Could not determine version" in verify_abort(p.package)

    # Provide a minimal setup.py
    system.write_contents(setup_py, "from setuptools import setup\nsetup(name='foo', version='0.0.0')\n")

    # Package project without entry points
    p.get_entry_points = lambda *_: None
    assert "is not a CLI" in verify_abort(p.required_entry_points)

    # Simulate presence of entry points
    p.get_entry_points = lambda *_: ["foo"]

    # Simulate pip wheel failure
    p.pip_wheel = lambda *_: "failed"
    assert "pip wheel failed" in verify_abort(p.package)

    # Simulate pex failure
    p.pip_wheel = lambda *_: None
    p.pex_build = lambda *_: "failed"
    assert "pex command failed" in verify_abort(p.package)

    SETTINGS.set_cli_config()


@patch("pickley.package.VenvPackager.virtualenv_path", return_value=None)
def test_configured_version(_):
    p = VenvPackager("foo")
    assert "Can't determine path to virtualenv.py" in verify_abort(p.effective_package, None)


def get_definition(key, package_name=None):
    if key == "channel":
        return Definition("stable", "test", key)
    if key.startswith("channel.stable."):
        return Definition("1.0", "test", key)
    if key == "delivery":
        return Definition("wrap", "test", key)
    return None


@patch("pickley.settings.SettingsFile.get_definition", side_effect=get_definition)
def test_channel(_):
    p = VenvPackager("foo")
    p.refresh_desired()
    assert p.desired.representation(verbose=True) == "foo 1.0 (as venv wrap, channel: stable, source: test:channel.stable.foo)"


def pydef(value, source="test"):
    def callback(*_):
        return Definition(value, source, key="python")
    return callback


def test_shebang():
    p = PexRunner(None)
    p.run = lambda *args: system.info("args: %s", system.represented_args(*args))

    # Universal wheels
    p.is_universal = lambda *_: True

    # Default python, absolute path
    p.resolved_python = pydef("/some-python", source=SETTINGS.defaults)
    with CaptureOutput() as logged:
        p.build(None, None, None, None)
        assert "--python=" not in logged
        assert "--python-shebang=/usr/bin/env python" in logged

    # Default python, relative path
    p.resolved_python = pydef("some-python", source=SETTINGS.defaults)
    with CaptureOutput() as logged:
        p.build(None, None, None, None)
        assert "--python=" not in logged
        assert "--python-shebang=/usr/bin/env python" in logged

    # Explicit python, absolute path
    p.resolved_python = pydef("/some-python")
    with CaptureOutput() as logged:
        p.build(None, None, None, None)
        assert "--python=/some-python" in logged
        assert "--python-shebang=/some-python" in logged

    # Explicit python, relative path
    p.resolved_python = pydef("some-python")
    with CaptureOutput() as logged:
        p.build(None, None, None, None)
        assert "--python=some-python" in logged
        assert "--python-shebang=/usr/bin/env some-python" in logged

    # Non-universal wheels
    p.is_universal = lambda *_: False

    # Default python, absolute path
    p.resolved_python = pydef("/some-python", source=SETTINGS.defaults)
    with CaptureOutput() as logged:
        p.build(None, None, None, None)
        assert "--python=" not in logged
        assert "--python-shebang=/some-python" in logged

    # Default python, relative path
    p.resolved_python = pydef("some-python", source=SETTINGS.defaults)
    with CaptureOutput() as logged:
        p.build(None, None, None, None)
        assert "--python=" not in logged
        assert "--python-shebang=/usr/bin/env some-python" in logged

    # Explicit python, absolute path
    p.resolved_python = pydef("/some-python")
    with CaptureOutput() as logged:
        p.build(None, None, None, None)
        assert "--python=/some-python" in logged
        assert "--python-shebang=/some-python" in logged

    # Explicit python, relative path
    p.resolved_python = pydef("some-python")
    with CaptureOutput() as logged:
        p.build(None, None, None, None)
        assert "--python=some-python" in logged
        assert "--python-shebang=/usr/bin/env some-python" in logged
