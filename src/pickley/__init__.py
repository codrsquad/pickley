import logging
import os
import platform
import re
import sys
import time
from datetime import datetime

import runez
from runez.pyenv import PypiStd, PythonDepot, PythonInstallationScanner, Version


__version__ = "3.4.3"
LOG = logging.getLogger(__name__)
PICKLEY = "pickley"
DOT_META = ".pickley"
K_CLI = {"delivery", "index", "python"}
K_DIRECTIVES = {"include"}
K_GROUPS = {"bundle", "pinned"}
K_LEAVES = {
    "facultative", "install_timeout", "min_python", "preferred_min_python", "preferred_pythons", "pyenv", "version", "version_check_delay"
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
        version = text[i + 2:].strip() or None
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


def _dynamic_resolver(cfg, name_or_url):
    package_name, package_version = despecced(name_or_url)
    if package_version:
        return package_name, package_version, None

    cached_resolved = None
    folder = None
    is_git = "://" in name_or_url or name_or_url.endswith(".git")
    if is_git or "/" in name_or_url:
        safe_name = re.sub(r"\W+", "-", name_or_url).strip("-")
        folder = name_or_url if not is_git else cfg.cache.full_path("checkout", safe_name)
        cached_resolved = cfg.cache.full_path(f"{safe_name}.rlv")
        info = runez.read_json(cached_resolved)
        if info and "resolved" in info:
            package_name, package_version = info["resolved"]
            return package_name, package_version, folder

        if is_git and not os.path.isdir(folder):
            runez.ensure_folder(folder, clean=True)
            if runez.DRYRUN:
                package_name = os.path.basename(name_or_url)
                if package_name.endswith(".git"):
                    package_name = package_name[:-4]

                return package_name, "0.0.0", folder

            runez.run("git", "clone", name_or_url, folder)

    if folder:
        setup_py = os.path.join(folder, "setup.py")
        if not os.path.exists(setup_py):
            abort(f"No setup.py in '{runez.red(runez.short(folder))}'")

        with runez.CurrentFolder(folder):
            # Some setup.py's assume current folder is the one with their setup.py
            r = runez.run(sys.executable, "setup.py", "--name", dryrun=False, fatal=False, logger=False)
            package_name = r.output
            if r.failed or not package_name:
                abort(f"Could not determine package name from '{runez.red(runez.short(setup_py))}'")

            r = runez.run(sys.executable, "setup.py", "--version", dryrun=False, fatal=False, logger=False)
            package_version = r.output
            if r.failed or not package_version:
                abort("Could not determine package version from setup.py")

            runez.save_json(dict(resolved=[package_name, package_version]), cached_resolved)
            return package_name, package_version, folder

    return package_name, package_version, None


class PackageSpec:
    """
    Formalizes a pypi package specification

    - accepted chars are: alpha numeric, or "-" and "."
    - pypi assumes names are lower-cased and dash-separated
    - wheel transforms dashes to underscores
    """

    # The following fields become available after a call to resolve()
    name = None  # type: str
    folder = None  # type: str
    dashed = None  # type: str
    wheelified = None  # type: str
    python = None  # type: runez.pyenv.PythonInstallation
    pinned = None  # type: str
    settings = None  # type: TrackedSettings
    _desired_track = None  # type: TrackedVersion
    _latest = None  # type: TrackedVersion
    _manifest = None  # type: TrackedManifest

    def __init__(self, cfg, name_or_url):
        """
        Args:
            cfg (PickleyConfig): Associated configuration
            name_or_url (str): Provided package reference (either name, folder or git url)
        """
        self.cfg = cfg
        self.original = name_or_url
        self.name, self.given_version, self.folder = _dynamic_resolver(cfg, name_or_url)
        runez.abort_if(pypi_name_problem(self.name))
        self.dashed = PypiStd.std_package_name(self.name)
        if self.name != self.dashed:
            logging.warning(f"'{runez.red(self.name)}' is not pypi canonical, use dashes only and lowercase")

        self.wheelified = PypiStd.std_wheel_basename(self.name)

    def __repr__(self):
        return specced(self.dashed, self.given_version)

    def __lt__(self, other):
        return str(self) < str(other)

    @runez.cached_property
    def python(self):
        return self.cfg.find_python(self)

    @runez.cached_property
    def settings(self):
        return TrackedSettings(
            delivery=self.cfg.delivery_method(self),
            index=self.cfg.index(self) or self.cfg.default_index,
            python=self.python.executable,
            virtualenv=self.cfg.get_virtualenv(self),
        )

    @runez.cached_property
    def pinned(self):
        return self.cfg.pinned_version(self)

    @runez.cached_property
    def _pickley_dev_mode(self):
        """Path to pickley source folder, if we're running in dev mode"""
        if self.dashed == PICKLEY:
            dev_path = self.cfg.pickley_dev_path()
            if dev_path:
                return dev_path

    @runez.cached_property
    def index(self):
        """Index to use for this package spec"""
        return self.cfg.index(self)

    @property
    def desired_track(self):
        if self._desired_track is None:
            if self.given_version:
                self._desired_track = TrackedVersion(source="explicit", version=self.given_version)

            elif self.pinned:
                self._desired_track = TrackedVersion(source="pinned", version=self.pinned)

            else:
                self._desired_track = self.get_latest()  # By default, the latest is desired
                candidates = []
                if self.is_currently_installed:
                    candidates.append(TrackedVersion.from_manifest(self.manifest))

                if self.dashed == PICKLEY:
                    candidates.append(TrackedVersion(source="current", version=__version__))

                for candidate in candidates:
                    if candidate and candidate.version:
                        dt = self._desired_track
                        if not dt or not dt.version or Version(candidate.version) > Version(dt.version):
                            self._desired_track = candidate

        return self._desired_track

    @property
    def is_up_to_date(self):
        manifest = self.manifest
        return manifest and manifest.version == self.desired_track.version and self.is_healthily_installed()

    @property
    def manifest(self):
        """TrackedManifest: Manifest of the current installation of this package"""
        if self._manifest is None:
            self._manifest = TrackedManifest.from_file(self.manifest_path)

        return self._manifest

    @runez.cached_property
    def manifest_path(self):
        return self.cfg.meta.full_path(self.dashed, ".manifest.json")

    @runez.cached_property
    def meta_path(self):
        return self.cfg.meta.full_path(self.dashed)

    @runez.cached_property
    def ping_path(self):
        """Path to .ping file (for throttle auto-upgrade checks)"""
        return self.cfg.cache.full_path(f"{self.dashed}.ping")

    @property
    def is_currently_installed(self):
        manifest = self.manifest
        return manifest and manifest.version

    @runez.cached_property
    def is_already_installed_by_pickley(self):
        """bool: True if this package was already installed by pickley once"""
        return self.dashed == PICKLEY or os.path.exists(self.manifest_path)

    def get_install_path(self, version):
        if self._pickley_dev_mode:
            return self.cfg.meta.full_path(PICKLEY, f"{PICKLEY}-dev")

        return self.cfg.meta.full_path(self.dashed, f"{self.dashed}-{version}")

    def get_lock_path(self):
        """str: Path to lock file used during installation for this package"""
        return self.cfg.meta.full_path(f"{self.dashed}.lock")

    def skip_reason(self, force=False):
        """str: Reason for skipping installation, when applicable"""
        if not force and self.cfg.facultative(pspec=self):
            if not self.is_clear_for_installation():
                return "not installed by pickley"

    def is_clear_for_installation(self):
        """
        Returns:
            (bool): True if we can proceed with installation without needing to uninstall anything
        """
        if self.is_already_installed_by_pickley:
            return True

        target = self.exe_path(self.dashed)
        if not target or not os.path.exists(target):
            return True

        path = os.path.realpath(target)
        if path.startswith(self.cfg.meta.path):
            return True  # Pickley symlink

        if os.path.isfile(target):
            if os.path.getsize(target) == 0 or not runez.is_executable(target):
                return True  # Empty file or not executable

        for line in runez.readlines(target, first=5):
            if PICKLEY in line:
                return True  # Pickley wrapper

    def exe_path(self, exe):
        return self.cfg.base.full_path(exe)

    def is_healthily_installed(self):
        """Double-check that current venv is still usable"""
        manifest = self.manifest
        if manifest and manifest.version:
            if manifest.entrypoints:
                for name in manifest.entrypoints:
                    exe_path = self.exe_path(name)
                    if not runez.is_executable(exe_path):
                        return False

            install_folder = self.get_install_path(manifest.version)
            py_path = self.cfg.available_pythons.resolved_python_exe(install_folder)
            if py_path:
                return runez.run(py_path, "--version", dryrun=False, fatal=False, logger=False).succeeded

    def find_wheel(self, folder, fatal=True):
        """list[str]: Wheel for this package found in 'folder', if any"""
        if runez.DRYRUN:
            return [f"{self.wheelified}-{self.desired_track.version}-py3-none-any.whl"]

        result = []
        if folder and os.path.isdir(folder):
            prefix = f"{self.wheelified}-"
            for fname in os.listdir(folder):
                if fname.startswith(prefix):
                    result.append(os.path.join(folder, fname))

            if len(result) == 1:
                return result[0]

        return runez.abort(f"Expecting 1 wheel, found: {runez.short(result)}", fatal=fatal, return_value=None)

    def groom_installation(self, keep_for=60):
        """
        Args:
            keep_for (int): Minimum time in minutes for how long to keep the previous latest version
        """
        if self._pickley_dev_mode:
            return

        if self.cfg.cache.contains(self.folder):
            runez.delete(self.folder, logger=False)

        meta_path = self.meta_path
        current_age = None
        manifest = self.manifest
        if manifest and os.path.isdir(meta_path):
            now = time.time()
            candidates = []
            for fname in os.listdir(meta_path):
                if fname.startswith("."):  # Pickley meta files start with '.'
                    continue

                fpath = os.path.join(meta_path, fname)
                vpart = fname[len(self.dashed) + 1:]
                age = now - os.path.getmtime(fpath)
                if vpart == manifest.version:
                    current_age = age

                else:
                    version = Version(vpart)
                    if not version.is_valid:
                        # Not a proper installation
                        runez.delete(fpath, fatal=False)
                        continue

                    # Different version, previously installed
                    candidates.append((age, version, fpath))

            if not candidates:
                return

            candidates = sorted(candidates)
            youngest = candidates[0]
            for candidate in candidates[1:]:
                runez.delete(candidate[2], fatal=False)

            if current_age:
                current_age = min(current_age, youngest[0])
                if current_age > (keep_for * runez.date.SECONDS_IN_ONE_MINUTE):
                    runez.delete(youngest[2], fatal=False)

    def save_manifest(self, entry_points):
        manifest = TrackedManifest(
            self.manifest_path,
            self.settings,
            entry_points,
            pinned=self.pinned,
            version=self.desired_track.version,
        )
        payload = manifest.to_dict()
        runez.save_json(payload, self.manifest_path)
        path = self.get_install_path(self.desired_track.version)
        runez.save_json(payload, os.path.join(path, ".manifest.json"))
        self._manifest = manifest
        return manifest

    def get_latest(self, force=False):
        """Tracked in DOT_META/.cache/<package>.latest"""
        if force or self._latest is None:
            path = self.cfg.cache.full_path(f"{self.dashed}.latest")
            if not force:
                age = self.cfg.version_check_delay(self)
                if age and runez.file.is_younger(path, age):
                    self._latest = TrackedVersion.from_file(path)
                    if self._latest:
                        return self._latest

            self._latest = TrackedVersion.from_pypi(self)
            if not self._latest.problem:
                runez.save_json(self._latest.to_dict(), path, fatal=None)

            self._desired_track = None

        return self._latest


def get_default_index(*paths):
    """Configured pypi index from pip.conf"""
    for path in paths:
        conf = runez.file.ini_to_dict(path)
        index = conf.get("global", {}).get("index-url")
        if index:
            return path, index

    return None, None


def get_program_path(path=None):
    if path is None:
        path = runez.resolved_path(sys.argv[0])

    if path.endswith(".py") or path.endswith(".pyc"):
        packaged = runez.SYS_INFO.venv_bin_path(PICKLEY)
        if runez.is_executable(packaged):
            path = packaged  # Convenience when running from debugger

    return path


class PickleyConfig:
    """Pickley configuration"""

    base = None  # type: FolderBase # Installation folder
    meta = None  # type: FolderBase # DOT_META subfolder
    cache = None  # type: FolderBase # DOT_META/.cache subfolder
    cli = None  # type: TrackedSettings # Tracks any custom CLI cfg flags given, such as --index, --python or --delivery
    configs = None  # type: list
    program_path = get_program_path()

    _pickley_dev_path = None

    def __init__(self):
        self.configs = []
        self.config_path = None
        self.pip_conf, self.pip_conf_index = get_default_index("~/.config/pip/pip.conf", "/etc/pip.conf")
        self.default_index = self.pip_conf_index or DEFAULT_PYPI
        self._explored = set()

    def __repr__(self):
        return "<not-configured>" if self.base is None else runez.short(self.base)

    @runez.cached_property
    def available_pythons(self):
        pyenv = self.pyenv()
        scanner = PythonInstallationScanner(pyenv) if pyenv else None
        depot = PythonDepot(scanner=scanner)
        depot.find_preferred_python(
            self.get_value("preferred_pythons"),
            min_python=self.get_value("min_python"),
            preferred_min_python=self.get_value("preferred_min_python")
        )
        return depot

    @classmethod
    def pickley_dev_path(cls):
        if cls._pickley_dev_path is None:
            cls._pickley_dev_path = runez.DEV.project_folder

        return cls._pickley_dev_path

    def set_base(self, base_path):
        """
        Args:
            base_path (str): Path to pickley base installation
        """
        self.configs = []
        self.base = FolderBase("base", base_path)
        self.meta = FolderBase("meta", os.path.join(self.base.path, DOT_META))
        self.cache = FolderBase("cache", os.path.join(self.meta.path, ".cache"))
        if self.cli:
            cli = runez.serialize.json_sanitized(self.cli.to_dict())
            self.configs.append(RawConfig(self, "cli", cli))

        self._add_config_file(self.config_path)
        self._add_config_file(self.meta.full_path("config.json"))
        defaults = dict(
            delivery="wrap",
            install_timeout=1800,
            min_python="3.6",
            preferred_min_python="3.7",
            preferred_pythons="/usr/bin/python3,/usr/bin/python",
            version_check_delay=300
        )
        self.configs.append(RawConfig(self, "defaults", defaults))

    def set_cli(self, config_path, delivery, index, python, virtualenv):
        """
        Args:
            config_path (str | None): Optional configuration to use
            delivery (str | None): Optional delivery method to use
            index (str | None): Optional pypi index to use
            python (str | None): Optional python interpreter to use
        """
        self.config_path = config_path
        self.cli = TrackedSettings(delivery, index, python, virtualenv)

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

    def find_python(self, pspec=None, fatal=True):
        """
        Args:
            pspec (PackageSpec | None): Package spec, when applicable
            fatal (bool): If True, abort execution is no valid python could be found

        Returns:
            (runez.pyenv.PythonInstallation): Object representing python installation
        """
        desired = self.get_value("python", pspec=pspec)
        if not desired:
            # Most common case: use configured preferred python (will be 'invoker' by default)
            return self.available_pythons.find_python(None)

        issues = []
        python = None
        desired = runez.flattened(desired, split=",")
        for d in desired:
            python = self.available_pythons.find_python(d)
            if not python.problem:
                return python

            issues.append(f"Python '{runez.bold(runez.short(d))}' skipped: {runez.red(python.problem)}")

        for i in issues:  # Warn only if no python could be found at all
            LOG.warning(i)

        if fatal:
            abort("No suitable python installation found")

        return python

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
            return [PackageSpec(self, name) for name in result]

        result = []
        if os.path.isdir(self.meta.path):
            for fname in sorted(os.listdir(self.meta.path)):
                if include_pickley or fname != PICKLEY:
                    fpath = os.path.join(self.meta.path, fname)
                    if os.path.isdir(fpath):
                        if os.path.exists(os.path.join(fpath, ".manifest.json")):
                            result.append(PackageSpec(self, fname))

        return result

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

    def get_value(self, key, pspec=None, validator=None):
        """
        Args:
            key (str): Key to look up
            pspec (PackageSpec | None): Package spec, when applicable
            validator (callable | None): Validator to use

        Returns:
            Value from first RawConfig that defines it
        """
        for c in self.configs:
            value = c.get_value(key, pspec, validator)
            if value:
                return value

    def delivery_method(self, pspec=None):
        """
        Args:
            pspec (PackageSpec | None): Package spec, when applicable

        Returns:
            (str): Configured delivery method for 'pspec'
        """
        return self.get_value("delivery", pspec=pspec)

    def facultative(self, pspec):
        """
        Args:
            pspec (PackageSpec | None): Associated package spec

        Returns:
            (bool): Is installation facultative for 'pspec'? (if it is: pre-existing non-pickley installs remain as-is)
        """
        return self.get_value("facultative", pspec=pspec, validator=runez.to_boolean)

    def index(self, pspec=None):
        """
        Args:
            pspec (PackageSpec | None): Package spec, when applicable

        Returns:
            (str | None): Optional pypi index to use
        """
        return self.get_value("index", pspec=pspec)

    def install_timeout(self, pspec=None):
        """
        Args:
            pspec (PackageSpec | None): Package spec, when applicable

        Returns:
            (int): How many seconds to give an installation to complete before assuming it failed
        """
        return self.get_value("install_timeout", pspec=pspec, validator=runez.to_int)

    def pinned_version(self, pspec):
        """
        Args:
            pspec (PackageSpec | None): Package spec, when applicable

        Returns:
            (str | None): Configured version for 'pspec', if any
        """
        if pspec:
            pinned = self.get_nested("pinned", pspec.dashed)
            if isinstance(pinned, dict):
                return pinned.get("version")

            if isinstance(pinned, str):
                return pinned

    def pyenv(self):
        """
        Returns:
            (str): Configured path to pyenv installation
        """
        return self.get_value("pyenv")

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

    def version_check_delay(self, pspec=None):
        """
        Args:
            pspec (PackageSpec | None): Package spec, when applicable

        Returns:
            (int): How many seconds to wait before checking latest version again
        """
        return self.get_value("version_check_delay", pspec=pspec, validator=runez.to_int)

    def get_virtualenv(self, pspec):
        """
        Args:
            pspec (PackageSpec | None): Package spec, when applicable

        Returns:
            (str): Virtualenv version to use for this package spec (default: stdlib venv module)
        """
        return self.get_value("virtualenv", pspec=pspec)

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


class TrackedVersion:
    """Object tracking a version, and the source it was obtained from"""

    index = None  # type: str # Associated pypi url, if any
    install_info = None  # type: TrackedInstallInfo
    problem = None  # type: str # Problem that occurred during pypi lookup, if any
    source = None  # type: str # How 'version' was determined (can be: latest, pinned, ...)
    version = None  # type: str

    def __init__(self, index=None, install_info=None, problem=None, source=None, version=None):
        self.index = index
        self.install_info = install_info or TrackedInstallInfo.current()
        self.problem = problem
        self.source = source
        self.version = version

    def __repr__(self):
        return self.version

    @classmethod
    def from_pypi(cls, pspec, index=None, include_prerelease=False):
        """
        Args:
            pspec (PackageSpec): Pypi package name to lookup
            index (str | None): URL to pypi index to use (default: pypi.org)
            include_prerelease (bool): If True, include latest pre-release

        Returns:
            (TrackedVersion):
        """
        index = index or pspec.index or pspec.cfg.default_index
        version = PypiStd.latest_pypi_version(pspec.dashed, index=index, include_prerelease=include_prerelease)
        if not version:
            return cls(index=index, problem=f"does not exist on {index}")

        return cls(index=index, source="latest", version=version.text)

    @classmethod
    def from_manifest(cls, manifest, source="installed"):
        return cls(index=manifest.index, install_info=manifest.install_info, source=source, version=manifest.version)

    @classmethod
    def from_file(cls, path):
        data = runez.read_json(path)
        if data:
            return cls(
                index=data.get("index"),
                install_info=TrackedInstallInfo.from_manifest_data(data),
                problem=data.get("problem"),
                source=data.get("source"),
                version=data.get("version"),
            )

    def to_dict(self):
        return dict(
            index=self.index,
            install_info=self.install_info.to_dict(),
            problem=self.problem,
            source=self.source,
            version=self.version,
        )


class TrackedManifest:
    """Info stored in .manifest.json for each installation"""

    path = None  # type: str # Path to this manifest
    settings = None  # type: TrackedSettings
    entrypoints = None  # type: dict
    install_info = None  # type: TrackedInstallInfo
    pinned = None  # type: str
    version = None  # type: str

    def __init__(self, path, settings, entrypoints, install_info=None, pinned=None, version=None):
        self.path = path
        self.settings = settings
        self.entrypoints = entrypoints or {}
        self.install_info = install_info or TrackedInstallInfo.current()
        self.pinned = pinned
        self.version = version

    def __repr__(self):
        return f"{self.version} [p: {self.python}]"

    @classmethod
    def from_file(cls, path):
        data = runez.read_json(path)
        if data:
            return cls(
                path,
                TrackedSettings.from_manifest_data(data),
                data.get("entrypoints"),
                install_info=TrackedInstallInfo.from_manifest_data(data),
                pinned=data.get("pinned"),
                version=data.get("version"),
            )

    @property
    def delivery(self):
        if self.settings:
            return self.settings.delivery

    @property
    def index(self):
        if self.settings:
            return self.settings.index

    @property
    def python(self):
        if self.settings:
            return self.settings.python

    def to_dict(self):
        return dict(
            settings=self.settings.to_dict(),
            entrypoints=self.entrypoints,
            install_info=self.install_info.to_dict(),
            pinned=self.pinned,
            version=self.version,
        )


class TrackedInstallInfo:
    """Info on which pickley run performed the installation"""

    args = None  # type: str # CLI args with which pickley was invoked
    timestamp = None  # type: datetime
    vpickley = None  # type: str # Version of pickley that performed the installation

    def __init__(self, args, timestamp, vpickley):
        self.args = args
        self.timestamp = timestamp
        self.vpickley = vpickley

    @classmethod
    def current(cls):
        return cls(runez.quoted(sys.argv[1:]), datetime.now(), __version__)

    @classmethod
    def from_manifest_data(cls, data):
        if data:
            return cls.from_dict(data.get("install_info"))

    @classmethod
    def from_dict(cls, data):
        if data:
            return cls(data.get("args"), runez.to_datetime(data.get("timestamp")), data.get("vpickley"))

    def to_dict(self):
        return dict(args=self.args, timestamp=self.timestamp.strftime("%Y-%m-%d %H:%M:%S"), vpickley=self.vpickley)


class TrackedSettings:
    delivery = None  # type: str # Delivery method name
    index = None  # type: str # Pypi url used
    python = None  # type: str # Desired python
    virtualenv = None  # type: str # Desired virtualenv version

    def __init__(self, delivery, index, python, virtualenv):
        self.delivery = delivery
        self.index = index
        self.python = runez.short(python) if python else None
        self.virtualenv = virtualenv

    @classmethod
    def from_manifest_data(cls, data):
        if data:
            return cls.from_dict(data.get("settings"))

    @classmethod
    def from_dict(cls, data):
        if data:
            return cls(delivery=data.get("delivery"), index=data.get("index"), python=data.get("python"), virtualenv=data.get("virtualenv"))

    def to_dict(self):
        return dict(delivery=self.delivery, index=self.index, python=self.python, virtualenv=self.virtualenv)


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
        self.path = runez.resolved_path(path)

    def __repr__(self):
        return self.path

    def contains(self, path):
        if path:
            path = runez.resolved_path(path)
            return path.startswith(self.path)

    def full_path(self, *relative):
        """
        Args:
            *relative: Relative path components

        Returns:
            (str): Full path based on self.path
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

    def get_value(self, key, pspec, validator):
        """
        Args:
            key (str): Key to look up
            pspec (PackageSpec | None): Package spec, when applicable
            validator (callable | None): Validator to use

        Returns:
            Value, if any
        """
        if pspec and pspec.name:
            pinned = self.get_nested("pinned", pspec.dashed)
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
