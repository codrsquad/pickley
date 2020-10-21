"""
See https://github.com/zsimic/pickley
"""

import logging
import os
import sys
import time

import click
import runez
from runez.render import PrettyTable

from pickley import __version__, abort, DOT_META, inform, PackageSpec, PickleyConfig, specced, validate_pypi_name
from pickley.delivery import DeliveryMethod, PICKLEY
from pickley.package import PexPackager, PLATFORM, PythonVenv, VenvPackager
from pickley.v1upgrade import V1Status


LOG = logging.getLogger(__name__)
PACKAGER = VenvPackager  # Packager to use for this run
CFG = PickleyConfig()


def protected_main():
    try:
        main()

    except KeyboardInterrupt:
        abort(runez.red("\n\nAborted\n"))

    except SoftLockException as e:
        abort(e)

    except NotImplementedError as e:
        msg = runez.stringified(e) or "Not implemented"
        abort(msg.format(packager=runez.red(PACKAGER.__name__.replace("Packager", "").lower())))


def setup_audit_log(cfg=CFG):
    """Setup audit.log log file handler"""
    if not runez.DRYRUN and not runez.log.file_handler:
        runez.ensure_folder(cfg.meta.path)
        runez.log.setup(
            file_format="%(asctime)s %(timezone)s [%(process)s] %(context)s%(levelname)s - %(message)s",
            file_level=logging.DEBUG,
            file_location=cfg.meta.full_path("audit.log"),
            greetings=":: {argv}",
            rotate="size:500k",
            rotate_count=1,
        )


class SoftLockException(Exception):
    """Raised when soft lock can't be acquired"""


class SoftLock(object):
    """
    Simple soft file lock mechanism, allows to ensure only one pickley is working on a specific installation.
    A 'lock' is a simple file containing 2 lines: process id of the logfetch holding it, and the CLI args it was invoked with.
    """

    def __init__(self, lock, give_up, invalid):
        """
        Args:
            lock (str): Path to lock file
            give_up (int): Timeout in seconds after which to give up (raise SoftLockException) if lock could not be acquired
            invalid (int): Age in seconds after which to consider existing lock as invalid
        """
        self.lock = lock
        self.give_up = give_up
        self.invalid = invalid

    def __repr__(self):
        return self.lock

    def _locked_by(self):
        """
        Returns:
            (str): CLI args of process holding the lock, if any
        """
        if not runez.file.is_younger(self.lock, self.invalid):
            return None  # Lock file does not exist or invalidation age reached

        pid = None
        for line in runez.readlines(self.lock, default=[], errors="ignore"):
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
                lock = runez.bold(runez.short(self.lock))
                holder_args = runez.bold(holder_args)
                raise SoftLockException("Can't grab lock %s, giving up\nIt is being held by: pickley %s" % (lock, holder_args))

            time.sleep(1)
            holder_args = self._locked_by()

        # We got the soft lock
        if runez.DRYRUN:
            print("Would acquire %s" % runez.short(self.lock))

        else:
            logging.debug("Acquired %s" % runez.short(self.lock))

        runez.write(self.lock, "%s\n%s\n" % (os.getpid(), runez.quoted(sys.argv[1:])), logger=False)
        return self

    def __exit__(self, *_):
        """Release lock"""
        if runez.DRYRUN:
            print("Would release %s" % runez.short(self.lock))

        else:
            logging.debug("Released %s" % runez.short(self.lock))

        if CFG.base:
            runez.Anchored.pop(CFG.base.path)

        runez.delete(self.lock, logger=False)


def perform_install(pspec, give_up=5, is_upgrade=False, force=False, quiet=False):
    """
    Args:
        pspec (PackageSpec): Package spec to install
        give_up (int): Timeout in minutes after which to give up (raise SoftLockException) if lock could not be acquired
        is_upgrade (bool): If True, intent is an upgrade (not a new install)
        force (bool): If True, check latest version even if recently checked
        quiet (bool): If True, don't chatter

    Returns:
        (pickley.TrackedManifest): Manifest is successfully installed (or was already up-to-date)
    """
    with SoftLock(pspec.lock_path, give_up=give_up * 60, invalid=pspec.cfg.install_timeout(pspec) * 60):
        started = time.time()
        manifest = pspec.get_manifest()
        if is_upgrade and not manifest and not quiet:
            abort("'%s' is not installed" % runez.red(pspec))

        if not pspec.version:
            desired = pspec.get_desired_version_info(force=force)
            if desired.problem:
                action = "upgrade" if is_upgrade else "install"
                abort("Can't %s %s: %s" % (action, pspec, runez.red(desired.problem)))

            pspec.version = desired.version

        if not force and manifest and manifest.version == pspec.version:
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
    base = os.environ.get("PICKLEY_ROOT")
    if base:
        if not os.path.isdir(base):
            abort("PICKLEY_ROOT points to non-existing directory %s" % runez.red(base))

        return runez.resolved_path(base)

    program_path = PickleyConfig.pickley_program_path
    return _find_base_from_program_path(program_path) or os.path.dirname(program_path)


@runez.click.group()
@click.pass_context
@runez.click.version(message="%(version)s", version=__version__)
@runez.click.debug()
@runez.click.dryrun("-n")
@runez.click.color()
@click.option("--config", "-c", default="~/.config/pickley.json", metavar="PATH", help="Configuration to use")
@click.option("--index", "-i", metavar="PATH", help="Pypi index to use")
@click.option("--python", "-P", metavar="PATH", help="Python interpreter to use")
@click.option("--delivery", "-d", help="Delivery method to use")
@click.option("--packager", "-p", type=click.Choice(["pex", "venv"]), help="Packager to use")
def main(ctx, debug, config, index, python, delivery, packager):
    """Package manager for python CLIs"""
    global PACKAGER
    PACKAGER = PexPackager if packager == "pex" else VenvPackager
    runez.system.AbortException = SystemExit
    if "__PYVENV_LAUNCHER__" in os.environ:
        # See https://github.com/python/cpython/pull/9516
        del os.environ["__PYVENV_LAUNCHER__"]

    if PLATFORM == "darwin" and "ARCHFLAGS" not in os.environ:
        # Avoid issue on some OSX installations where ARM support seems to have been enabled too early
        os.environ["ARCHFLAGS"] = "-arch x86_64"

    if runez.PY2:
        import codecs

        UTF8Writer = codecs.getwriter("utf8")
        sys.stdout = UTF8Writer(sys.stdout)

    if ctx.invoked_subcommand == "package" and python is None:
        python = "python"  # Use invoker python by default when packaging

    CFG.set_cli(config, delivery, index, python)
    if ctx.invoked_subcommand != "package":
        CFG.set_base(find_base())

    runez.log.setup(
        debug=debug,
        console_format="%(levelname)s %(message)s" if debug else "%(message)s",
        console_level=logging.WARNING,
        console_stream=sys.stderr,
        locations=None,
    )


def auto_upgrade_v1(cfg):
    """Look for v1 installations, and upgrade them to v2"""
    v1 = V1Status(cfg)
    if v1.installed:
        # On first auto-upgrade pickley (ran in background by wrapper)
        setup_audit_log(cfg)
        inform("Auto-upgrading %s packages with pickley v2" % len(v1.installed))
        for prev in v1.installed:
            pspec = PackageSpec(cfg, prev.name)
            try:
                manifest = perform_install(pspec, is_upgrade=False, quiet=False)
                if manifest and manifest.entrypoints and prev.entrypoints:
                    for old_ep in prev.entrypoints:
                        if old_ep not in manifest.entrypoints:
                            runez.delete(os.path.join(cfg.base.path, old_ep))

            except BaseException:
                inform("%s could not be upgraded, please reinstall it" % runez.red(prev.name))
                if prev.entrypoints:
                    for old_ep in prev.entrypoints:
                        runez.delete(os.path.join(cfg.base.path, old_ep))

            inform("----")

        v1.clean_old_files()
        inform("Done")
        sys.exit(0)


def _location_grand_parent():
    return runez.parent_folder(runez.parent_folder(__file__))


def needs_bootstrap(pickleyspec=None):
    if pickleyspec is None:
        return ".whl" in _location_grand_parent() or "pickley.bootstrap" in sys.argv[0]

    if pickleyspec.python and not pickleyspec.python.problem and CFG.available_pythons.invoker.major < pickleyspec.python.major:
        return True  # A better python became available

    manifest = pickleyspec.get_manifest()
    if not manifest or not manifest.version:
        return True  # We're running from an old pickley v1 install (no v2 manifest)


def bootstrap():
    """Bootstrap pickley (reinstall with venv instead of downloaded pex package)"""
    pickleyspec = PackageSpec(CFG, "%s==%s" % (PICKLEY, __version__))
    grand_parent = _location_grand_parent()
    if grand_parent and grand_parent.endswith(".whl"):
        # We are running from a pex package
        setup_audit_log()
        LOG.debug("Bootstrapping pickley %s with %s" % (pickleyspec.version, pickleyspec.python))
        venv = PythonVenv(pickleyspec)
        venv.pip_install("wheel")
        with runez.TempFolder():
            venv.run_python("-mwheel", "pack", grand_parent)
            names = os.listdir(".")
            if len(names) == 1:
                venv.pip_install(names[0])

            else:
                abort("Internal error: wheel pack %s" % grand_parent)

        delivery = DeliveryMethod.delivery_method_by_name(pickleyspec.settings.delivery)
        delivery.ping = False
        delivery.install(pickleyspec, venv, {PICKLEY: "bootstrapped"})
        runez.run(pickleyspec.exe_path(PICKLEY), "auto-upgrade", fatal=None, stdout=None, stderr=None)  # Trigger auto_upgrade_v1()
        sys.exit(0)

    if "pickley.bootstrap" in sys.argv[0]:
        # We're running from pass1 bootstrap
        setup_audit_log()
        with SoftLock(pickleyspec.lock_path, give_up=60, invalid=60):
            VenvPackager.install(pickleyspec, ping=False)

        LOG.debug("Pass 2 bootstrap done")
        runez.run(pickleyspec.exe_path(PICKLEY), "auto-upgrade", fatal=None, stdout=None, stderr=None)  # Trigger auto_upgrade_v1()
        sys.exit(0)

    if needs_bootstrap(pickleyspec):
        setup_audit_log()
        with SoftLock(pickleyspec.lock_path, give_up=60, invalid=60):
            delivery = DeliveryMethod.delivery_method_by_name(pickleyspec.settings.delivery)
            delivery.ping = False
            venv = PythonVenv(pickleyspec, folder=pickleyspec.cfg.meta.full_path(PICKLEY, "pickley.bootstrap"))
            venv.pip_install(pickleyspec.specced)
            src = venv.bin_path(PICKLEY)
            dest = pickleyspec.exe_path(PICKLEY)
            delivery._install(pickleyspec, dest, src)

        LOG.debug("Pass 1 bootstrap done")
        runez.run(pickleyspec.exe_path(PICKLEY), "auto-upgrade", fatal=None, stdout=None, stderr=None)  # Trigger pass 2
        sys.exit(0)


@main.command(name="auto-upgrade")
@click.option("--force", is_flag=True, help="Force auto-upgrade check, even if recently checked")
@click.argument("package", required=False)
def auto_upgrade(force, package):
    """Background auto-upgrade command (called by wrapper)"""
    if not package or needs_bootstrap():
        # We may need to bootstrap, or auto-upgrade v1, covered by test_config:test_v1
        bootstrap()
        auto_upgrade_v1(CFG)
        if not package:
            sys.exit(0)

    pspec = PackageSpec(CFG, package)
    ping = pspec.ping_path
    if not force and runez.file.is_younger(ping, CFG.version_check_delay(pspec) * 60):
        LOG.debug("Skipping auto-upgrade, checked recently")
        sys.exit(0)

    runez.touch(ping)
    if runez.file.is_younger(pspec.lock_path, CFG.install_timeout(pspec) * 60):
        LOG.debug("Lock file present, another installation is in progress")
        sys.exit(0)

    perform_install(pspec, is_upgrade=True, force=False, quiet=True)


@main.command()
def base():
    """Show pickley's base folder"""
    print(CFG.base.path)


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


@main.command()
@runez.click.border("-b", default="colon")
@click.option("--verbose", "-v", is_flag=True, help="Show internal info")
def diagnostics(border, verbose):
    """Show diagnostics info"""
    table = PrettyTable(2, border=border)
    table.header[0].align = "right"
    table.header[1].style = "bold"
    table.add_row("base", runez.short(CFG.base))
    python = CFG.find_python()
    table.add_row("python", "%s %s" % (runez.short(python.executable), runez.dim("(%s)" % python.version)))
    table.add_row("sys.executable", runez.short(sys.executable))
    table.add_row("default index", CFG.default_index)
    table.add_row("pip.conf", runez.short(CFG.pip_conf, none=runez.dim("-not found-")))

    if verbose:
        table.add_row("sys.prefix", runez.short(sys.prefix))
        table.add_row("sys.arg[0]", runez.short(sys.argv[0]))
        table.add_row("__file__", runez.short(__file__))

    print(table)


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
    packages = CFG.package_specs()
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
            table.add_row(pspec.dashed, manifest.version, manifest.delivery, manifest.python, manifest.index)

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
@click.option("--no-sanity-check", is_flag=True, help="Disable sanity check")
@click.option("--sanity-check", default="--version", show_default=True, help="Args to invoke produced package for sanity check")
@click.option("--requirement", "-r", multiple=True, help="Install from the given requirements file (can be used multiple times)")
@click.argument("folder", required=True)
def package(build, dist, symlink, no_compile, no_sanity_check, sanity_check, folder, requirement):
    """Package a project from source checkout"""
    folder = runez.resolved_path(folder)
    if not os.path.isdir(folder):
        abort("Folder %s does not exist" % runez.red(runez.short(folder)))

    if no_sanity_check:
        sanity_check = None

    finalizer = PackageFinalizer(folder, build, dist, symlink, sanity_check, requirement, compile=not no_compile)
    problem = finalizer.resolve()
    if problem:
        abort(problem)

    report = finalizer.finalize()
    if report:
        inform("")
        inform(report)
        inform("")

    inform("Packaged %s successfully" % runez.bold(runez.short(folder)))


class PackageFinalizer(object):
    """
    This class allows to have an early check on provided settings, and wrap them up
    """

    package_name = None  # type: str # Name from associated setup.py (after call to resolve())
    package_version = None  # type: str # Version from associated setup.py (after call to resolve())

    def __init__(self, folder, build, dist, symlink, sanity_check, requirement, compile=True, border="reddit"):
        """
        Args:
            folder (str): Folder where project to be packaged resides (must have a setup.py)
            build (str): Full path to folder to use as build folder
            dist (str): Relative path to folder to use as 'dist' (where to deliver package)
            symlink (str | None): Synlink specification, of the form 'root:root/...'
            sanity_check (str | None): CLI to use as sanity check for packaged exes (default: --version)
            requirement (list | None): Optional list of requirements files
            compile (bool): Don't byte-compile pacakged venv
            border (str): Border to use for PrettyTable overview
        """
        self.folder = folder
        self.build = runez.resolved_path(build)
        self.dist = dist
        self.symlink = Symlinker(symlink) if symlink else None
        self.sanity_check = sanity_check
        self.compile = compile
        self.border = border
        default_req = runez.resolved_path("requirements.txt", base=folder)
        if not requirement and os.path.exists(default_req):
            requirement = [default_req]

        if requirement:
            requirement = [("-r", runez.resolved_path(r, base=folder)) for r in requirement]

        requirement = runez.flattened(requirement, shellify=True)
        requirement.append(folder)
        self.requirements = requirement

    def resolve_dist(self):
        """Resolve 'dist' folder, taking into account possible debian mode"""
        if self.dist.startswith("root/"):
            # Special case: we're targeting 'root/...' probably for a debian, use target in that case to avoid venv relocation issues
            target = self.dist[4:]
            if os.path.isdir(target):
                LOG.debug("debian mode: %s -> %s", self.dist, target)
                self.dist = target

            parts = self.dist.split("/")
            if len(parts) <= 2:
                # Auto-add package name to targets of the form root/subfolder (most typical case)
                self.dist = os.path.join(self.dist, self.package_name)

    def resolve(self):
        with runez.CurrentFolder(self.folder, anchor=True):
            # Some setup.py's assume current folder is the one with their setup.py
            if not os.path.exists("setup.py"):
                return "No setup.py in %s" % self.folder

            r = runez.run(sys.executable, "setup.py", "--name", dryrun=False, fatal=False, logger=None)
            self.package_name = r.output
            if r.failed or not self.package_name:
                return "Could not determine package name from setup.py"

            validate_pypi_name(self.package_name)
            r = runez.run(sys.executable, "setup.py", "--version", dryrun=False, fatal=False, logger=None)
            self.package_version = r.output
            if r.failed or not self.package_version:
                return "Could not determine package version from setup.py"

            self.resolve_dist()

    @staticmethod
    def validate_sanity_check(exe, sanity_check):
        if not exe or not sanity_check:
            return None

        r = runez.run(exe, sanity_check, fatal=False, logger=None)
        exe_info = r.output or r.error
        if r.failed:
            if does_not_implement_cli_flag(r.output, r.error):
                return "does not respond to %s" % sanity_check

            abort("'%s' failed %s sanity check: %s" % (exe, sanity_check, r.full_output))

        return runez.first_line(exe_info)

    def finalize(self):
        """Run sanity check and/or symlinks, and return a report"""
        with runez.Anchored(self.folder):
            runez.ensure_folder(self.build, clean=True)
            CFG.set_base(self.build)
            pspec = PackageSpec(CFG, specced(self.package_name, self.package_version))
            dist_folder = runez.resolved_path(self.dist)
            exes = PACKAGER.package(pspec, self.build, dist_folder, self.requirements)
            if exes:
                report = PrettyTable(["Executable", self.sanity_check], border=self.border)
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
            runez.delete(dest)
            r = runez.symlink(exe, dest, must_exist=False)
            if r > 0:
                inform("Symlinked %s -> %s" % (runez.short(dest), runez.short(exe)))

        else:
            LOG.debug("'%s' does not exist, skipping symlink" % exe)


def delete_file(path):
    if runez.delete(path, fatal=False, logger=None) > 0:
        return 1

    return 0


def clean_compiled_artifacts(folder):
    """Remove usual byte-code compiled artifacts from `folder`"""
    # See https://www.debian.org/doc/packaging-manuals/python-policy/ch-module_packages.html
    deleted = delete_file(os.path.join(folder, "share", "python-wheels"))
    dirs_to_be_deleted = []
    for root, dirs, files in os.walk(folder):
        for basename in dirs[:]:
            if basename == "__pycache__":
                dirs.remove(basename)
                dirs_to_be_deleted.append(os.path.join(root, basename))

        for basename in files:
            if basename.lower().endswith(".pyc"):
                deleted += delete_file(os.path.join(root, basename))

    for path in dirs_to_be_deleted:
        deleted += delete_file(path)

    if deleted:
        logging.debug("Deleted %s compiled artifacts", deleted)
