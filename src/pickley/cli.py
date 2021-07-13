"""
See https://github.com/codrsquad/pickley
"""

import logging
import os
import sys
import time

import click
import runez
from runez.render import PrettyTable

from pickley import __version__, abort, DOT_META, inform, PackageSpec, PickleyConfig, PLATFORM
from pickley.delivery import PICKLEY
from pickley.package import PexPackager, PythonVenv, VenvPackager


LOG = logging.getLogger(__name__)
PACKAGER = VenvPackager  # Packager to use for this run
CFG = PickleyConfig()


def setup_audit_log(cfg=CFG):
    """Setup audit.log log file handler"""
    if runez.DRYRUN:
        if not runez.log.console_handler or runez.log.spec.console_level > logging.INFO:
            runez.log.setup(console_level=logging.INFO)

        return

    if not runez.log.file_handler:
        runez.log.progress.start(message_color=runez.dim, spinner_color=runez.bold)
        log_path = cfg.meta.full_path("audit.log")
        runez.log.trace("Logging to %s", log_path)
        runez.ensure_folder(cfg.meta.path)
        runez.log.setup(
            file_format="%(asctime)s %(timezone)s [%(process)s] %(context)s%(levelname)s - %(message)s",
            file_level=logging.DEBUG,
            file_location=log_path,
            greetings=":: {argv}",
            rotate="size:500k",
            rotate_count=1,
        )


class SoftLockException(Exception):
    """Raised when soft lock can't be acquired"""


class SoftLock(object):
    """
    Simple soft file lock mechanism, allows to ensure only one pickley is working on a specific installation.
    A lock is a simple file containing 2 lines: process id holding it, and the CLI args it was invoked with.
    """

    def __init__(self, pspec, give_up=None, invalid=None):
        """
        Args:
            pspec (PackageSpec): Package to acquire lock for
            give_up (int | None): Timeout in seconds after which to give up (raise SoftLockException) if lock could not be acquired
            invalid (int | None): Age in seconds after which to consider existing lock as invalid
        """
        self.pspec = pspec
        self.lock_path = pspec.get_lock_path()
        self.give_up = give_up or pspec.cfg.install_timeout(pspec) or 120
        self.invalid = invalid or self.give_up * 2

    def __repr__(self):
        return "lock %s" % self.pspec

    def _locked_by(self):
        """
        Returns:
            (str): CLI args of process holding the lock, if any
        """
        if self.invalid and self.invalid > 0 and not runez.file.is_younger(self.lock_path, self.invalid):
            return None  # Lock file does not exist or invalidation age reached

        pid = None
        for line in runez.readlines(self.lock_path, default=[], errors="ignore"):
            if pid is not None:
                return line  # 2nd line hold CLI args process was invoked with

            pid = runez.to_int(line)
            if not runez.check_pid(pid):
                return None  # PID is no longer active

    def __enter__(self):
        """Acquire lock"""
        if CFG.base:
            runez.Anchored.add(CFG.base.path)

        cutoff = time.time() + self.give_up
        holder_args = self._locked_by()
        while holder_args:
            if time.time() >= cutoff:
                lock = runez.bold(runez.short(self.lock_path))
                holder_args = runez.bold(holder_args)
                raise SoftLockException("Can't grab lock %s, giving up\nIt is being held by: pickley %s" % (lock, holder_args))

            time.sleep(1)
            holder_args = self._locked_by()

        # We got the soft lock
        if runez.DRYRUN:
            print("Would acquire %s" % runez.short(self.lock_path))

        else:
            runez.log.trace("Acquired %s" % runez.short(self.lock_path))

        runez.write(self.lock_path, "%s\n%s\n" % (os.getpid(), runez.quoted(sys.argv[1:])), logger=False)
        self.pspec.resolve()
        return self

    def __exit__(self, *_):
        """Release lock"""
        if runez.DRYRUN:
            print("Would release %s" % runez.short(self.lock_path))

        else:
            runez.log.trace("Released %s" % runez.short(self.lock_path))

        if CFG.base:
            runez.Anchored.pop(CFG.base.path)

        runez.delete(self.lock_path, logger=False)


def perform_install(pspec, is_upgrade=False, force=False, quiet=False):
    """
    Args:
        pspec (PackageSpec): Package spec to install
        is_upgrade (bool): If True, intent is an upgrade (not a new install)
        force (bool): If True, check latest version even if recently checked
        quiet (bool): If True, don't chatter

    Returns:
        (pickley.TrackedManifest): Manifest is successfully installed (or was already up-to-date)
    """
    with SoftLock(pspec):
        started = time.time()
        pspec.resolve()
        skip_reason = pspec.skip_reason(force)
        if skip_reason:
            inform("Skipping installation of %s: %s" % (pspec.dashed, runez.bold(skip_reason)))
            return None

        manifest = pspec.get_manifest()
        if is_upgrade and not manifest and not quiet:
            abort("'%s' is not installed" % runez.red(pspec))

        if not pspec.version:
            desired = pspec.get_desired_version_info(force=force)
            if desired.problem:
                action = "upgrade" if is_upgrade else "install"
                abort("Can't %s %s: %s" % (action, pspec, runez.red(desired.problem)))

            pspec.version = desired.version

        if not force and manifest and manifest.version == pspec.version and pspec.is_healthily_installed():
            if not quiet:
                status = "up-to-date" if is_upgrade else "installed"
                inform("%s v%s is already %s" % (pspec.dashed, runez.bold(pspec.version), status))

            pspec.groom_installation()
            return manifest

        setup_audit_log()
        manifest = PACKAGER.install(pspec)
        if manifest and not quiet:
            note = " in %s" % runez.represented_duration(time.time() - started)
            action = "Upgraded" if is_upgrade else "Installed"
            if runez.DRYRUN:
                action = "Would state: %s" % action

            inform("%s %s v%s%s" % (action, pspec.dashed, runez.bold(pspec.version), runez.dim(note)))

        if not pspec._pickley_dev_mode:
            pspec.groom_installation()

        return manifest


def _find_base_from_program_path(path):
    if not path or len(path) <= 1:
        return None

    dirpath, basename = os.path.split(path)
    if basename:
        basename = basename.lower()
        if basename == DOT_META:
            return dirpath  # We're running from an installed pickley

        if basename == ".venv":
            return os.path.join(path, "root")  # Convenience for development

    return _find_base_from_program_path(dirpath)


def find_base():
    base_path = runez.resolved_path(os.environ.get("PICKLEY_ROOT"))
    if base_path:
        if not os.path.isdir(base_path):
            abort("PICKLEY_ROOT points to non-existing directory %s" % runez.red(base_path))

        return runez.resolved_path(base_path)

    program_path = PickleyConfig.program_path
    return _find_base_from_program_path(program_path) or os.path.dirname(program_path)


def clean_env_vars(*keys):
    """Ensure given env vars are removed if present"""
    for key in keys:
        if key in os.environ:
            del os.environ[key]


@runez.click.group()
@click.pass_context
@runez.click.version(message="%(version)s", version=__version__)
@runez.click.debug()
@runez.click.dryrun("-n")
@runez.click.color()
@click.option("--config", "-c", metavar="PATH", help="Optional additional configuration to use")
@click.option("--index", "-i", metavar="PATH", help="Pypi index to use")
@click.option("--python", "-P", metavar="PATH", help="Python interpreter to use")
@click.option("--delivery", "-d", help="Delivery method to use")
@click.option("--packager", "-p", type=click.Choice(["pex", "venv"]), help="Packager to use")
def main(ctx, debug, config, index, python, delivery, packager):
    """Package manager for python CLIs"""
    global PACKAGER
    PACKAGER = PexPackager if packager == "pex" else VenvPackager
    runez.system.AbortException = SystemExit
    clean_env_vars("__PYVENV_LAUNCHER__", "PYTHONPATH")  # See https://github.com/python/cpython/pull/9516
    if PLATFORM == "darwin" and "ARCHFLAGS" not in os.environ:
        # Avoid issue on some OSX installations where ARM support seems to have been enabled too early
        os.environ["ARCHFLAGS"] = "-arch x86_64"

    level = logging.WARNING
    if ctx.invoked_subcommand == "package":
        level = logging.INFO
        python = python or "invoker"  # Default to using invoker for 'package' subcommand

    CFG.set_cli(config, delivery, index, python)
    if ctx.invoked_subcommand != "package":
        CFG.set_base(find_base())

    runez.log.setup(
        debug=debug or os.environ.get("PICKLEY_TRACE"),
        console_format="%(levelname)s %(message)s" if debug else "%(message)s",
        console_level=level,
        console_stream=sys.stderr,
        locations=None,
        trace="PICKLEY_TRACE",
    )


@main.command(name="auto-upgrade")
@click.option("--force", is_flag=True, help="Force auto-upgrade check, even if recently checked")
@click.argument("package", required=True)
def auto_upgrade(force, package):
    """Background auto-upgrade command (called by wrapper)"""
    pspec = PackageSpec(CFG, package)
    ping = pspec.ping_path
    if not force and runez.file.is_younger(ping, 5):  # 5 seconds cool down on version check to avoid bursts
        LOG.debug("Skipping auto-upgrade, checked recently")
        sys.exit(0)

    runez.touch(ping)
    lock_path = pspec.get_lock_path()
    if runez.file.is_younger(lock_path, CFG.install_timeout(pspec)):
        LOG.debug("Lock file present, another installation is in progress")
        sys.exit(0)

    perform_install(pspec, is_upgrade=True, force=False, quiet=True)


@main.command()
@click.argument("what", required=False)
def base(what):
    """Show pickley base folder"""
    path = CFG.base.path
    if what == "bootstrap-own-wrapper":
        # Internal: called by bootstrap script
        from pickley.delivery import DeliveryMethodWrap

        pspec = PackageSpec(CFG, PICKLEY, version=__version__)
        venv = PythonVenv(pspec, create=False)
        wrap = DeliveryMethodWrap()
        wrap.install(pspec, venv, {PICKLEY: PICKLEY})
        return

    if what:
        paths = {
            "audit": CFG.meta.full_path("audit.log"),
            "cache": CFG.cache.path,
            "config": CFG.meta.full_path("config.json"),
            "meta": CFG.meta.path,
        }
        paths["audit.log"] = paths["audit"]
        paths["config.json"] = paths["config"]
        path = paths.get(what)
        if not path:
            options = [runez.green(s) for s in sorted(paths)]
            abort("Unknown base folder reference '%s', try one of: %s" % (runez.red(what), ", ".join(options)))

    print(path)


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force check, even if checked recently")
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
@click.argument("packages", nargs=-1, required=False)
def check(force, verbose, packages):
    """Check whether specified packages need an upgrade"""
    code = 0
    packages = CFG.package_specs(packages)
    if not packages:
        print("No packages installed")
        sys.exit(0)

    for pspec in packages:
        skip_reason = pspec.skip_reason(force)
        if skip_reason:
            print("%s: %s, %s" % (pspec.dashed, runez.bold("skipped"), runez.dim(skip_reason)))
            continue

        desired = pspec.get_desired_version_info(force=force)
        dv = runez.bold(desired and desired.version)
        manifest = pspec.get_manifest()
        if desired.problem:
            msg = desired.problem
            code = 1

        elif not manifest or not manifest.version:
            msg = "v%s is not installed" % dv
            code = 1

        elif manifest.version == desired.version:
            msg = "v%s is installed" % dv

        else:
            action = "upgraded to" if desired.source == "latest" else "caught up to %s" % desired.source
            msg = "v%s installed, can be %s v%s" % (runez.dim(manifest.version), action, dv)

        print("%s: %s" % (pspec.dashed, msg))

    sys.exit(code)


@main.command()
def config():
    """Show current configuration"""
    print(CFG.represented())


def _diagnostics():
    desired = CFG.get_value("python")
    yield "base", CFG.base
    yield "desired python", desired
    python = CFG.find_python(fatal=False)
    yield "selected python", python.representation()
    if python is not CFG.available_pythons.invoker:
        yield "invoker python", CFG.available_pythons.invoker.representation()

    yield "default index", CFG.default_index
    yield "pip.conf", CFG.pip_conf


@main.command()
def diagnostics():
    """Show diagnostics info"""
    CFG.available_pythons.scan_path_env_var()
    print(PrettyTable.two_column_diagnostics(_diagnostics(), runez.SYS_INFO.diagnostics(), CFG.available_pythons.representation()))


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force installation, even if already installed")
@click.argument("packages", nargs=-1, required=True)
def install(force, packages):
    """Install a package from pypi"""
    setup_audit_log()
    for pspec in CFG.package_specs(packages):
        perform_install(pspec, is_upgrade=False, force=force, quiet=False)


@main.command()
@runez.click.border("-b", default="github")
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
def list(border, verbose):
    """List installed packages"""
    packages = CFG.package_specs(include_pickley=verbose)
    if not packages:
        print("No packages installed")
        sys.exit(0)

    table = PrettyTable("Package,Version,Delivery,Python,From index", border=border)
    table.header.style = runez.bold
    if not verbose:
        table.header.hide(2, 3, 4)

    for pspec in packages:
        manifest = pspec.get_manifest()
        if manifest:
            python = CFG.available_pythons.find_python(manifest.python)
            python = manifest.python if python.problem else python.representation()
            table.add_row(pspec.dashed, manifest.version, manifest.delivery, python, manifest.index)

    print(table)


@main.command()
@click.argument("packages", nargs=-1, required=False)
def upgrade(packages):
    """Upgrade an installed package"""
    packages = CFG.package_specs(packages)
    if not packages:
        inform("No packages installed, nothing to upgrade")
        sys.exit(0)

    setup_audit_log()
    for pspec in packages:
        perform_install(pspec, is_upgrade=True, force=False, quiet=False)


@main.command()
@click.option("--all", is_flag=True, help="Uninstall everything pickley-installed, including pickley itself")
@click.argument("packages", nargs=-1, required=False)
def uninstall(all, packages):
    """Uninstall packages"""
    if packages and all:
        abort("Either specify packages to uninstall, or --all (but not both)")

    if not packages and not all:
        abort("Specify packages to uninstall, or --all")

    if packages and PICKLEY in packages:
        abort("Run 'uninstall --all' if you wish to uninstall pickley itself (and everything it installed)")

    setup_audit_log()
    for pspec in CFG.package_specs(packages):
        manifest = pspec.get_manifest()
        if not manifest or not manifest.version:
            abort("%s was not installed with pickley" % runez.bold(pspec.dashed))

        if manifest.entrypoints:
            for ep in manifest.entrypoints:
                runez.delete(pspec.exe_path(ep))

        runez.delete(pspec.meta_path)
        action = "Would uninstall" if runez.DRYRUN else "Uninstalled"
        inform("%s %s" % (action, pspec.dashed))

    if all:
        runez.delete(CFG.base.full_path(PICKLEY))
        runez.delete(CFG.meta.path)
        inform("pickley is now %s" % runez.red("uninstalled"))


@main.command()
@click.option("--build", "-b", default="./build", show_default=True, help="Folder to use as build cache")
@click.option("--dist", "-d", default="./dist", show_default=True, help="Folder where to produce package")
@click.option("--symlink", "-s", help="Create symlinks for debian-style packaging, example: root:root/usr/local/bin")
@click.option("--no-compile", is_flag=True, help="Don't byte-compile packaged venv")
@click.option("--sanity-check", default=None, show_default=True, help="Args to invoke produced package as a sanity check")
@click.option("--requirement", "-r", multiple=True, help="Install from the given requirements file (can be used multiple times)")
@click.argument("project", required=True)
def package(build, dist, symlink, no_compile, sanity_check, project, requirement):
    """Package a project from source checkout"""
    started = time.time()
    runez.log.spec.default_logger = LOG.info
    CFG.set_base(runez.resolved_path(build))
    finalizer = PackageFinalizer(project, dist, symlink)
    finalizer.sanity_check = sanity_check
    finalizer.requirements = requirement
    finalizer.compile = not no_compile
    finalizer.resolve()
    report = finalizer.finalize()
    if report:
        inform("")
        inform(report)
        inform("")

    elapsed = "in %s" % runez.represented_duration(time.time() - started)
    inform("Packaged %s successfully %s" % (runez.bold(runez.short(project)), runez.dim(elapsed)))


class PackageFinalizer(object):
    """
    This class allows to have an early check on provided settings, and wrap them up
    """

    pspec = None  # type: PackageSpec

    def __init__(self, project, dist, symlink):
        """
        Args:
            project (str): Folder where project to be packaged resides (must have a setup.py)
            dist (str): Relative path to folder to use as 'dist' (where to deliver package)
            symlink (str | None): Synlink specification, of the form 'root:root/...'
        """
        self.folder = runez.resolved_path(project)
        self.dist = dist
        self.symlink = Symlinker(symlink) if symlink else None
        self.sanity_check = None
        self.requirements = []
        self.compile = True
        self.border = "reddit"

    @staticmethod
    def validate_sanity_check(exe, sanity_check):
        if not exe or not sanity_check:
            return None

        r = runez.run(exe, sanity_check, fatal=False)
        if r.failed:
            if does_not_implement_cli_flag(r.output, r.error):
                return "does not respond to %s" % sanity_check

            abort("'%s' failed %s sanity check: %s" % (exe, sanity_check, r.full_output))

        return runez.first_line(r.output or r.error)

    def resolve(self):
        if not os.path.isdir(self.folder):
            abort("Folder %s does not exist" % runez.red(runez.short(self.folder)))

        req = self.requirements
        if not req:
            default_req = runez.resolved_path("requirements.txt", base=self.folder)
            if os.path.exists(default_req):
                req = [default_req]

        if req:
            req = [("-r", runez.resolved_path(r, base=self.folder)) for r in req]

        req = runez.flattened(req, shellify=True)
        req.append(self.folder)
        self.requirements = req
        self.pspec = PackageSpec(CFG, self.folder)
        LOG.info("Using python: %s" % self.pspec.python)
        if self.dist.startswith("root/"):
            # Special case: we're targeting 'root/...' probably for a debian, use target in that case to avoid venv relocation issues
            target = self.dist[4:]
            if os.path.isdir(target):
                LOG.debug("debian mode: %s -> %s", self.dist, target)
                self.dist = target

            parts = self.dist.split("/")
            if len(parts) <= 2:
                # Auto-add package name to targets of the form root/subfolder (most typical case)
                self.dist = os.path.join(self.dist, self.pspec.dashed)

    def finalize(self):
        """Run sanity check and/or symlinks, and return a report"""
        with runez.Anchored(self.folder, CFG.base.path):
            runez.ensure_folder(CFG.base.path, clean=True, logger=False)
            dist_folder = runez.resolved_path(self.dist)
            exes = PACKAGER.package(self.pspec, CFG.base.path, dist_folder, self.requirements, self.compile)
            if exes:
                report = PrettyTable(["Packaged executable", self.sanity_check], border=self.border)
                report.header.style = "bold"
                if not self.sanity_check:
                    report.header[1].shown = False

                for exe in exes:
                    exe_info = self.validate_sanity_check(exe, self.sanity_check)
                    report.add_row(runez.quoted(exe), exe_info)
                    if self.symlink and exe:
                        self.symlink.apply(exe)

                if not self.compile and not runez.DRYRUN:
                    clean_compiled_artifacts(dist_folder)

                return report


def does_not_implement_cli_flag(*messages):
    """Detect case where packaged CLI does not respond to --version"""
    for msg in messages:
        if msg:
            msg = msg.lower()
            if "usage:" in msg or "unrecognized" in msg:
                return True


class Symlinker(object):
    def __init__(self, spec):
        self.base, _, self.target = spec.partition(":")
        if not self.base or not self.target:
            abort("Invalid symlink specification '%s'" % spec)

        self.base = runez.resolved_path(self.base)
        self.target = runez.resolved_path(self.target)

    def apply(self, exe):
        dest = os.path.join(self.target, os.path.basename(exe))
        if os.path.exists(exe):
            runez.delete(dest, logger=False)
            r = runez.symlink(exe, dest, must_exist=False)
            if r > 0:
                inform("Symlinked %s -> %s" % (runez.short(dest), runez.short(exe)))

        else:
            LOG.debug("'%s' does not exist, skipping symlink" % exe)


def delete_file(path):
    if runez.delete(path, fatal=False, logger=False) > 0:
        return 1

    return 0


def should_clean(basename):
    return basename == "__pycache__" or (basename.endswith(".pyc") or basename.endswith(".pyo"))


def clean_compiled_artifacts(folder):
    """Remove usual byte-code compiled artifacts from `folder`"""
    # See https://www.debian.org/doc/packaging-manuals/python-policy/ch-module_packages.html
    deleted = delete_file(os.path.join(folder, "share", "python-wheels"))
    dirs_to_be_deleted = []
    for root, dirs, files in os.walk(folder):
        for basename in dirs[:]:
            if should_clean(basename):
                dirs.remove(basename)
                dirs_to_be_deleted.append(os.path.join(root, basename))

        for basename in files:
            if should_clean(basename.lower()):
                deleted += delete_file(os.path.join(root, basename))

    for path in dirs_to_be_deleted:
        deleted += delete_file(path)

    if deleted:
        LOG.info("Deleted %s compiled artifacts", deleted)
