import hashlib
import logging
import os
import platform
import re
import sys
import time
from datetime import datetime
from  pathlib import Path
from typing import List, Optional, Sequence

import runez
from runez.pyenv import PypiStd, PythonDepot, Version

from pickley.bstrap import default_package_manager, DOT_META, http_get, PICKLEY

__version__ = "4.3.1"
LOG = logging.getLogger(__name__)
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

DEFAULT_PYPI = "https://pypi.org/simple"


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


def _absolute_package_spec(name_or_url):
    if isinstance(name_or_url, Path):
        name_or_url = str(name_or_url.absolute())

    if name_or_url.startswith("http"):
        name_or_url = f"git+{name_or_url}"

    if name_or_url.startswith("git+"):
        return name_or_url

    if name_or_url.startswith(".") or "/" in name_or_url:
        name_or_url = str(runez.to_path(name_or_url).absolute())

    return name_or_url


def parsed_version(text):
    """Parse --version from text, in reverse order to avoid being fooled by warnings..."""
    if text:
        for line in reversed(text.splitlines()):
            version = Version.extracted_from_text(line)
            if version:
                return version


def program_version(path, dryrun: bool = runez.UNSET, fatal=True, logger: callable = runez.UNSET):
    if runez.is_executable(path):
        r = runez.run(path, "--version", dryrun=dryrun, fatal=fatal, logger=logger)
        if r.succeeded:
            return parsed_version(r.output or r.error)


class ResolvedPackageInfo:
    """Resolved package information, look up latest version if needed"""

    problem: Optional[str] = None  # Problem with package spec, if any
    resolution_reason: str = None  # How version to use was resolved
    version_check_delay: int  # How often to check for new versions
    _canonical_name: str = None  # Canonical pypi package name
    _version: Version = None  # Exact version being installed
    _pip_spec: List[str] = None  # CLI args to pass to `pip install`
    _auto_upgrade_spec: Optional[str] = None  # Spec to use for `pickley auto-upgrade`
    _venv_basename: str = None  # The basename of the .pk/ venv pickley uses to install this
    _is_resolved = False

    def __init__(self, given_package_spec, logger=None, package_manager=None):
        self.given_package_spec = _absolute_package_spec(given_package_spec)
        self.version_check_delay = CFG.get_value("version_check_delay", validator=runez.to_int)
        self.logger = logger
        self.python_spec = CFG.get_value("python", package_name=given_package_spec)
        self.package_manager = package_manager or CFG.get_value("package_manager", package_name=given_package_spec)
        if given_package_spec == "uv":
            # `uv` is a special case, it's used for bootstrap and does not need a venv
            tmp_uv = runez.to_path(CFG.meta.full_path(".uv"))
            if runez.is_executable(tmp_uv / "bin/uv"):
                # Use the .pk/.uv/bin/uv obtained during bootstrap
                version = program_version(tmp_uv / "bin/uv", dryrun=False, fatal=False, logger=False)
                if version:
                    self._set_canonical(given_package_spec, version)
                    self._auto_upgrade_spec = self._canonical_name
                    self._pip_spec = [self._canonical_name]
                    self.resolution_reason = "uv bootstrap"
                    return

        name, version = despecced(given_package_spec)
        if version:
            self.resolution_reason = "pinned"

        else:
            # Does configuration mandate a specifically pinned version?
            pinned = CFG.get_nested("pinned", given_package_spec)
            if isinstance(pinned, dict):
                version = pinned.get("version")

            elif isinstance(pinned, str):
                version = pinned

            if version:
                self.resolution_reason = "pinned by configuration"

        if name and version:
            # User or configuration wants to install a specifically pinned version (example: poetry==1.8.3)
            self._is_resolved = True
            self._set_canonical(name, version, is_exact_pin=True)
            self._auto_upgrade_spec = f"{self._canonical_name}=={version}"
            self._pip_spec = [self._auto_upgrade_spec]
            return

    def __repr__(self):
        return runez.short(self.given_package_spec)

    def _set_canonical(self, name, version, is_exact_pin=False):
        self._canonical_name = PypiStd.std_package_name(name)
        if version and not isinstance(version, Version):
            version = Version(version)

        self._version = version
        self._venv_basename = f"{self._canonical_name}-{version}" if self._canonical_name and version else None

    def _save_to_cache(self, cache_path):
        payload = {
            "given_package_spec": self.given_package_spec,
            "auto_upgrade_spec": self._auto_upgrade_spec,
            "canonical_name": self._canonical_name,
            "pip_spec": self._pip_spec,
            "problem": self.problem,
            "resolution_reason": self.resolution_reason,
            "venv_basename": self._venv_basename,
            "version": self._version,
        }
        runez.save_json(payload, cache_path, fatal=None)

    def _cache_path(self, name):
        filename = PypiStd.std_package_name(name)
        if not filename:
            filename = hashlib.md5(name.encode()).hexdigest()

        return runez.to_path(CFG.cache.full_path(f"{filename}.resolved.json"))

    def _auto_resolve(self):
        from pickley.package import PythonVenv

        self._is_resolved = True
        cache_path = self._cache_path(self.given_package_spec)
        if self.version_check_delay and runez.file.is_younger(cache_path, self.version_check_delay):
                data = runez.read_json(cache_path)
                if isinstance(data, dict) and ("canonical_name" in data or "problem" in data):
                    self._set_canonical(data.get("canonical_name"), data.get("version"))
                    self._auto_upgrade_spec = data.get("auto_upgrade_spec")
                    self._pip_spec = data.get("pip_spec")
                    self.problem = data.get("problem")
                    self.resolution_reason = data.get("resolution_reason")
                    self._venv_basename = data.get("venv_basename")
                    return

        pip_spec = self.given_package_spec
        with runez.TempFolder(dryrun=False):
            # User wants to install from a folder or git url
            self._auto_upgrade_spec = pip_spec
            self._pip_spec = [pip_spec]
            self.resolution_reason = "project reference"
            venv = PythonVenv(runez.to_path("resolution-venv"), package_manager=self.package_manager, python_spec=self.python_spec)
            venv.groom_uv_venv = False
            venv.logger = self.logger
            venv.create_venv()
            r = venv.pip_install(pip_spec, no_deps=True, quiet=True, fatal=False)
            if not r:
                pip_spec = runez.joined(pip_spec)
                lines = r.full_output.strip().splitlines()
                if lines and len(lines) > 3:
                    runez.log.trace("Full output of 'pip install %s':\n%s", pip_spec, r.full_output)
                    lines = lines[:3]

                self.problem = runez.joined(lines, delimiter="\n") or f"Resolution failed for {pip_spec}"
                self._save_to_cache(cache_path)
                return

            r = venv.pip_freeze()
            lines = r.output.strip().splitlines()
            if len(lines) != 1:
                self.problem = f"'pip freeze' for '{runez.joined(pip_spec)}' failed: {r.full_output}"
                self._save_to_cache(cache_path)
                return

            line = lines[0]
            if "==" in line:
                self.resolution_reason = f"resolved by {self.package_manager}"
                name, version = despecced(line)
                self._set_canonical(name, version)
                self._save_to_cache(cache_path)
                return

            name = line.partition(" ")[0]
            version, _ = venv.get_version_location(name)
            self._set_canonical(name, version)
            if self._canonical_name == PICKLEY and pip_spec == runez.DEV.project_folder:
                # Dev mode: install pickley from source in editable mode
                self.resolution_reason = "pickley dev mode"
                self._pip_spec = ["-e", runez.DEV.project_folder]
                self._venv_basename = f"{PICKLEY}-dev"
                return

            self._save_to_cache(cache_path)

    @property
    def canonical_name(self) -> str:
        if not self._is_resolved:
            self._auto_resolve()

        return self._canonical_name

    @property
    def version(self) -> Version:
        if not self._is_resolved:
            self._auto_resolve()

        return self._version

    @property
    def auto_upgrade_spec(self) -> Optional[str]:
        if not self._is_resolved:
            self._auto_resolve()

        return self._auto_upgrade_spec

    @property
    def pip_spec(self) -> List[str]:
        if not self._is_resolved:
            self._auto_resolve()

        return self._pip_spec

    @property
    def venv_basename(self) -> str:
        if not self._is_resolved:
            self._auto_resolve()

        return self._venv_basename


class PackageSpec:
    """
    Formalizes a pypi package specification

    Examples:
        - poetry
        - poetry==1.0.0
        - git+https://..

    - accepted chars are: alphanumeric, or "-" and "."
    - pypi assumes names are lower-cased and dash-separated
    - wheel transforms dashes to underscores
    """

    _manifest: "TrackedManifest" = None

    def __init__(self, name_or_url, package_manager=None):
        """
        Args:
            name_or_url (str): Provided package reference (either name, folder or git url)
            package_manager (str | None): Override package manager to use for resolution
        """
        self.resolved_info = ResolvedPackageInfo(name_or_url)

    def __repr__(self):
        return self.resolved_info.given_package_spec

    def __lt__(self, other):
        return str(self) < str(other)

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
        # TODO: Turn all paths to Path objects
        return runez.to_path(CFG.meta.full_path(self.resolved_info.venv_basename))

    @property
    def delivery_method(self):
        """Delivery method to use for this package"""
        from pickley.delivery import DeliveryMethod

        delivery = CFG.get_value("delivery", package_name=self.canonical_name)
        return DeliveryMethod.delivery_method_by_name(delivery)

    @property
    def is_up_to_date(self) -> bool:
        manifest = self.manifest
        return manifest and manifest.version == self.target_version and self.is_healthily_installed

    @property
    def manifest(self) -> Optional["TrackedManifest"]:
        """Manifest of the current installation of this package, if any"""
        if self._manifest is None:
            self._manifest = TrackedManifest.from_file(self.manifest_path)

        return self._manifest

    @runez.cached_property
    def manifest_path(self):
        return CFG.manifests.full_path(f"{self.canonical_name}.manifest.json")

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
                    exe_path = self.exe_path(name)
                    if not runez.is_executable(exe_path):
                        return False

            # uv does not need a typical venv with bin/python
            exe_path = "uv" if self.canonical_name == "uv" else "python"
            exe_path = self.target_installation_folder / f"bin/{exe_path}"
            if runez.is_executable(exe_path):
                return runez.run(exe_path, "--version", dryrun=False, fatal=False, logger=False).succeeded

    def get_lock_path(self):
        """str: Path to lock file used during installation for this package"""
        name = self.canonical_name
        if name:
            return CFG.meta.full_path(f"{name}.lock")

    def skip_reason(self, force=False) -> Optional[str]:
        """Reason for skipping installation, when applicable"""
        is_facultative = CFG.get_value("facultative", package_name=self.canonical_name)
        if not force and is_facultative and not self.is_clear_for_installation():
            return "not installed by pickley"

    def is_clear_for_installation(self) -> bool:
        """True if we can proceed with installation without needing to uninstall anything"""
        if self.currently_installed_version:
            return True

        target = self.exe_path(self.canonical_name)
        if not target or not os.path.exists(target):
            return True

        path = os.path.realpath(target)
        if path.startswith(CFG.meta.path):
            return True  # Pickley symlink

        if os.path.isfile(target) and os.path.getsize(target) == 0 or not runez.is_executable(target):
            return True  # Empty file or not executable

        for line in runez.readlines(target, first=5):
            if PICKLEY in line:
                return True  # Pickley wrapper

    def exe_path(self, exe):
        return CFG.base.full_path(exe)

    def delete_all_files(self):
        """Delete all files in DOT_META/ folder related to this package spec"""
        runez.delete(self.manifest_path, fatal=False)
        for candidate, _ in self.installed_sibling_folders():
            runez.delete(candidate, fatal=False)

    def installed_sibling_folders(self):
        regex = re.compile(r"^(.+)-(\d+[.\d+]+)$")
        for item in runez.ls_dir(CFG.meta.path):
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

            if current_age and current_age > (keep_for * runez.date.SECONDS_IN_ONE_DAY):
                runez.delete(candidates[0][1], fatal=False)

    def save_manifest(self, entry_points):
        manifest = TrackedManifest(
            self.manifest_path,
            self.resolved_info.auto_upgrade_spec,
            entry_points,
            TrackedInstallInfo.current(),
            self.resolved_info.python_spec,
            self.target_version,
        )
        payload = manifest.to_dict()
        runez.save_json(payload, self.manifest_path)
        runez.save_json(payload, self.target_installation_folder / ".manifest.json")
        self._manifest = manifest
        return manifest


def get_default_index(*paths):
    """Configured pypi index from pip.conf"""
    for path in paths:
        conf = runez.file.ini_to_dict(path)
        index = conf.get("global", {}).get("index-url")
        if index:
            return path, index

    return None, None


class PickleyConfig:
    """Pickley configuration"""

    base: Optional["FolderBase"] = None  # Installation folder
    meta: Optional["FolderBase"] = None  # DOT_META subfolder
    cache: Optional["FolderBase"] = None  # DOT_META/.cache subfolder
    cli_config: Optional[dict]  # Tracks any custom CLI cfg flags given, such as --index, --python or --delivery
    configs: List["RawConfig"]
    _uv_path = None

    def __init__(self):
        self.configs = []
        self.config_path = None
        self.pip_conf, self.pip_conf_index = get_default_index("~/.config/pip/pip.conf", "/etc/pip.conf")
        self.default_index = self.pip_conf_index or DEFAULT_PYPI

    def reset(self):
        """Used for testing"""
        self.base = None
        self.meta = None
        self.cache = None
        self.cli_config = None
        self.configs = []
        self.config_path = None
        self.pip_conf = None
        self.pip_conf_index = None
        self.default_index = DEFAULT_PYPI

    def __repr__(self):
        return "<not-configured>" if self.base is None else runez.short(self.base)

    @runez.cached_property
    def available_pythons(self):
        locations = runez.flattened(self.get_value("python_installations") or "PATH")
        depot = PythonDepot(*locations)
        preferred = runez.flattened(self.get_value("preferred_pythons"), split=",")
        depot.set_preferred_python(preferred)
        return depot

    def find_uv(self):
        """Path to uv installation"""
        if self._uv_path is None:
            for candidate in ("uv", f"{DOT_META}/.uv/bin/uv"):
                uv_path = os.path.join(self.base.path, candidate)
                if runez.is_executable(uv_path):
                    self._uv_path = uv_path
                    break

            if runez.DEV.project_folder:  # pragma: no cover, for dev mode
                self._uv_path = runez.which("uv")

        runez.abort_if(not self._uv_path, "`uv` is not installed, please reinstall with `pickley install uv`")
        return self._uv_path

    def set_base(self, base_path):
        """
        Args:
            base_path (str): Path to pickley base installation
        """
        self.configs = []
        self.base = FolderBase("base", runez.resolved_path(base_path))
        self.meta = FolderBase("meta", os.path.join(self.base.path, DOT_META))
        self.cache = FolderBase("cache", os.path.join(self.meta.path, ".cache"))
        self.manifests = FolderBase("manifests", os.path.join(self.meta.path, ".manifest"))
        if self.cli_config is not None:
            self.configs.append(RawConfig(self, "cli", self.cli_config))

        # TODO: Remove once pickley 3.4 is phased out
        old_meta = self.base.full_path(".pickley")
        old_cfg = os.path.join(old_meta, "config.json")
        new_cfg = self.meta.full_path("config.json")
        if not os.path.exists(new_cfg) and os.path.exists(old_cfg):
            runez.move(old_cfg, new_cfg)

        self._add_config_file(self.config_path)
        self._add_config_file(self.meta.full_path("config.json"))
        package_manager = os.getenv("PICKLEY_PACKAGE_MANAGER") or default_package_manager()
        defaults = {"delivery": "wrap", "install_timeout": 1800, "version_check_delay": 300, "package_manager": package_manager}
        self.configs.append(RawConfig(self, "defaults", defaults))

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
        path = runez.resolved_path(path, base=base)
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

    def package_specs(self, names=None, include_pickley=False):
        """
        Args:
            names (list | None): Package names, if empty: all installed

        Returns:
            (list[PackageSpec]): Corresponding PackageSpec-s
        """
        if names:
            names = runez.flattened(names, split=" ")
            if include_pickley and PICKLEY not in names:
                names.append(PICKLEY)

            result = [self.resolved_bundle(name) for name in names]
            result = runez.flattened(result, unique=True)
            return [PackageSpec(name) for name in result]

        return self.installed_specs()

    @runez.cached_property
    def _wrapped_canonical_regex(self):
        # TODO: Remove once pickley 3.4 is phased out
        return re.compile(r"\.pickley/([^/]+)/.+/bin/")

    def _wrapped_canonical(self, path):
        """(str | None): Canonical name of installed python package, if installed via pickley wrapper"""
        if runez.is_executable(path):
            for line in runez.readlines(path, first=12):
                if line.startswith("# pypi-package:"):
                    return line[15:].strip()

                m = self._wrapped_canonical_regex.search(line)
                if m:  # pragma: no cover, TODO: Remove once pickley 3.4 is phased out
                    return m.group(1)

    def scan_installed(self):
        """Scan installed"""
        for item in self.base.iterdir():
            spec_name = self._wrapped_canonical(item)
            if spec_name:
                yield spec_name

        for item in self.manifests.iterdir():
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

    path: str = None  # Path to this manifest
    auto_upgrade_spec: Optional[str] = None  # Spec to use for `pickley auto-upgrade`
    delivery: str = None
    entrypoints: Sequence[str] = None
    install_info: "TrackedInstallInfo" = None
    python: str = None
    version: Version = None

    def __init__(self, path, auto_upgrade_spec: Optional[str], entrypoints: dict, install_info: "TrackedInstallInfo", python: str, version: Version):
        self.path = path
        self.auto_upgrade_spec = auto_upgrade_spec
        self.entrypoints = entrypoints
        self.install_info = install_info
        self.python = python
        self.version = version

    @classmethod
    def from_file(cls, path):
        data = runez.read_json(path)
        if data:
            manifest = cls(path)
            manifest.auto_upgrade_spec = data.get("auto_upgrade_spec")
            manifest.delivery = data.get("delivery")
            manifest.entrypoints = data.get("entrypoints")
            manifest.install_info = TrackedInstallInfo.from_dict(data.get("install_info"))
            manifest.python = data.get("python")
            manifest.version = Version(data.get("version"))
            return manifest

    def to_dict(self):
        return {
            "auto_upgrade_spec": self.auto_upgrade_spec,
            "delivery": self.delivery,
            "entrypoints": self.entrypoints,
            "install_info": self.install_info.to_dict(),
            "python": self.python,
            "version": self.version.text,
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


class FolderBase:
    """
    This class allows to more easily deal with folders
    """

    def __init__(self, name, path):
        """
        Args:
            name (str): Internal name of this folder
            path (str): Path to folder
        """
        self.name = name
        self.path = path

    def __repr__(self):
        return self.path

    def iterdir(self):
        path = runez.to_path(self.path)
        if path.is_dir():
            yield from path.iterdir()

    def full_path(self, *relative):
        """
        Args:
            *relative: Relative path components

        Returns:
            (str): Full path based on `self.path`
        """
        return os.path.join(self.path, *relative)


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
