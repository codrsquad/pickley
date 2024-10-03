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

__version__ = "4.3.3"
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


def abort(message):
    message = runez.stringified(message)
    print(message)
    _log_to_file(message, error=True)
    sys.exit(1)


def inform(message):
    """
    Args:
        message: Message to print and log at level INFO
    """
    message = runez.stringified(message)
    print(message)
    _log_to_file(message)


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


def specced(name, version):
    """
    Args:
        name (str): Pypi package name
        version (str | None): Version

    Returns:
        (str): Specced name==version
    """
    name = name.strip()
    if version and version.strip():
        return f"{name}=={version.strip()}"

    return name


def pypi_name_problem(name):
    if not PypiStd.is_acceptable(name):
        note = None
        problem = f"'{runez.red(name)}' is not a valid pypi package name"
        if name and not name[0].isalpha():
            note = "\npickley intentionally refuses to look at names that don't start with a letter"

        if note:
            note += "\nIf you think this name is legit, please submit an issue https://github.com/codrsquad/pickley/issues"
            problem = runez.joined(problem, note, delimiter="\n")

        return problem


def _absolute_package_spec(given_package_spec: str):
    if given_package_spec.startswith("http"):
        given_package_spec = f"git+{given_package_spec}"

    if re.match(r"^(file:|https?:|git[@+])", given_package_spec):
        return given_package_spec

    if given_package_spec.startswith(".") or "/" in given_package_spec:
        return str(CFG.resolved_path(given_package_spec))

    return given_package_spec


def parsed_version(text):
    """Parse --version from text, in reverse order to avoid being fooled by warnings..."""
    if text:
        for line in reversed(text.splitlines()):
            version = Version.extracted_from_text(line)
            if version and version.is_valid:
                return version


def program_version(path):
    if runez.is_executable(path):
        r = runez.run(path, "--version", dryrun=False, fatal=False, logger=None)
        if r.succeeded:
            return parsed_version(r.output or r.error)


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
    pip_spec: List[str] = None  # CLI args to pass to `pip install`
    problem: Optional[str] = None  # Problem with package spec, if any
    resolution_reason: Optional[str] = None  # How version to use was resolved
    venv_basename: str = None  # The basename of the .pk/ venv pickley uses to install this
    version: Version = None  # Resolved version

    logger = runez.log.trace

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
            "venv_basename": self.venv_basename,
            "version": self.version,
        }

    def _set_canonical(self, name, version):
        self.canonical_name = PypiStd.std_package_name(name)
        if version and not isinstance(version, Version):
            version = Version(version)

        self.version = version
        self.venv_basename = f"{self.canonical_name}-{version}" if self.canonical_name and version else None

    @classmethod
    def from_cache(cls, cache_path):
        if CFG.version_check_delay and runez.file.is_younger(cache_path, CFG.version_check_delay):
            data = runez.read_json(cache_path)
            if isinstance(data, dict) and "given_package_spec" in data:
                info = cls()
                info.given_package_spec = data.get("given_package_spec")
                info.canonical_name = data.get("canonical_name")
                info.entrypoints = data.get("entrypoints")
                info.pip_spec = data.get("pip_spec")
                info.problem = data.get("problem")
                info.resolution_reason = data.get("resolution_reason")
                info.venv_basename = data.get("venv_basename")
                info.version = Version(data.get("version"))
                return info

    def resolve(self, settings: "TrackedSettings"):
        from pickley.package import PythonVenv

        self.given_package_spec = settings.auto_upgrade_spec
        self.pip_spec = [self.given_package_spec]
        self.problem = None
        self.resolution_reason = None
        if settings.auto_upgrade_spec == runez.DEV.project_folder:
            # Dev mode: install pickley from source in editable mode
            self.canonical_name = bstrap.PICKLEY
            self.entrypoints = (bstrap.PICKLEY,)
            self.pip_spec = ["-e", runez.DEV.project_folder]
            self.resolution_reason = "pickley dev mode"
            self.venv_basename = f"{bstrap.PICKLEY}-dev"
            self.version = Version(__version__)
            return

        if self.given_package_spec == "uv":
            # `uv` is a special case, it's used for bootstrap and does not need a venv
            uv_version = program_version(CFG.find_uv())
            if uv_version:
                self._set_canonical(self.given_package_spec, uv_version)
                self.entrypoints = ("uv", "uvx")
                self.resolution_reason = "uv bootstrap"
                return

        pip_spec = self.given_package_spec
        canonical_name, version = despecced(self.given_package_spec)
        if version:
            pip_spec = f"{canonical_name}=={version}"
            self.resolution_reason = "pinned"

        elif settings.pinned_version:
            pip_spec = f"{canonical_name}=={settings.pinned_version}"
            self.resolution_reason = "pinned by configuration"

        with runez.TempFolder(dryrun=False):
            venv = PythonVenv(runez.to_path("tmp-venv"), package_manager=settings.package_manager, python_spec=settings.python)
            venv.groom_uv_venv = False
            venv.logger = self.logger
            venv.create_venv()
            if settings.bake_time:
                # uv allows to exclude newer packages, but pip does not
                # This can fail if project is new (bake time did not elapse yet since project release)
                LOG.debug("Applying bake_time of %s", runez.represented_duration(settings.bake_time))
                ago = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(time.time() - settings.bake_time))
                os.environ["UV_EXCLUDE_NEWER"] = ago

            r = venv.pip_install(pip_spec, no_deps=True, quiet=True, fatal=False)
            if not r:
                lines = r.full_output.strip().splitlines()
                if lines:
                    lines[0] = runez.red(lines[0])
                    if len(lines) > 4:  # pragma: no cover, hard to trigger, happens when a wheel can't be built for example
                        # Truncate pip's output to the first 4 lines (in `uv`, they're the most relevant)
                        runez.log.trace("Full output of 'pip install %s':\n%s", pip_spec, r.full_output)
                        lines = lines[:4]

                self.problem = runez.joined(lines, delimiter="\n") or f"Resolution failed for {pip_spec}"
                return

            r = venv.pip_freeze()
            lines = r.output.strip().splitlines()
            if len(lines) != 1:  # pragma: no cover, hard to trigger (not sure how to make `pip freeze` fail)
                self.problem = f"'pip freeze' for '{runez.joined(pip_spec)}' failed: {r.full_output}"
                return

            location = None
            line = lines[0]
            if "==" in line:
                canonical_name, version = despecced(line)
                self.pip_spec = [f"{canonical_name}=={version}"]
                if not self.resolution_reason:
                    self.resolution_reason = "package spec"

            else:
                canonical_name = line.partition(" ")[0]
                self.resolution_reason = "project reference"

            self.resolution_reason = f"{self.resolution_reason} resolved by {settings.package_manager}"
            if not version:
                version, location = self._get_version_location(venv, canonical_name)

            self._set_canonical(canonical_name, version)
            ep = self._get_entry_points(venv, canonical_name, version, location)
            self.entrypoints = sorted(n for n in ep if "_completer" not in n)
            if not self.entrypoints:
                self.problem = runez.red("not a CLI")

    def _get_entry_points(self, venv, canonical_name, version, location):
        # Use `uv pip show` to get location on disk and version of package
        if canonical_name in (bstrap.PICKLEY, "tox"):
            # Don't bother peeking at metadata for some ultra common cases
            return (canonical_name,)

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

    @staticmethod
    def _get_version_location(venv, package_name):
        r = venv.pip_show(package_name)
        version = None
        location = None
        for line in r.output.splitlines():
            if line.startswith("Version:"):
                version = line.partition(":")[2].strip()

            if line.startswith("Location:"):
                location = line.partition(":")[2].strip()

            if location and version:
                break

        return version, location


class PackageSpec:
    """
    This class represents a package spec, and provides access to its resolved info and current installation state.
    """

    _manifest: "TrackedManifest" = runez.UNSET

    def __init__(self, given_package_spec: str):
        """
        Parameters
        ----------
        given_package_spec : str
            Provided package reference (either name, folder or git url)
        """
        self.settings = TrackedSettings.from_config(given_package_spec)

    def __repr__(self):
        return repr(self.settings)

    def __lt__(self, other):
        return str(self) < str(other)

    @runez.cached_property
    def resolved_info(self):
        canonical_name = PypiStd.std_package_name(self.settings.auto_upgrade_spec)
        cache_path = CFG.resolution_cache_path(canonical_name or hashlib.md5(self.settings.auto_upgrade_spec.encode()).hexdigest())
        info = ResolvedPackage.from_cache(cache_path)
        if info is None:
            info = ResolvedPackage()
            info.resolve(self.settings)
            if not info.problem:
                payload = info.to_dict()
                runez.save_json(payload, cache_path, fatal=None)

        return info

    @property
    def canonical_name(self) -> str:
        return self.resolved_info.canonical_name

    @property
    def target_version(self) -> Version:
        """The version of this package that we are targeting for installation"""
        return self.resolved_info.version

    @property
    def target_installation_folder(self):
        """Folder that will hold current installation of this package"""
        return CFG.meta / self.resolved_info.venv_basename

    @property
    def delivery_method(self):
        """Delivery method to use for this package"""
        from pickley.delivery import DeliveryMethod

        return DeliveryMethod.delivery_method_by_name(self.settings.delivery)

    @property
    def is_up_to_date(self) -> bool:
        manifest = self.manifest
        return manifest and manifest.version == self.target_version and self.is_healthily_installed

    @property
    def manifest(self) -> Optional["TrackedManifest"]:
        """Manifest of the current installation of this package, if any"""
        if self._manifest is runez.UNSET:
            self._manifest = TrackedManifest.from_file(CFG.manifest_path(self.canonical_name))

        return self._manifest

    @runez.cached_property
    def currently_installed_version(self):
        manifest = self.manifest
        return manifest and manifest.version

    @runez.cached_property
    def is_healthily_installed(self) -> bool:
        """Double-check that current venv is still usable"""
        manifest = self.manifest
        if manifest and manifest.version:
            if manifest.entrypoints:
                for name in manifest.entrypoints:
                    if not runez.is_executable(CFG.base / name):
                        return False

            # uv does not need a typical venv with bin/python
            exe_path = "uv" if self.canonical_name == "uv" else "python"
            exe_path = CFG.meta / manifest.venv_basename / "bin" / exe_path
            return bool(program_version(exe_path))

    def skip_reason(self) -> Optional[str]:
        """Reason for skipping installation, when applicable"""
        if CFG.version_check_delay:
            # When --force is used `version_check_delay` is zero (and there is no skip reason possible)
            is_facultative = CFG.get_value("facultative", package_name=self.canonical_name)
            if is_facultative and not self.is_clear_for_installation():
                return "not installed by pickley"

    def is_clear_for_installation(self) -> bool:
        """True if we can proceed with installation without needing to uninstall anything"""
        if self.currently_installed_version:
            return True

        target = CFG.base / self.canonical_name
        if not target or not os.path.exists(target):
            return True

        if CFG.symlinked_canonical(target):
            return True

        if os.path.isfile(target) and os.path.getsize(target) == 0 or not runez.is_executable(target):
            return True  # Empty file or not executable

        for line in runez.readlines(target, first=5):
            if bstrap.PICKLEY in line:
                return True  # Pickley wrapper

    def delete_all_files(self):
        """Delete all files in DOT_META/ folder related to this package spec"""
        runez.delete(CFG.manifest_path(self.canonical_name), fatal=False)
        for candidate, _ in self.installed_sibling_folders():
            runez.delete(candidate, fatal=False)

    def installed_sibling_folders(self):
        regex = re.compile(r"^(.+)-(\d+[.\d+]+)$")
        for item in runez.ls_dir(CFG.meta):
            if item.is_dir():
                m = regex.match(item.name)
                if m and m.group(1) == self.canonical_name:
                    yield item, m.group(2)

    def groom_installation(self, keep_for=7):
        """
        Args:
            keep_for (int): Minimum time in days for how long to keep the previous latest version
        """
        candidates = []
        manifest = self.manifest
        now = time.time()
        current_age = None
        for candidate, version in self.installed_sibling_folders():
            age = now - os.path.getmtime(candidate)
            if version == manifest.version:
                current_age = age

            else:
                candidates.append((age, candidate))

        if candidates:
            candidates = sorted(candidates)
            for candidate in candidates[1:]:
                runez.delete(candidate[1], fatal=False)

            if current_age and current_age > (keep_for * runez.date.SECONDS_IN_ONE_DAY):  # pragma: no cover
                # Delete version N-1 if it's older than `keep_for` days
                runez.delete(candidates[0][1], fatal=False)

    def save_manifest(self):
        manifest = TrackedManifest()
        self._manifest = manifest
        manifest.entrypoints = self.resolved_info.entrypoints
        manifest.install_info = TrackedInstallInfo.current()
        manifest.settings = self.settings
        manifest.venv_basename = self.resolved_info.venv_basename
        manifest.version = self.target_version
        payload = manifest.to_dict()
        runez.save_json(payload, CFG.manifest_path(self.canonical_name))
        runez.save_json(payload, self.target_installation_folder / ".manifest.json")
        return manifest


class PickleyConfig:
    """Pickley configuration"""

    base: Optional[Path] = None  # Installation folder
    meta: Optional[Path] = None  # DOT_META subfolder
    cache: Optional[Path] = None  # DOT_META/.cache subfolder
    manifests: Optional[Path] = None
    cli_config: Optional[dict] = None  # Tracks any custom CLI cfg flags given, such as --index, --python or --delivery
    configs: List["RawConfig"]
    _pip_conf = runez.UNSET
    _pip_conf_index = runez.UNSET

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
        self._pip_conf = runez.UNSET
        self._pip_conf_index = runez.UNSET
        self.version_check_delay = DEFAULT_VERSION_CHECK_DELAY

    def __repr__(self):
        return "<not-configured>" if self.base is None else runez.short(self.base)

    @staticmethod
    def resolved_path(path, base=None) -> Path:
        """
        Temporary: to be cleaned up when runez returns `Path` throughout as well.
        This function turns any string or path into a fully resolved (ie: `~` expanded) absolute path.
        """
        return runez.to_path(runez.resolved_path(path, base=base))

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

    def find_uv(self):
        """Path to uv installation"""
        return bstrap.find_uv(self.base)

    def set_base(self, base_path):
        """
        Parameters
        ----------
        base_path : Path | str
            Path to pickley base installation
        """
        self.configs = []
        self.base = self.resolved_path(base_path)
        self.meta = self.base / bstrap.DOT_META
        self.cache = self.meta / ".cache"
        self.manifests = self.meta / ".manifest"
        if self.cli_config is not None:
            self.configs.append(RawConfig(self, "cli", self.cli_config))

        self._add_config_file(self.config_path)
        self._add_config_file(self.meta / "config.json")
        package_manager = os.getenv("PICKLEY_PACKAGE_MANAGER") or bstrap.default_package_manager()
        defaults = {
            "delivery": "wrap",
            "install_timeout": 1800,
            "version_check_delay": DEFAULT_VERSION_CHECK_DELAY,
            "package_manager": package_manager,
        }
        self.configs.append(RawConfig(self, "defaults", defaults))
        self.version_check_delay = self.get_value("version_check_delay", validator=runez.to_int)

    def set_cli(self, config_path, delivery, index, python, package_manager):
        """
        Args:
            config_path (str | None): Optional configuration to use
            delivery (str | None): Optional delivery method to use
            index (str | None): Optional pypi index to use
            python (str | None): Optional python interpreter to use
            package_manager (str | None): Optional package manager to use
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
            path = path.resolve()
            try:
                relative = path.relative_to(self.meta)
                pv = relative.parts[0]
                return pv.rpartition("-")[0]

            except ValueError:
                return None

    def soft_lock_path(self, canonical_name):
        """str: Path to lock file used during installation for this package"""
        return self.meta / f"{canonical_name}.lock"

    def manifest_path(self, canonical_name):
        return self.manifests / f"{canonical_name}.manifest.json"

    def resolution_cache_path(self, filename):
        return self.cache / f"{filename}.resolved.json"

    def package_specs(self, names=None, canonical_only=True, include_pickley=False):
        """
        Args:
            names (list | None): Package names, if empty: all installed

        Returns:
            (list[PackageSpec]): Corresponding PackageSpec-s
        """
        if names:
            names = runez.flattened(names, split=" ")
            if canonical_only:
                for n in names:
                    runez.abort_if(not PypiStd.std_package_name(n), f"Invalid package name: {n}")

                names = [PypiStd.std_package_name(n) for n in names]

            if include_pickley and bstrap.PICKLEY not in names:
                names.append(bstrap.PICKLEY)

            result = [self.resolved_bundle(name) for name in names]
            result = runez.flattened(result, unique=True)
            return [PackageSpec(name) for name in result]

        return self.installed_specs()

    def wrapped_canonical_name(self, path):
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

    def installed_specs(self):
        """(list[PackageSpec]): Currently installed package specs"""
        spec_names = set(self.scan_installed())
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
            if value:
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
            if value:
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


CFG = PickleyConfig()


class TrackedManifest:
    """Info stored in .manifest.json for each installation"""

    entrypoints: Sequence[str] = None
    install_info: "TrackedInstallInfo" = None
    settings: "TrackedSettings" = None
    venv_basename: str = None
    version: Version = None

    def __repr__(self):
        return repr(self.settings)

    @classmethod
    def from_file(cls, path):
        data = runez.read_json(path)
        if data:
            manifest = cls()
            manifest.entrypoints = data.get("entrypoints")
            manifest.install_info = TrackedInstallInfo.from_manifest_data(data)
            manifest.settings = TrackedSettings.from_manifest_data(data)
            manifest.venv_basename = data.get("venv_basename")
            manifest.version = Version(data.get("version"))
            return manifest

    def to_dict(self):
        return {
            "entrypoints": self.entrypoints,
            "install_info": self.install_info.to_dict(),
            "settings": self.settings.to_dict(),
            "venv_basename": self.venv_basename,
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
        info.vpickley = __version__
        return info

    @classmethod
    def from_manifest_data(cls, data):
        if data:
            return cls.from_dict(data.get("install_info"))

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


class TrackedSettings:
    """
    Resolved config settings to use when installing a package.
    """

    auto_upgrade_spec: str = None  # Spec to use for `pickley auto-upgrade`
    bake_time: Optional[int] = None  # The amount of time to ignore new releases
    delivery: str = None  # Delivery method name
    package_manager: str  # Desired package manager
    pinned_version: Optional[str] = None  # Pinned version, if any
    python: Optional[str] = None  # Desired python

    def __repr__(self):
        return self.auto_upgrade_spec

    @classmethod
    def from_config(cls, auto_upgrade_spec: str):
        settings = cls()
        canonical_name = PypiStd.std_package_name(auto_upgrade_spec)
        settings.auto_upgrade_spec = canonical_name or _absolute_package_spec(auto_upgrade_spec)
        settings.bake_time = CFG.get_value("bake_time", package_name=canonical_name, validator=runez.to_int)
        settings.delivery = CFG.get_value("delivery", package_name=canonical_name)
        settings.package_manager = CFG.get_value("package_manager", package_name=canonical_name)
        settings.pinned_version = CFG.get_value("version", package_name=canonical_name)
        settings.python = CFG.get_value("python", package_name=canonical_name)
        return settings

    @classmethod
    def from_manifest_data(cls, data):
        if data:
            return cls.from_dict(data.get("settings"))

    @classmethod
    def from_dict(cls, data):
        if data:
            settings = cls()
            settings.auto_upgrade_spec = data.get("auto_upgrade_spec")
            settings.bake_time = data.get("bake_time")
            settings.delivery = data.get("delivery")
            settings.package_manager = data.get("package_manager")
            settings.pinned_version = data.get("pinned_version")
            settings.python = data.get("python")
            return settings

    def to_dict(self):
        return {
            "auto_upgrade_spec": self.auto_upgrade_spec,
            "bake_time": self.bake_time,
            "delivery": self.delivery,
            "package_manager": self.package_manager,
            "pinned_version": self.pinned_version,
            "python": self.python,
        }


def _log_to_file(message, error=False):
    if runez.log.file_handler is not None:
        # Avoid to log twice to console
        prev_level = None
        c = runez.log.console_handler
        if c is not None and c.level < logging.CRITICAL:
            prev_level = c.level
            c.level = logging.CRITICAL

        message = runez.uncolored(message)
        if error:
            logging.error(message)

        else:
            logging.info(message)

        if prev_level is not None:
            c.level = prev_level


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
