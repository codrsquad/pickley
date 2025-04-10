import hashlib
import logging
import os
import platform
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

import runez
from runez.pyenv import PypiStd, PythonDepot, Version

from pickley import bstrap
from pickley.bstrap import PICKLEY

LOG = logging.getLogger(__name__)
DEFAULT_VERSION_CHECK_DELAY = 300
K_CLI = {"delivery", "index", "python"}
K_DIRECTIVES = {"include"}
K_GROUPS = {"bundle", "pinned"}
K_LEAVES = {
    "facultative",
    "install_timeout",
    "package_manager",
    "preferred_pythons",
    "python_installations",
    "pyenv",
    "version",
    "version_check_delay",
}
PLATFORM = platform.system().lower()


class Reporter:
    """Allows to nicely capture logging from `bstrap` module (which is limited to std lib only otherwise)"""

    _original_tracer = None  # `runez.log.trace()` original tracer function
    _pending_records = None  # Holds records to be emitted to `audit.log` later (when applicable)

    @staticmethod
    def abort(message):
        """Allows to reuse `runez.abort()` from `bstrap` module (when not running in bootstrap mode)"""
        runez.abort(message)

    @staticmethod
    def trace(message):
        """Allows `bstrap` module to use tracing"""
        Reporter._captured_trace(message)

    @staticmethod
    def debug(message):
        """Allows `bstrap` module to use LOG.info()"""
        LOG.debug(message)

    @staticmethod
    def inform(message):
        """Allows `bstrap` module to use LOG.info()"""
        LOG.info(message)

    @staticmethod
    def capture_trace():
        """Set up tracing."""
        tracer = runez.log.tracer
        if tracer:
            # Tracing is already active (for commands `install`, `upgrade`, etc.), let's capture it.
            Reporter._original_tracer = tracer.trace
            tracer.trace = Reporter._captured_trace

        if CFG.use_audit_log:
            if not runez.log.tracer:
                # Tracing is not active on stderr because user did not use `-vv`, we still want to capture all messages in 'audit.log'
                runez.log.tracer = Reporter

            if not runez.log.file_handler:
                # 'audit.log' is not active yet, but can potentially be activated later (commands `auto-upgrade`, etc.)
                Reporter._pending_records = []

    @staticmethod
    def flush_pending_records():
        """'audit.log' was just activated, emit all pending records to it."""
        if Reporter._pending_records:
            for pending in Reporter._pending_records:
                runez.log.file_handler.emit(pending)

            Reporter._pending_records = None

    @staticmethod
    def _captured_trace(message):
        if Reporter._original_tracer:
            # Pass through to original tracer (which will show trace messages on stderr)
            Reporter._original_tracer(message)

        if CFG.use_audit_log:
            record = LOG.makeRecord(bstrap.PICKLEY, logging.DEBUG, "unknown file", 0, message, (), None)
            if runez.log.file_handler is not None:
                # 'audit.log' is active, emit trace message to it
                runez.log.file_handler.emit(record)

            elif Reporter._pending_records is not None:
                # 'audit.log' is not active yet, let's capture all trace messages in memory for now
                Reporter._pending_records.append(record)


bstrap.Reporter = Reporter


class PipMetadata:
    """Info about a package as extracted from running `pip show`."""

    canonical_name: str = None
    problem: str = None
    values: dict = None

    def update_from_pip_show(self, venv, canonical_name):
        self.canonical_name = canonical_name
        self.values = {}
        r = venv.run_pip("show", canonical_name, fatal=False)
        if r.failed:  # pragma: no cover, hard to trigger without excessive mocking
            self.problem = f"Failed to `pip show {canonical_name}`: {r.full_output}"
            return

        for line in r.output.splitlines():
            k, _, v = line.partition(":")
            k = k.lower().strip()
            v = v.strip()
            if k and v:
                self.values[k] = v

    @property
    def location(self):
        return self.values.get("location")

    @property
    def version(self):
        return self.values.get("version")


class ResolvedPackage:
    """
    Resolve a given package spec to a canonical name and version.

    A "package spec" is anything acceptable to `pip install`, examples:
    - package-name
    - package-name==1.0.0
    - package-name<2
    - git+https://...
    - /path/to/project
    """

    given_package_spec: str
    canonical_name: str = None  # Canonical pypi package name
    entrypoints: Optional[Sequence[str]] = None  # Entry points, if any
    pip_spec: List[str] = None  # One of: `<name>==<version>`, or `<url>`, or `-e <path>`
    problem: Optional[str] = None  # Problem with package spec, if any
    resolution_reason: Optional[str] = None  # How version to use was resolved
    version: Version = None  # Resolved version

    logger = runez.log.trace
    _metadata: PipMetadata = None  # Available only after `resolve()` has been called (not kept in cache)

    def __repr__(self):
        return runez.short(self.given_package_spec)

    def to_dict(self):
        return {
            "given_package_spec": self.given_package_spec,
            "canonical_name": self.canonical_name,
            "entrypoints": self.entrypoints,
            "pip_spec": self.pip_spec,
            "problem": self.problem,
            "resolution_reason": self.resolution_reason,
            "version": self.version,
        }

    def _set_canonical(self, name, version):
        self.canonical_name = PypiStd.std_package_name(name)
        if version and not isinstance(version, Version):
            version = Version(version)

        self.version = version

    @classmethod
    def from_cache(cls, cache_path):
        if CFG.version_check_delay and runez.file.is_younger(cache_path, CFG.version_check_delay):
            data = runez.read_json(cache_path)
            if isinstance(data, dict) and "given_package_spec" in data:
                runez.log.trace(f"Using cached resolved info from {runez.short(cache_path)}")
                info = cls()
                info.set_attributes(
                    data.get("given_package_spec"),
                    data.get("canonical_name"),
                    data.get("entrypoints"),
                    data.get("pip_spec"),
                    data.get("problem"),
                    data.get("resolution_reason"),
                    data.get("version"),
                )
                return info

    def set_attributes(self, given_package_spec, canonical_name, entrypoints, pip_spec, problem, resolution_reason, version):
        if not isinstance(version, Version):
            version = Version(version)

        self.given_package_spec = given_package_spec
        self.canonical_name = canonical_name
        self.entrypoints = entrypoints
        self.pip_spec = pip_spec
        self.problem = problem
        self.resolution_reason = resolution_reason
        self.version = version

    def resolve(self, settings: "TrackedSettings"):
        from pickley.package import PythonVenv

        self.given_package_spec = settings.auto_upgrade_spec
        self.pip_spec = [self.given_package_spec]
        self.problem = None
        self.resolution_reason = None
        pip_spec = self.given_package_spec
        canonical_name, version = CFG.despecced(self.given_package_spec)
        if version:
            self.resolution_reason = "pinned"
            if canonical_name == bstrap.PICKLEY:
                self._set_canonical(canonical_name, version)
                self.entrypoints = CFG.configured_entrypoints(bstrap.PICKLEY)
                return

        elif canonical_name == self.given_package_spec:
            version = CFG.get_value("version", package_name=canonical_name)
            if version:
                pip_spec = f"{canonical_name}=={version}"
                self.resolution_reason = "pinned by configuration"

        with runez.TempFolder(dryrun=False):
            venv_settings = settings.venv_settings()
            venv_settings.uv_seed = False
            venv = PythonVenv(runez.to_path("tmp-venv"), venv_settings, groom_uv_venv=False)
            venv.logger = self.logger
            venv.create_venv()
            bake_time = runez.to_int(CFG.get_value("bake_time", package_name=canonical_name))
            env = None
            if bake_time:
                # uv allows to exclude newer packages, but pip does not
                # This can fail if project is new (bake time did not elapse yet since project release)
                LOG.debug("Applying bake_time of %s", runez.represented_duration(bake_time))
                ago = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(time.time() - bake_time))
                env = dict(os.environ)
                env["UV_EXCLUDE_NEWER"] = ago

            r = venv.pip_install(pip_spec, no_deps=True, quiet=True, fatal=False, env=env)
            if r.failed:
                lines = r.full_output.strip().splitlines()
                if lines:
                    lines[0] = runez.red(lines[0])
                    if len(lines) > 4:  # pragma: no cover, hard to trigger, happens when a wheel can't be built for example
                        # Truncate pip's output to the first 4 lines (in `uv`, they're the most relevant)
                        runez.log.trace(f"Full output of 'pip install {pip_spec}':\n{r.full_output}")
                        lines = lines[:4]

                self.problem = runez.joined(lines, delimiter="\n") or f"Resolution failed for {pip_spec}"
                return

            r = venv.run_pip("freeze", fatal=False)
            lines = r.output and r.output.strip().splitlines()
            # Edge case: older pythons venvs sometimes report having `pkg_resources`, even with --no-deps
            if lines:
                lines = [x for x in lines if not x.startswith("pkg_resources")]

            if not lines or len(lines) != 1:  # pragma: no cover, hard to trigger (not sure how to make `pip freeze` fail)
                self.problem = f"'pip freeze' for '{runez.joined(pip_spec)}' failed: {r.full_output}"
                return

            location = None
            line = lines[0]
            canonical_name, version = CFG.despecced(line)
            if version:
                if canonical_name == self.given_package_spec:
                    self.pip_spec = [f"{canonical_name}=={version}"]

                if not self.resolution_reason:
                    self.resolution_reason = "package spec"

            else:
                canonical_name = line.partition(" ")[0]
                version, location = self._get_version_location(venv, canonical_name)
                self.resolution_reason = "project reference"

            self.resolution_reason = f"{self.resolution_reason} resolved by {venv_settings.package_manager}"
            self._set_canonical(canonical_name, version)
            ep = self._get_entry_points(venv, canonical_name, version, location)
            self.entrypoints = sorted(n for n in ep if "_completer" not in n)
            if not self.entrypoints:
                self.problem = runez.red("not a CLI")

            if CFG.is_dev_mode and self.given_package_spec in (bstrap.PICKLEY, runez.DEV.project_folder):
                # Dev mode: install pickley from source in editable mode
                self.pip_spec = ["-e", runez.DEV.project_folder]

    def _get_entry_points(self, venv, canonical_name, version, location):
        # Use `uv pip show` to get location on disk and version of package
        eps = CFG.configured_entrypoints(canonical_name)
        if eps:
            return eps

        ep_name = self._ep_name(canonical_name)
        if ep_name != canonical_name:
            location = None
            venv.pip_install(ep_name, no_deps=True, quiet=True, fatal=False)

        if not version or not location:
            version, location = self._get_version_location(venv, ep_name)

        location = runez.to_path(location)
        wheel_name = PypiStd.std_wheel_basename(ep_name)
        folder = location / f"{wheel_name}-{version}.dist-info"
        declared_entry_points = runez.file.ini_to_dict(folder / "entry_points.txt")
        if declared_entry_points and "console_scripts" in declared_entry_points:
            console_scripts = declared_entry_points["console_scripts"]
            if console_scripts and isinstance(console_scripts, dict):
                # Package has a standard entry_points.txt file
                return console_scripts.keys()

        # No standard entry_points.txt, let's try to find executables in bin/
        # For example: `awscli` does this (no proper entry points, has bin-scripts only)
        entry_points = []
        for line in runez.readlines(folder / "RECORD"):
            if line.startswith(".."):
                path = line.partition(",")[0]
                dirname = os.path.dirname(path)
                if os.path.basename(dirname) == "bin":
                    entry_points.append(os.path.basename(path))

        if not entry_points and "tox" in declared_entry_points:
            # Special case for `tox` plugins (is there a better way to detect this?)
            entry_points.append("tox")

        return entry_points

    @staticmethod
    def _ep_name(package_name):
        if package_name == "ansible":
            # Is there a better way to detect weird indirections like ansible does?
            return "ansible-core"

        return package_name

    def _get_version_location(self, venv, canonical_name):
        self._metadata = PipMetadata()
        self._metadata.update_from_pip_show(venv, canonical_name)
        version = self._metadata.version
        location = self._metadata.location
        runez.abort_if(self._metadata.problem)
        return version, location


class PackageSpec:
    """
    This class represents a package spec, and provides access to its resolved info and current installation state.
    """

    auto_upgrade_spec: str
    _manifest: "TrackedManifest" = runez.UNSET
    _resolved_info: ResolvedPackage = None

    def __init__(self, given_package_spec: str, authoritative=False, settings=None):
        """
        Parameters
        ----------
        given_package_spec : str
            Provided package reference (either name, folder or git url)
        authoritative : bool
            If True-ish, the `given_package_spec` will be used as package spec when upgrading (otherwise prev manifest is used)
            If a ResolvedPackage instance, it will be used as the authoritative resolution
        settings : TrackedSettings
            Settings to use for this package spec (applies only to uv)
        """
        given_package_spec = CFG.absolute_package_spec(given_package_spec)
        self._canonical_name = PypiStd.std_package_name(given_package_spec)
        self.is_uv = self._canonical_name == "uv"
        if settings:
            self.auto_upgrade_spec = settings.auto_upgrade_spec

        elif authoritative:
            self.auto_upgrade_spec = given_package_spec
            runez.log.trace(f"Authoritative auto-upgrade spec '{self.auto_upgrade_spec}'")

        elif self._canonical_name:
            # Non-authoritative specs are necessarily canonical names (since only authoritative specs can refer to git urls, etc.)
            manifest = self.manifest
            if manifest and manifest.settings and manifest.settings.auto_upgrade_spec:
                # Use previously saved authoritative auto-upgrade spec
                runez.log.trace(f"Using previous authoritative auto-upgrade spec '{manifest.settings.auto_upgrade_spec}'")
                self.auto_upgrade_spec = manifest.settings.auto_upgrade_spec

            else:
                # Manifest was produced by an older pickley prior to v4.4
                runez.log.trace(f"Assuming auto-upgrade spec '{self._canonical_name}'")
                self.auto_upgrade_spec = self._canonical_name

        else:
            # Should not be reachable, unless we are given a non-authoritative spec that is not a canonical name
            self.auto_upgrade_spec = given_package_spec

        cache_file_name = self.auto_upgrade_spec
        if PypiStd.std_package_name(cache_file_name) != cache_file_name:
            # If package spec is not a canonical name, use md5 hash of it as filename
            cache_file_name = hashlib.md5(cache_file_name.encode()).hexdigest()

        self.resolution_cache_path = CFG.cache / f"{cache_file_name}.resolved.json"
        if self._resolved_info is None:
            self._resolved_info = ResolvedPackage.from_cache(self.resolution_cache_path)

        self.settings = settings or TrackedSettings.from_cli(self.auto_upgrade_spec)

    def __repr__(self):
        return runez.short(self.auto_upgrade_spec)

    @property
    def canonical_name(self) -> str:
        if self._canonical_name is None:
            # Full resolution is needed because we have been given an authoritative spec (example `git+https://...`)
            self._canonical_name = self.resolved_info.canonical_name
            self.is_uv = self._canonical_name == "uv"

        return self._canonical_name

    @runez.cached_property
    def manifest_path(self):
        return CFG.manifests / f"{self.canonical_name}.manifest.json"

    @property
    def problem(self):
        return self.resolved_info.problem

    @property
    def resolved_info(self):
        if self._resolved_info is None:
            info = ResolvedPackage()
            info.resolve(self.settings)
            if not info.problem:
                payload = info.to_dict()
                runez.save_json(payload, self.resolution_cache_path, fatal=None, logger=runez.log.trace)

            self._resolved_info = info

        return self._resolved_info

    @property
    def target_version(self) -> Version:
        """The version of this package that we are targeting for installation"""
        return self.resolved_info.version

    @property
    def manifest(self) -> Optional["TrackedManifest"]:
        """Manifest of the current installation of this package, if any"""
        if self._manifest is runez.UNSET:
            self._manifest = TrackedManifest.from_file(self.manifest_path)

        return self._manifest

    @property
    def currently_installed_version(self):
        if self.is_uv:
            # For `uv`, no need to trust the manifest, we can just dynamically ask what's its version
            return CFG.program_version(self.healthcheck_exe)

        manifest = self.manifest
        return manifest and manifest.version

    @runez.cached_property
    def is_facultative(self) -> bool:
        """Is this package optional? (ie: OK if present but not installed by pickley)"""
        return runez.to_boolean(CFG.get_value("facultative", package_name=self.canonical_name))

    @runez.cached_property
    def healthcheck_exe(self) -> Path:
        """Executable used to determine whether installation is healthy"""
        if self.is_uv:
            return CFG.base / "uv"

        manifest = self.manifest
        version = (manifest and manifest.version) or self.target_version
        return CFG.meta / f"{self.canonical_name}-{version}/bin/python"

    def delivery_method_name(self) -> str:
        return self.settings.delivery or CFG.get_value("delivery", package_name=self.canonical_name)

    def is_healthily_installed(self, entrypoints_only=False) -> bool:
        """Is the venv for this package spec still usable?"""
        manifest = self.manifest
        entrypoints = (manifest and manifest.entrypoints) or self.resolved_info.entrypoints
        if entrypoints:
            for name in entrypoints:
                if not runez.is_executable(CFG.base / name):
                    return False

        return entrypoints_only or bool(CFG.program_version(self.healthcheck_exe))

    def target_installation_folder(self):
        """Folder that will hold current installation of this package (does not apply to uv)"""
        if not self.is_uv:
            return CFG.meta / f"{self.canonical_name}-{self.target_version}"

    def upgrade_reason(self):
        """Reason this package spec needs an upgrade (if any)"""
        if self.currently_installed_version != self.target_version:
            return f"new version available, current version is {self.currently_installed_version}"

        manifest = self.manifest
        if not manifest:
            return "manifest missing"

        if not manifest.settings or not manifest.settings.auto_upgrade_spec:
            return "incomplete manifest"

        if not self.is_healthily_installed():
            return "unhealthy"

    @staticmethod
    def _mentions_pickley(path: Path):
        for line in runez.readlines(path, first=7):
            if bstrap.PICKLEY in line:
                return True

    def is_clear_for_installation(self) -> bool:
        """True if we can proceed with installation without needing to uninstall anything"""
        if self.is_uv:
            return True

        if self.resolved_info.entrypoints:
            for ep in self.resolved_info.entrypoints:
                path = CFG.base / ep
                if path.exists() and os.path.getsize(path) > 0 and (path.is_symlink() or runez.is_executable(path)):
                    if not CFG.symlinked_canonical(path) and not self._mentions_pickley(path):
                        return False

        return True

    def uninstall_all_files(self):
        """Uninstall all files related to this package spec"""
        runez.delete(self.manifest_path, fatal=False, logger=runez.log.trace)
        for candidate, _ in CFG.installed_sibling_folders(self.canonical_name):
            runez.delete(candidate, fatal=False, logger=runez.log.trace)

    def groom_installation(self):
        """Groom installation folder, keeping only the latest version, and prev versions a day."""
        CFG.groom_cache()
        if self.is_uv:
            for candidate, _ in CFG.installed_sibling_folders("uv"):
                # Old pickley versions used to wrap 'uv', we don't do that anymore, cleanup leftovers
                runez.delete(candidate, fatal=False)

            return

        # Minimum time in days for how long to keep the previous latest version, not officially configured, but config used by tests
        age_cutoff = runez.to_int(CFG.get_value("installation_retention", package_name=self.canonical_name))
        if age_cutoff is None:
            age_cutoff = runez.date.SECONDS_IN_ONE_DAY

        candidates = []
        manifest = self.manifest
        now = time.time()
        current_age = None
        for candidate, version in CFG.installed_sibling_folders(self.canonical_name):
            age = now - os.path.getmtime(candidate)
            if manifest and version == str(manifest.version):
                # We want current version to not be a candidate for deletion,
                # but use its age to determine whether it's OK to delete previous version N-1
                current_age = age

            else:
                candidates.append((age, candidate))

        if candidates:
            candidates = sorted(candidates)
            for candidate in candidates[1:]:
                runez.delete(candidate[1], fatal=False, logger=runez.log.trace)

            if current_age and current_age >= age_cutoff:
                # Clean previous installation (N-1) if it is older than `age_cutoff`
                runez.delete(candidates[0][1], fatal=False, logger=runez.log.trace)

    def save_manifest(self):
        manifest = TrackedManifest()
        self._manifest = manifest
        venv_settings = self.settings.venv_settings()
        manifest.entrypoints = self.resolved_info.entrypoints
        manifest.install_info = TrackedInstallInfo.current()
        manifest.settings = self.settings
        manifest.version = self.target_version
        if not self.is_uv:
            manifest.delivery = self.delivery_method_name()
            manifest.package_manager = venv_settings.package_manager
            manifest.python_executable = venv_settings.python_executable

        if self.canonical_name == PICKLEY:
            runez.save_json(manifest.install_info, CFG.manifests / ".bootstrap.json")

        payload = manifest.to_dict()
        runez.save_json(payload, self.manifest_path)
        folder = self.target_installation_folder()
        if folder:
            runez.save_json(payload, folder / ".manifest.json")

        # Touch .cooldown file to let auto-upgrade know we just installed this package
        cooldown_path = CFG.cache / f"{self.canonical_name}.cooldown"
        runez.touch(cooldown_path)
        return manifest


class PickleyConfig:
    """Pickley configuration"""

    base: Optional[Path] = None  # Installation folder
    meta: Optional[Path] = None  # DOT_META subfolder
    cache: Optional[Path] = None  # DOT_META/.cache subfolder
    manifests: Optional[Path] = None
    cli_config: Optional[dict] = None  # Tracks any custom CLI cfg flags given, such as --index, --python or --delivery
    configs: List["RawConfig"]
    version_check_delay: int = DEFAULT_VERSION_CHECK_DELAY

    is_dev_mode = False
    pickley_version = runez.get_version(bstrap.PICKLEY)
    use_audit_log = False  # If True, capture log in .pk/audit.log
    verbosity = 0
    _pip_conf = runez.UNSET
    _pip_conf_index = runez.UNSET
    _uv_bootstrap: Optional[bstrap.UvBootstrap] = None  # Computed once, overridden during tests

    def __init__(self):
        self.configs = []
        self.config_path = None

    def reset(self):
        """Used for testing"""
        self.base = None
        self.meta = None
        self.cache = None
        self.cli_config = None
        self.configs = []
        self.config_path = None
        self.is_dev_mode = False
        self._pip_conf = runez.UNSET
        self._pip_conf_index = runez.UNSET
        self._uv_bootstrap = None

    def __repr__(self):
        return "<not-configured>" if self.base is None else runez.short(self.base)

    @staticmethod
    def absolute_package_spec(given_package_spec: str) -> str:
        if given_package_spec.startswith("http"):
            given_package_spec = f"git+{given_package_spec}"

        if re.match(r"^(file:|https?:|git[@+])", given_package_spec):
            return given_package_spec

        if given_package_spec.startswith(".") or "/" in given_package_spec:
            return str(CFG.resolved_path(given_package_spec))

        return given_package_spec

    @staticmethod
    def parsed_version(text):
        """Parse --version from text, in reverse order to avoid being fooled by warnings..."""
        if text:
            for line in reversed(text.splitlines()):
                version = Version.extracted_from_text(line)
                if version and version.is_valid:
                    return version

    @staticmethod
    def program_version(path, logger=None):
        if runez.is_executable(path):
            r = runez.run(path, "--version", dryrun=False, fatal=False, logger=logger)
            if r.succeeded:
                return PickleyConfig.parsed_version(r.output or r.error)

    @staticmethod
    def installed_sibling_folders(canonical_name):
        """
        Sibling installations of the form '<canonical_name>-[<version>]'.
        Intent of this is to find and clean older installations (to liberate disk space).
        """
        for item in runez.ls_dir(CFG.meta):
            if item.is_dir() and item.name.startswith(f"{canonical_name}-"):
                version_part = item.name[len(canonical_name) + 1 :]
                if not version_part or version_part[0].isdigit():
                    # Edge case: bug in previous versions of pickley that yielded a "uv-" folder for example (seen in the wild)
                    yield item, version_part

    @staticmethod
    def resolved_path(path, base=None) -> Path:
        """
        Temporary: to be cleaned up when runez returns `Path` throughout as well.
        This function turns any string or path into a fully resolved (ie: `~` expanded) absolute path.
        """
        return runez.to_path(runez.resolved_path(path, base=base))

    @staticmethod
    def required_canonical_name(text):
        canonical_name = PypiStd.std_package_name(text)
        runez.abort_if(not canonical_name, f"'{runez.red(text)}' is not a canonical pypi package name")
        return canonical_name

    @runez.cached_property
    def available_pythons(self):
        locations = runez.flattened(self.get_value("python_installations") or "PATH")
        depot = PythonDepot(*locations)
        preferred = runez.flattened(self.get_value("preferred_pythons"), split=",")
        depot.set_preferred_python(preferred)
        return depot

    @property
    def default_index(self):
        """Default pypi mirror index, as configured by pip.conf (global or user)"""
        return self.pip_conf_index or bstrap.DEFAULT_MIRROR

    @property
    def pip_conf(self):
        """Path to pip.conf file where user/machine's default pypi mirror is defined"""
        if self._pip_conf is runez.UNSET:
            self._pip_conf_index, self._pip_conf = bstrap.globally_configured_pypi_mirror()

        return self._pip_conf

    @property
    def pip_conf_index(self):
        """Default mirror as configured by user/machine pip.conf"""
        if self._pip_conf_index is runez.UNSET:
            self._pip_conf_index, self._pip_conf = bstrap.globally_configured_pypi_mirror()

        return self._pip_conf_index

    @property
    def uv_bootstrap(self):
        if self._uv_bootstrap is None:
            self._uv_bootstrap = bstrap.UvBootstrap(self.base)
            self._uv_bootstrap.auto_bootstrap_uv()

        return self._uv_bootstrap

    def configured_entrypoints(self, canonical_name) -> Optional[list]:
        """Configured entrypoints, if any"""
        eps = self.get_value("entrypoints")
        if isinstance(eps, dict):
            # This allows to optionally configure entrypoints, shall the need arise
            value = runez.flattened(eps.get(canonical_name))
            if value:
                return value

        return bstrap.KNOWN_ENTRYPOINTS.get(canonical_name)

    def require_bootstrap(self):
        """
        Require that we are running in a bootstrapped pickley environment.
        This allows to verify that one is not running `pickley install` from a temporary environment,
        such as one created by `uvx pickley ...` for example.
        """
        bootstrap_info_path = CFG.manifests / ".bootstrap.json"
        contents = runez.read_json(bootstrap_info_path)
        if not contents and self.is_dev_mode:
            contents = {"vpickley": self.pickley_version}

        info = TrackedInstallInfo.from_dict(contents)
        runez.abort_if(not info or not info.vpickley, "This command applies only to bootstrapped pickley installations")

    def set_base(self, base_path):
        """
        Parameters
        ----------
        base_path : Path | str
            Path to pickley base installation
        """
        self.configs = []
        self.base = self.resolved_path(base_path)
        self.is_dev_mode = self.base.name == "dev_mode"
        self.meta = self.base / bstrap.DOT_META
        self.cache = self.meta / ".cache"
        self.manifests = self.meta / ".manifest"
        if self.cli_config is not None:
            self.configs.append(RawConfig(self, "cli", self.cli_config))

        self._add_config_file(self.config_path)
        self._add_config_file(self.meta / "config.json")
        defaults = {
            "delivery": "wrap",
            "install_timeout": 1800,
            "version_check_delay": DEFAULT_VERSION_CHECK_DELAY,
        }
        self.configs.append(RawConfig(self, "defaults", defaults))
        self.version_check_delay = runez.to_int(self.get_value("version_check_delay"), default=DEFAULT_VERSION_CHECK_DELAY)

    def set_cli(self, config_path, delivery, index, python, package_manager):
        """
        Parameters
        ----------
        config_path : str | None
            Optional configuration to use
        delivery : str | None
            Optional delivery method to use
        index : str | None
            Optional pypi index to use
        python : str | None
            Optional python interpreter to use
        package_manager : str | None
            Optional package manager to use
        """
        self.config_path = config_path
        cli_config = {"delivery": delivery, "index": index, "python": python, "package_manager": package_manager}
        self.cli_config = runez.serialize.json_sanitized(cli_config)

    def _add_config_file(self, path, base=None):
        path = CFG.resolved_path(path, base=base)
        if path and all(c.source != path for c in self.configs) and os.path.exists(path):
            values = runez.read_json(path, logger=LOG.warning)
            if values:
                self.configs.append(RawConfig(self, path, values))
                included = values.get("include")
                if included:
                    for additional in runez.flattened(included):
                        self._add_config_file(additional, base=os.path.dirname(path))

    def _expand_bundle(self, result, seen, bundle_name):
        if not bundle_name or bundle_name in seen:
            return

        seen.add(bundle_name)
        if not bundle_name.startswith("bundle:"):
            result.append(bundle_name)
            return

        names = self.get_nested("bundle", bundle_name[7:])
        if names:
            for name in runez.flattened(names, split=" "):
                self._expand_bundle(result, seen, name)

    def symlinked_canonical(self, path: Path) -> Optional[str]:
        """Canonical name of pickley-installed package, if installed via symlink"""
        if path and self.meta and os.path.islink(path):
            actual_path = path.resolve()
            try:
                relative = actual_path.relative_to(self.meta)
                pv = relative.parts[0]
                return pv.rpartition("-")[0]

            except ValueError:
                runez.log.trace(f"Symlink {runez.short(path)} -> {runez.short(actual_path)} does not belong to {bstrap.PICKLEY}")
                return None

    def soft_lock_path(self, canonical_name):
        """str: Path to lock file used during installation for this package"""
        return self.meta / f"{canonical_name}.lock"

    def package_specs(self, names: Sequence[str], authoritative=False):
        """
        Parameters
        ----------
        names : Sequence[str]
            Package names, if empty: return all installed
        authoritative : bool
            If True, the given package specs are considered authoritative, will be used as package spec when upgrading

        Returns
        -------
        List[PackageSpec]
            Corresponding PackageSpec-s
        """
        names = runez.flattened(names, split=" ")
        if not authoritative:
            names = [CFG.required_canonical_name(n) for n in names]

        result = [self.resolved_bundle(name) for name in names]
        result = runez.flattened(result, unique=True)
        return [PackageSpec(name, authoritative=authoritative) for name in result]

    @staticmethod
    def wrapped_canonical_name(path):
        """(str | None): Canonical name of installed python package, if installed via pickley wrapper"""
        if runez.is_executable(path):
            for line in runez.readlines(path, first=12):
                if line.startswith("# pypi-package:"):
                    return line[15:].strip()

    def scan_installed(self):
        """Scan installed"""
        for item in runez.ls_dir(self.base):
            spec_name = self.symlinked_canonical(item) or self.wrapped_canonical_name(item)
            if spec_name:
                yield spec_name

        for item in runez.ls_dir(self.manifests):
            if item.name.endswith(".manifest.json"):
                spec_name = item.name[:-14]
                if spec_name:
                    yield spec_name

    def installed_specs(self, include_pickley=False):
        """(list[PackageSpec]): Currently installed package specs"""
        spec_names = set(self.scan_installed())
        if include_pickley:
            spec_names.add(bstrap.PICKLEY)

        return [PackageSpec(x) for x in sorted(spec_names)]

    def get_nested(self, section, key):
        """
        Args:
            section (str): Nested section to examine
            key (str): Key to look up in nested section

        Returns:
            Nested value from first RawConfig that defines it
        """
        for c in self.configs:
            value = c.get_nested(section, key)
            if value is not None:
                return value

    def get_value(self, key, package_name=None, validator=None):
        """
        Args:
            key (str): Key to look up
            package_name (str | None): Use specific value for stated `package_name` when available
            validator (callable | None): Validator to use

        Returns:
            Value from first RawConfig that defines it
        """
        for c in self.configs:
            value = c.get_value(key, package_name, validator)
            if value is not None:
                return value

    @property
    def index(self):
        """Pypi index (mirror) to use."""
        return self.get_value("index") or self.default_index

    def install_timeout(self, package_name):
        """
        Args:
            package_name (str | None): Use specific value for stated `package_name` when available

        Returns:
            (int): How many seconds to give an installation to complete before assuming it failed
        """
        return self.get_value("install_timeout", package_name=package_name, validator=runez.to_int)

    def resolved_bundle(self, name):
        """
        Args:
            name (str): Name of bundle to resolve

        Returns:
            (list): List of expanded package names included in the bundle
        """
        result = []
        self._expand_bundle(result, set(), name)
        return result

    @staticmethod
    def colored_key(key, indent):
        if (key in K_CLI or key in K_LEAVES) and indent in (1, 3):
            return runez.teal(key)

        if key in K_DIRECTIVES and indent == 1:
            return runez.dim(key)

        if key in K_GROUPS and indent == 1:
            return runez.purple(key)

        if indent == 2:
            return runez.bold(key)

        return runez.red(key)

    def represented(self):
        """str: Human readable representation of this configuration"""
        result = [f"{runez.bold('base')}: {self}", ""]
        for c in self.configs:
            result.append(c.represented())

        return "\n".join(result).strip()

    def groom_cache(self):
        """Delete all files in DOT_META/.cache/ folder that are older than `cache_age`"""
        if self.cache and self.cache.is_dir():
            age_cutoff = runez.to_int(self.get_value("cache_retention"))
            if age_cutoff is None:
                age_cutoff = runez.date.SECONDS_IN_ONE_DAY

            now = time.time()
            for candidate in runez.ls_dir(self.cache):
                age = now - os.path.getmtime(candidate)
                if age and age >= age_cutoff:
                    runez.delete(candidate, fatal=False, logger=runez.log.trace)

    @staticmethod
    def despecced(text):
        """
        Args:
            text (str): Text of form <name>==<version>, or just <name>

        Returns:
            (str, str | None): Name and version
        """
        version = None
        if text and "==" in text:
            text = text.strip()
            i = text.index("==")
            version = text[i + 2 :].strip() or None
            text = text[:i].strip()

        return text, version


CFG = PickleyConfig()


class TrackedManifest:
    """Info stored in .manifest.json for each installation"""

    entrypoints: Sequence[str] = None  # Entry points seen when package was installed
    delivery: str = None  # Delivery method used when package was installed
    install_info: "TrackedInstallInfo" = None  # Info on which pickley run performed the installation
    package_manager: str = None  # Package manager used when package was installed
    python_executable: str = None  # Python interpreter used when package was installed
    settings: "TrackedSettings" = None  # Resolved settings used when package was installed
    version: Version = None  # Version of package installed

    def __repr__(self):
        return repr(self.settings)

    @classmethod
    def from_file(cls, path):
        if path.exists():
            data = runez.read_json(path, logger=None)
            if data:
                manifest = cls()
                manifest.entrypoints = data.get("entrypoints")
                manifest.delivery = data.get("delivery")
                manifest.install_info = TrackedInstallInfo.from_dict(data.get("install_info"))
                manifest.package_manager = data.get("package_manager")
                manifest.python_executable = data.get("python")
                manifest.settings = TrackedSettings.from_dict(data.get("tracked_settings"))
                manifest.version = Version(data.get("version"))
                return manifest

        runez.log.trace(f"Manifest {runez.short(path)} is not present")

    def to_dict(self):
        return {
            "entrypoints": self.entrypoints,
            "delivery": self.delivery,
            "install_info": self.install_info.to_dict(),
            "package_manager": self.package_manager,
            "python": self.python_executable,
            "tracked_settings": self.settings.to_dict(),
            "version": str(self.version) if self.version else None,
        }


class TrackedInstallInfo:
    """Info on which pickley run performed the installation"""

    args: str = None  # CLI args with which pickley was invoked
    index: str = None  # Index (mirror) used for installation
    timestamp: datetime = None
    vpickley: str = None  # Version of pickley that performed the installation

    @classmethod
    def current(cls):
        info = TrackedInstallInfo()
        info.args = runez.quoted(sys.argv[1:])
        info.index = CFG.index
        info.timestamp = datetime.now()
        info.vpickley = CFG.pickley_version
        return info

    @classmethod
    def from_dict(cls, data):
        if data:
            info = TrackedInstallInfo()
            info.args = data.get("args")
            info.index = data.get("index")
            info.timestamp = runez.to_datetime(data.get("timestamp"))
            info.vpickley = data.get("vpickley")
            return info

    def to_dict(self):
        return {
            "args": self.args,
            "index": self.index,
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "vpickley": self.vpickley,
        }


class VenvSettings:
    """Allows to define in one place how package_manager and python installation are to be resolved"""

    def __init__(self, canonical_name, python_spec, package_manager, uv_seed=None):
        if not python_spec:
            python_spec = CFG.get_value("python", package_name=canonical_name)

        self.python_spec = python_spec
        self.python_installation = CFG.available_pythons.find_python(python_spec)
        if not package_manager:
            package_manager = CFG.get_value("package_manager", package_name=canonical_name)

        if not package_manager and self.python_installation.mm:
            package_manager = bstrap.default_package_manager(self.python_installation.mm.major, self.python_installation.mm.minor)

        self.package_manager = package_manager or bstrap.default_package_manager()
        self.python_executable = self.python_installation.executable
        self.uv_seed = uv_seed


class TrackedSettings:
    """
    Resolved config settings to use when installing a package.
    """

    auto_upgrade_spec: str = None  # Spec to use for `pickley auto-upgrade`
    delivery: str = None  # Delivery method name
    package_manager: str = None  # Desired package manager
    python: Optional[str] = None  # Desired python
    uv_seed: bool = None  # Long term: CLIs should not assume setuptools is always there... (same problem with py3.12)

    def __repr__(self):
        return self.auto_upgrade_spec

    def venv_settings(self) -> VenvSettings:
        canonical_name = PypiStd.std_package_name(self.auto_upgrade_spec)
        uv_seed = self.uv_seed or CFG.get_value("uv_seed", package_name=canonical_name)
        return VenvSettings(canonical_name, self.python, self.package_manager, uv_seed=uv_seed)

    @classmethod
    def from_cli(cls, auto_upgrade_spec: str):
        settings = cls()
        canonical_name = PypiStd.std_package_name(auto_upgrade_spec)
        settings.auto_upgrade_spec = canonical_name or auto_upgrade_spec
        settings.delivery = CFG.cli_config.get("delivery")
        settings.package_manager = CFG.cli_config.get("package_manager")
        settings.python = CFG.cli_config.get("python")
        settings.uv_seed = CFG.cli_config.get("uv_seed")
        return settings

    @classmethod
    def from_dict(cls, data):
        if data:
            settings = cls()
            settings.auto_upgrade_spec = data.get("auto_upgrade_spec")
            settings.delivery = data.get("delivery")
            settings.package_manager = data.get("package_manager")
            settings.python = data.get("python")
            settings.uv_seed = data.get("uv_seed")
            return settings

    def to_dict(self):
        return {
            "auto_upgrade_spec": self.auto_upgrade_spec,
            "delivery": self.delivery,
            "package_manager": self.package_manager,
            "python": self.python,
            "uv_seed": self.uv_seed,
        }


class RawConfig:
    """Represents one configuration source: one particular file, or hardcoded defaults"""

    def __init__(self, parent, source, values):
        self.parent = parent
        self.source = source
        self.values = values

    def __repr__(self):
        return f"{runez.short(self.source)} ({runez.plural(self.values)})"

    def get_nested(self, section, key):
        """
        Args:
            section (str): Nested section to examine
            key (str): Key to look up in nested section

        Returns:
            Value, if any
        """
        section_value = self.values.get(section)
        if isinstance(section_value, dict):
            return section_value.get(key)

    def get_value(self, key, package_name, validator):
        """
        Args:
            key (str): Key to look up
            package_name (str | None): Use specific value for stated `package_name` when available
            validator (callable | None): Validator to use

        Returns:
            Value, if any
        """
        if package_name:
            pinned = self.get_nested("pinned", package_name)
            if isinstance(pinned, str) and key == "version":
                return pinned

            if isinstance(pinned, dict):
                value = pinned.get(key)
                if validator is not None:
                    value = validator(value)

                if value:
                    return value

        value = self.values.get(key)
        if validator is not None:
            value = validator(value)

        return value

    def _add_dict_representation(self, result, data, indent=1):
        """
        Args:
            result (list): Where to add lines representing 'data'
            data (dict): Data to represent
            indent (int): Indentation to use
        """
        padding = "  " * indent
        for key, value in sorted(data.items()):
            key = self.parent.colored_key(key, indent)
            if isinstance(value, dict):
                result.append(f"{padding}{key}:")
                self._add_dict_representation(result, value, indent=indent + 1)

            elif isinstance(value, list):
                result.append(f"{padding}{key}:")
                for item in value:
                    result.append(f"{padding} - {runez.short(item)}")

            else:
                result.append(f"{padding}{key}: {runez.short(value)}")

    def represented(self):
        """str: Human readable representation of this configuration"""
        result = [f"{runez.bold(runez.short(self.source))}:"]
        if self.values:
            self._add_dict_representation(result, self.values)

        else:
            result[0] += runez.dim("  # empty")

        result.append("")
        return "\n".join(result)
