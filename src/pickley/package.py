import logging
import os
import time
import zipfile

import runez

import pickley
from pickley import system
from pickley.context import ImplementationMap
from pickley.delivery import DELIVERERS, move_venv
from pickley.lock import SoftLock, SoftLockException, vrun
from pickley.pypi import latest_pypi_version
from pickley.settings import short
from pickley.uninstall import uninstall_existing


LOG = logging.getLogger(__name__)
PACKAGERS = ImplementationMap("packager")

# These standard locations usually help avoid silly C compilation errors
C_COMPILATION_HELP = {
    "CPPFLAGS": " -I/usr/local/opt/openssl/include",
    "LDFLAGS": " -L/usr/local/opt/openssl/lib",
    "PKG_CONFIG_PATH": ":/usr/local/opt/openssl/lib/pkgconfig",
}


def find_prefix(prefixes, text):
    """
    :param dict prefixes: Prefixes available
    :param str text: Text to examine
    :return str|None: Longest prefix found
    """
    if not text or not prefixes:
        return None
    candidate = None
    for name in prefixes:
        if name and text.startswith(name):
            if not candidate or len(name) > len(candidate):
                candidate = name
    return candidate


class VersionMeta(runez.Serializable):
    """
    Version meta on a given package
    """

    # Dields starting with '_' are not stored to json file
    _base = None                    # type: VersionMeta # Base meta
    _problem = None                 # type: str | None # Detected problem, if any
    _suffix = None                  # type: str # Suffix of json file where this object is persisted
    _package_spec = None            # type: system.PackageSpec # Associated pypi package

    # Main info, should be passed from latest -> current etc
    version = ""                    # type: str # Effective version
    channel = ""                    # type: str # Channel (stable, latest, ...) via which this version was determined
    source = ""                     # type: str # Description of where definition came from

    # Runtime info, should be set/stored for 'current'
    packager = ""                   # type: str # Packager used
    delivery = ""                   # type: str # Delivery method used
    python = ""                     # type: str # Python interpreter used

    # Additional info
    pickley = ""                    # type: str # Pickley version used to perform install
    timestamp = None                # type: int # Epoch when version was determined (useful to cache "expensive" calls to pypi)

    def __init__(self, package_spec, suffix=None, base=None):
        """
        :param system.PackageSpec package_spec: Associated pypi package spec
        :param str|None suffix: Optional suffix where to store this object
        :param VersionMeta|None base: Base meta on which 'self' should be based
        """
        self._package_spec = package_spec
        self._suffix = suffix
        self._base = base
        if suffix:
            self._path = system.SETTINGS.meta.full_path(package_spec.dashed, ".%s.json" % suffix)

    def __repr__(self):
        return self.representation()

    def load(self, path=None, fatal=True, logger=None):  # pragma: no cover
        old_path = None
        if path is None and self._suffix == "current" and self._package_spec.multi_named and not os.path.exists(self._path):
            # Temporary fix: pickley <v1.8 didn't standardize on dashed package name
            p = system.SETTINGS.meta.full_path(self._package_spec.pythonified, ".%s.json" % self._suffix)
            if os.path.exists(p):
                path = p
                old_path = self._path
        super(VersionMeta, self).load(path=path, fatal=fatal, logger=logger)
        if old_path:
            self._path = old_path
            self._source = runez.short(old_path)

    def _update_dynamic_fields(self):
        """Update dynamically determined fields"""
        self.delivery = DELIVERERS.resolved_name(self._package_spec, default=self._base and self._base.delivery)
        self.packager = PACKAGERS.resolved_name(self._package_spec, default=self._base and self._base.packager)
        # Record which python was used, as specified
        self.python = system.target_python(package_spec=self._package_spec).text
        self.pickley = runez.get_version(pickley)
        self.timestamp = int(time.time())

    def representation(self, verbose=False, note=None):
        """
        :param bool verbose: If True, show more extensive info
        :param str|None note: Optional not to mention in returned text
        :return str: Human readable representation
        """
        if self._problem:
            lead = "%s: %s" % (self._package_spec, self._problem)
        elif self.version:
            lead = "%s %s" % (self._package_spec, self.version)
        else:
            lead = "%s: no version" % self._package_spec
        notice = ""
        if verbose:
            notice = []
            if not self._problem and self.version and (self.packager or self.delivery):
                info = "as"
                if self.packager:
                    info = "%s %s" % (info, self.packager)
                if self.delivery:
                    info = "%s %s" % (info, self.delivery)
                notice.append(info)
            if self.channel:
                notice.append("channel: %s" % self.channel)
            if notice and self.source and self.source != system.SETTINGS.index:
                notice.append("source: %s" % self.source)
            if notice:
                notice = " (%s)" % ", ".join(notice)
            else:
                notice = ""
        if note:
            notice = " %s%s" % (note, notice)
        return "%s%s" % (lead, notice)

    @property
    def problem(self):
        """
        :return str|None: Problem description, if any
        """
        return self._problem

    @property
    def valid(self):
        """
        :return bool: Was version determined successfully?
        """
        return bool(self.version) and not self._problem

    @property
    def file_exists(self):
        """
        :return bool: True if corresponding json file exists
        """
        return self._path and os.path.exists(self._path)

    def equivalent(self, other):
        """
        :param VersionMeta|None other: VersionMeta to compare to
        :return bool: True if 'self' is equivalent to 'other'
        """
        if other is None:
            return False
        if self.version != other.version:
            return False
        if self.packager != other.packager:
            return False
        if self.delivery != other.delivery:
            return False
        return True

    def set_version(self, version, channel, source):
        """
        :param str version: Effective version
        :param str channel: Channel (stable, latest, ...) via which this version was determined
        :param str source: Description of where version determination came from
        """
        self.version = version
        self.channel = channel
        self.source = source
        if version:
            self._problem = None
        self._update_dynamic_fields()

    def set_from(self, other):
        """
        :param VersionMeta other: Set this meta from 'other'
        """
        if isinstance(other, VersionMeta):
            self._problem = other._problem
            self.version = other.version
            self.channel = other.channel
            self.source = other.source
            self._update_dynamic_fields()

    def invalidate(self, problem):
        """
        :param str problem: Description of problem
        """
        self._problem = problem
        self.version = ""

    @property
    def still_valid(self):
        """
        :return bool: Is this version determination still valid? (based on timestamp)
        """
        if not self.valid or not self.timestamp:
            return self.valid
        try:
            return (int(time.time()) - self.timestamp) < system.SETTINGS.version_check_seconds
        except (TypeError, ValueError):
            return False


class Packager(object):
    """
    Interface of a packager
    """

    implementation_name = None  # type: str # Injected by ImplementationMap
    implementation_version = None  # type: str # Optional, pypi version of underlying implementation to use (example: pex==1.4.5)

    def __init__(self, package_spec):
        """
        :param system.PackageSpec package_spec: Pypi package spec
        """
        self.package_spec = package_spec
        self._entry_points = None
        self.current = VersionMeta(self.package_spec, "current")
        self.latest = VersionMeta(self.package_spec, system.LATEST_CHANNEL, base=self.current)
        self.desired = VersionMeta(self.package_spec, base=self.current)

        self.current.load(fatal=False)
        if not self.current.valid:
            self.current.invalidate("is not installed")

        self.dist_folder = system.SETTINGS.meta.full_path(self.package_spec.dashed, ".tmp")
        self.build_folder = os.path.join(self.dist_folder, "build")
        self.relocatable = False
        self.source_folder = None
        self.packaged = []  # Paths to what was packaged (populated by self.effective_package())
        self.executables = []  # Paths to delivered exes (populated by perform_delivery())

    def __repr__(self):
        return "%s %s" % (self.implementation_name, self.package_spec.specced)

    def specced_command(self):
        """
        :return str: Name of underlying pypi package to use, optionally with pinned version
        """
        if self.implementation_version:
            return "%s==%s" % (self.implementation_name, self.implementation_version)
        return self.implementation_name

    @property
    def entry_points_path(self):
        return system.SETTINGS.meta.full_path(self.package_spec.dashed, ".entry-points.json")

    @property
    def removed_entry_points_path(self):
        return system.SETTINGS.meta.full_path(self.package_spec.dashed, ".removed-entry-points.json")

    @property
    def entry_points(self):
        """
        :return dict: Determined entry points from produced wheel, if available
        """
        if self._entry_points is None:
            self._entry_points = runez.read_json(self.entry_points_path, fatal=None, default=None)
            if isinstance(self._entry_points, list):
                # For backwards compatibility with pickley <= v1.4.2
                self._entry_points = dict((k, "") for k in self._entry_points)
            if self._entry_points is None:
                return {self.package_spec.dashed: ""} if runez.DRYRUN else {}
        return self._entry_points

    def refresh_entry_points(self):
        """Refresh entry point from saved json and/or build folder"""
        if runez.DRYRUN:
            return
        self._entry_points = self.get_entry_points()
        if self._entry_points:
            runez.save_json(self._entry_points, self.entry_points_path, fatal=False)

    def get_entry_points(self):
        """
        :return dict|None: Determined entry points for associated pypi package
        """
        if not os.path.isdir(self.build_folder):
            return None

        scripts = {}
        for fname in os.listdir(self.build_folder):
            if fname.endswith(".whl") and self.package_spec.version_part(fname):
                wheel_path = os.path.join(self.build_folder, fname)
                try:
                    with zipfile.ZipFile(wheel_path, "r") as wheel:
                        for wname in wheel.namelist():
                            wdir, wbase = os.path.split(wname)
                            if wbase == "entry_points.txt":
                                with wheel.open(wname) as fh:
                                    scripts.update(runez.get_conf(fh.readlines(), default={}).get("console_scripts"))
                            elif wdir.endswith("/scripts") and "_completer" not in wbase:
                                with wheel.open(wname) as fh:
                                    first_line = runez.decode(fh.readline())
                                    if "python" in first_line:
                                        scripts[wbase] = wname

                except Exception as e:
                    LOG.error("Can't read wheel %s: %s", wheel_path, e, exc_info=e)

        return scripts

    def refresh_latest(self, force=False):
        """Refresh self.latest"""
        self.latest.load(fatal=False)
        if not force and self.latest.still_valid:
            return

        version = latest_pypi_version(system.SETTINGS.index, self.package_spec)
        source = system.SETTINGS.index or "pypi"
        self.latest.set_version(version, system.LATEST_CHANNEL, source)
        if not version:
            self.latest.invalidate("can't determine latest version from %s" % source)

        elif not version.startswith("error: "):
            self.latest.save(fatal=False)

        else:
            self.latest.invalidate(version[7:])

    def refresh_desired(self, force=False):
        """Refresh self.desired"""
        channel = system.SETTINGS.resolved_value("channel", package_spec=self.package_spec)
        vdef = system.SETTINGS.get_definition("channel.%s.%s" % (channel, self.package_spec.dashed))
        source = str(vdef)
        version = vdef and vdef.value

        if self.package_spec.version and self.package_spec.version != version:
            channel = "adhoc"
            source = "cli"
            version = self.package_spec.version

        if version:
            self.desired.set_version(version, channel, source)
            return

        if channel == system.LATEST_CHANNEL:
            self.refresh_latest(force=force)
            self.desired.set_from(self.latest)
            return

        self.desired.invalidate("can't determine %s version" % channel)

    def pip_wheel(self):
        """
        Run pip wheel

        :return str: None if successful, error message otherwise
        """
        runez.ensure_folder(self.build_folder, folder=True)
        return vrun(
            self.package_spec,
            "pip", "wheel",
            "-i", system.SETTINGS.index,
            "--cache-dir", self.build_folder,
            "--wheel-dir", self.build_folder,
            self.source_folder if self.source_folder else "%s==%s" % (self.package_spec.dashed, self.desired.version)
        )

    def package(self):
        """Package given python project"""
        if not self.desired.version and not self.source_folder:
            return runez.abort("Need either source_folder or version in order to package", fatal=(True, []))

        if not self.desired.version:
            setup_py = os.path.join(self.source_folder, "setup.py")
            if not os.path.isfile(setup_py):
                return runez.abort("No setup.py in %s", short(self.source_folder), fatal=(True, []))
            self.desired.version = system.run_python(setup_py, "--version", dryrun=False, fatal=False, package_spec=self.package_spec)
            if not self.desired.version:
                return runez.abort("Could not determine version from %s", short(setup_py), fatal=(True, []))

        self.pip_wheel()

        self.refresh_entry_points()
        runez.ensure_folder(self.dist_folder, folder=True)
        template = "{name}" if self.source_folder else "{name}-{version}"
        self.packaged = []
        self.effective_package(template)

    def create_symlinks(self, symlink, fatal=True):
        """
        Use case: preparing a .tox/package/root folder to be packaged as a debian
        With a spec of "root:root/usr/local/bin", all executables produced under ./root will be symlinked to /usr/local/bin

        :param str symlink: A specification of the form "root:root/usr/local/bin"
        :param bool fatal: Abort execution on failure if True
        :return int: 1 if effectively done, 0 if no-op, -1 on failure
        """
        if not symlink:
            return 0
        base, _, target = symlink.partition(":")
        if not target:
            return runez.abort("Invalid symlink specification '%s'", symlink, fatal=(fatal, -1))
        base = runez.resolved_path(base)
        target = runez.resolved_path(target)
        for path in self.executables:
            if not path.startswith(base) or len(path) <= len(base):
                return runez.abort("Symlink base '%s' does not cover '%s'", base, path, fatal=(fatal, -1))
            source = path[len(base):]
            basename = os.path.basename(path)
            destination = os.path.join(target, basename)
            runez.symlink(source, destination, must_exist=False, fatal=fatal, logger=LOG.info)
        return 1 if self.executables else 0

    def sanity_check(self, args):
        """
        :param str args: Args to run as sanity-check against all packaged exes, example: "--version"
        """
        if args:
            for path in self.executables:
                output = runez.run(path, args)
                print("Sanity check: %s %s -> %s" % (short(path), args, output))

    def effective_package(self, template):
        """
        :param str template: Template describing how to name delivered files, example: {meta}/{name}-{version}
        """

    def install(self, force=False):
        """
        :param bool force: If True, re-install even if package is already installed
        """
        try:
            self.internal_install(force=force)

        except SoftLockException as e:
            LOG.error("%s is currently being installed by another process" % self.package_spec)
            runez.abort("If that is incorrect, please delete %s.lock", short(e.folder))

    def internal_install(self, force=False, verbose=True):
        """
        :param bool force: If True, re-install even if package is already installed
        :param bool verbose: If True, show more extensive info
        """
        with SoftLock(self.dist_folder, timeout=system.SETTINGS.install_timeout):
            self.refresh_desired(force=force)
            if not self.desired.valid:
                system.setup_audit_log()
                return runez.abort("Can't install %s: %s", self.package_spec, self.desired.problem)

            if not force and self.current.equivalent(self.desired):
                system.inform(self.desired.representation(verbose=verbose, note="is already installed"))
                self.cleanup()
                return

            system.setup_audit_log()
            prev_entry_points = self.entry_points
            self.effective_install()

            new_entry_points = self.entry_points
            removed = set(prev_entry_points).difference(new_entry_points)
            if removed:
                old_removed = runez.read_json(self.removed_entry_points_path, default=[], fatal=False)
                removed = sorted(removed.union(old_removed))
                runez.save_json(removed, self.removed_entry_points_path, fatal=False)

            # Delete wrapper/symlinks of removed entry points immediately
            for name in removed:
                runez.delete(system.SETTINGS.base.full_path(name))

            self.cleanup()
            if self.package_spec.multi_named:
                # Clean up old installations with underscore in name
                runez.delete(system.SETTINGS.meta.full_path(self.package_spec.pythonified))

            self.current.set_from(self.desired)
            self.current.save(fatal=False)

            msg = "Would install" if runez.DRYRUN else "Installed"
            system.inform("%s %s" % (msg, self.desired.representation(verbose=verbose)))

    def cleanup(self):
        """Cleanup older installs"""
        cutoff = time.time() - system.SETTINGS.install_timeout * 60
        folder = system.SETTINGS.meta.full_path(self.package_spec.dashed)

        removed_entry_points = runez.read_json(self.removed_entry_points_path, default=[], fatal=False)

        prefixes = {None: [], self.package_spec.dashed: []}
        for name in self.entry_points:
            prefixes[name] = []
        for name in removed_entry_points:
            prefixes[name] = []

        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if name.startswith("."):
                    continue
                target = find_prefix(prefixes, name)
                if target in prefixes:
                    fpath = os.path.join(folder, name)
                    prefixes[target].append((os.path.getmtime(fpath), fpath))

        # Sort each by last modified timestamp
        for target, cleanable in prefixes.items():
            prefixes[target] = sorted(cleanable, reverse=True)

        rem_cleaned = 0
        for target, cleanable in prefixes.items():
            if not cleanable:
                if target in removed_entry_points:
                    # No cleanable found for a removed entry-point -> count as cleaned
                    rem_cleaned += 1
                continue

            if target not in removed_entry_points:
                if cleanable[0][0] <= cutoff:
                    # Latest is old enough now, cleanup all except latest
                    cleanable = cleanable[1:]
                else:
                    # Latest is too young, keep the last 2
                    cleanable = cleanable[2:]
            elif cleanable[0][0] <= cutoff:
                # Delete all removed entry points when old enough
                rem_cleaned += 1
            else:
                # Removed entry point still too young, keep latest
                cleanable = cleanable[1:]

            for _, path in cleanable:
                runez.delete(path)

        if rem_cleaned >= len(removed_entry_points):
            runez.delete(self.removed_entry_points_path)

    def effective_install(self):
        """Install this pypi cli to self.dist_folder"""

    def required_entry_points(self):
        """
        :return list: Entry points, abort execution if there aren't any
        """
        ep = self.entry_points
        if not ep:
            runez.delete(system.SETTINGS.meta.full_path(self.package_spec.dashed))
            runez.abort("'%s' is not a CLI, it has no console_scripts entry points", self.package_spec.dashed)
        return ep

    def perform_delivery(self, template):
        """
        :param str template: Template describing how to name delivered files, example: {meta}/{name}-{version}
        """
        # Touch the .ping file since this is a fresh install (no need to check for upgrades right away)
        runez.touch(system.SETTINGS.meta.full_path(self.package_spec.dashed, ".ping"))

        self.executables = []
        deliverer = DELIVERERS.resolved(self.package_spec, default=self.desired.delivery)
        for name in self.required_entry_points():
            target = system.SETTINGS.base.full_path(name)
            if self.package_spec.dashed != system.PICKLEY and not self.current.file_exists:
                uninstall_existing(target)
            path = template.format(meta=system.SETTINGS.meta.full_path(self.package_spec.dashed), name=name, version=self.desired.version)
            deliverer.install(target, path)
            self.executables.append(target)


@PACKAGERS.register
class PexPackager(Packager):
    """
    Package/install via pex (https://pypi.org/project/pex/)
    """

    def pex_build(self, name, destination):
        """
        Run pex build

        :param str name: Name of entry point
        :param str destination: Path to file where to produce pex
        :return str: None if successful, error message otherwise
        """
        runez.ensure_folder(self.build_folder, folder=True)
        runez.delete(destination)

        args = ["--cache-dir", self.build_folder, "--repo", self.build_folder]
        args.extend(["-c%s" % name, "-o%s" % destination, self.package_spec.specced])

        python = system.target_python(package_spec=self.package_spec)
        shebang = python.shebang(universal=system.is_universal(self.build_folder))
        if shebang:
            args.append("--python-shebang")
            args.append(shebang)

        vrun(self.package_spec, self.specced_command(), *args, path_env=C_COMPILATION_HELP)

    def effective_package(self, template):
        """
        :param str template: Template describing how to name delivered files, example: {meta}/{name}-{version}
        """
        self.executables = []
        for name in self.required_entry_points():
            dest = template.format(name=name, version=self.desired.version)
            dest = os.path.join(self.dist_folder, dest)
            self.pex_build(name, dest)
            self.packaged.append(dest)
            self.executables.append(dest)

    def effective_install(self):
        """Install this pypi cli to self.dist_folder"""
        self.package()
        for path in self.packaged:
            name = os.path.basename(path)
            target = system.SETTINGS.meta.full_path(self.package_spec.dashed, name)
            move_venv(path, target)
        self.perform_delivery("{meta}/{name}-{version}")


@PACKAGERS.register
class VenvPackager(Packager):
    """
    Install via virtualenv (https://pypi.org/project/virtualenv/)
    """

    def effective_package(self, template):
        """
        :param str template: Template describing how to name delivered files, example: {meta}/{name}-{version}
        """
        folder = os.path.join(self.dist_folder, template.format(name=self.package_spec.dashed, version=self.desired.version))
        runez.delete(folder, logger=None)
        runez.ensure_folder(folder, folder=True, logger=None)
        vrun(self.package_spec, "virtualenv", folder)

        bin_folder = os.path.join(folder, "bin")
        pip = os.path.join(bin_folder, "pip")
        spec = self.source_folder if self.source_folder else "%s==%s" % (self.package_spec.dashed, self.desired.version)
        runez.run(pip, "install", "-i", system.SETTINGS.index, "-f", self.build_folder, spec)

        if self.relocatable:
            python = system.target_python(package_spec=self.package_spec).executable
            vrun(self.package_spec, "virtualenv", "--relocatable", "--python=%s" % python, folder)

        self.packaged.append(folder)
        self.executables = [os.path.join(bin_folder, name) for name in self.entry_points]

    def effective_install(self):
        """Install this pypi cli to self.dist_folder"""
        self.package()
        path = self.packaged[0]
        target = system.SETTINGS.meta.full_path(self.package_spec.dashed, os.path.basename(path))
        move_venv(path, target)
        self.perform_delivery(os.path.join(target, "bin", "{name}"))
