"""
See https://github.com/codrsquad/pickley
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

import click
import runez
from runez.pyenv import Version
from runez.render import PrettyTable

from pickley import bstrap, CFG, PackageSpec, PICKLEY, Reporter, ResolvedPackage, TrackedSettings
from pickley.package import VenvPackager

LOG = logging.getLogger(__name__)


class Requirements(NamedTuple):
    requirement_files: Sequence[Path]
    additional_packages: Optional[Sequence[str]]
    project: Path


def setup_audit_log():
    """Setup audit.log log file handler"""
    if CFG.use_audit_log and not runez.log.file_handler:
        if runez.color.is_coloring():
            runez.log.progress.start(message_color=runez.dim, spinner_color=runez.bold)

        log_path = CFG.meta / "audit.log"
        runez.ensure_folder(CFG.meta)
        runez.log.setup(
            # PID number to distinguish parallel runs, inlined because some trace records are emitted in a custom way
            file_format=f"%(asctime)s %(timezone)s [{os.getpid()}] %(levelname)s - %(message)s",
            file_location=str(log_path),
            rotate="size:500k",
            rotate_count=4,
        )
        Reporter.flush_pending_records()


class SoftLockException(Exception):
    """Raised when soft lock can't be acquired"""


class SoftLock:
    """
    Simple soft file lock mechanism, allows to ensure only one pickley is working on a specific installation.
    A lock is a simple file containing 2 lines: process id holding it, and the CLI args it was invoked with.
    """

    def __init__(self, canonical_name, give_up=None, invalid=None):
        """
        Args:
            canonical_name (str): Canonical name of package to acquire lock for
            give_up (int | None): Timeout in seconds after which to give up (raise SoftLockException) if lock could not be acquired
            invalid (int | None): Age in seconds after which to consider existing lock as invalid
        """
        self.canonical_name = canonical_name
        self.lock_path = CFG.soft_lock_path(canonical_name)
        self.give_up = give_up or CFG.install_timeout(canonical_name)
        self.invalid = invalid or self.give_up * 2

    def __repr__(self):
        return f"lock {self.canonical_name}"

    def _locked_by(self):
        """
        Returns:
            (str): CLI args of process holding the lock, if any
        """
        if self.lock_path:
            if self.invalid and self.invalid > 0 and not runez.file.is_younger(self.lock_path, self.invalid):
                return None  # Lock file does not exist or invalidation age reached

            pid = None
            for line in runez.readlines(self.lock_path):
                if pid is not None:
                    return line  # 2nd line hold CLI args process was invoked with

                pid = runez.to_int(line)
                if not runez.check_pid(pid):
                    return None  # PID is no longer active

    def __enter__(self):
        """Acquire lock"""
        if self.lock_path:
            cutoff = time.time() + self.give_up
            holder_args = self._locked_by()
            while holder_args:
                if time.time() >= cutoff:
                    lock = runez.bold(runez.short(self.lock_path))
                    holder_args = runez.bold(holder_args)
                    raise SoftLockException(f"Can't grab lock {lock}, giving up\nIt is being held by: pickley {holder_args}")

                time.sleep(1)
                holder_args = self._locked_by()

            # We got the soft lock
            msg = "Would acquire" if runez.DRYRUN else "Acquired"
            runez.log.trace(f"{msg} {runez.short(self.lock_path)}")
            runez.write(self.lock_path, runez.joined(os.getpid(), runez.quoted(sys.argv[1:]), delimiter="\n"), logger=None)

        return self

    def __exit__(self, *_):
        """Release lock"""
        if self.lock_path:
            msg = "Would release" if runez.DRYRUN else "Released"
            runez.log.trace(f"{msg} {runez.short(self.lock_path)}")
            runez.delete(self.lock_path, logger=None)


def perform_install(pspec: PackageSpec, quiet=False):
    """
    Parameters
    ----------
    pspec : PackageSpec
        Package spec to install
    quiet : bool
        If True, don't chatter
    """
    runez.abort_if(pspec.problem, f"Can't install {pspec}: {runez.red(pspec.problem)}")
    if not pspec.is_clear_for_installation():
        if pspec.is_facultative:
            LOG.info("Skipping facultative installation '%s', not installed by pickley", pspec)
            return

        runez.abort(f"{runez.red(pspec.canonical_name)} is not installed by pickley, please uninstall it first")

    with SoftLock(pspec.canonical_name):
        started = time.time()
        upgrade_reason = None
        if CFG.version_check_delay and pspec.manifest:
            # When --force is used `version_check_delay` is zero (we force-install)
            upgrade_reason = pspec.upgrade_reason()
            if not upgrade_reason:
                logger = Reporter.trace if quiet else Reporter.inform
                logger(f"{pspec.canonical_name} v{runez.bold(pspec.currently_installed_version)} is already installed")
                pspec.groom_installation()
                return

        setup_audit_log()
        manifest = VenvPackager.install(pspec)
        if manifest and not quiet:
            note = f" in {runez.represented_duration(time.time() - started)}"
            if upgrade_reason:
                note += f"  (reason: {upgrade_reason})"

            action = "Would state: Installed" if runez.DRYRUN else "Installed"
            if runez.DRYRUN:
                action = f"Would state: {action}"

            LOG.info("%s %s v%s%s", action, pspec.canonical_name, runez.bold(pspec.target_version), runez.dim(note))

        pspec.groom_installation()


def perform_upgrade(pspec: PackageSpec, fatal=True, logger=LOG.debug, verb="auto-upgrade"):
    """
    Parameters
    ----------
    pspec : PackageSpec
        Package to upgrade
    fatal : bool
        If True, abort on error
    logger : callable
        Logger to use
    verb : str
        Verb to use to convey what kind of installation is being done (ex: auto-heal)
    """
    if not pspec.is_uv:
        manifest = pspec.manifest
        if not manifest or not manifest.version:
            return runez.abort(f"Can't upgrade '{runez.red(pspec)}': not installed with pickley", fatal=fatal)

    if pspec.problem:
        return runez.abort(f"Can't {verb} {runez.red(pspec)}: {runez.red(pspec.problem)}", fatal=fatal)

    with SoftLock(pspec.canonical_name):
        started = time.time()
        upgrade_reason = pspec.upgrade_reason()
        if not upgrade_reason:
            logger("%s v%s is already up-to-date", pspec.canonical_name, runez.bold(pspec.currently_installed_version))
            pspec.groom_installation()
            return

        setup_audit_log()
        manifest = VenvPackager.install(pspec, fatal=fatal)
        outcome = ""
        action = "%s%sed" % (verb[0].upper(), verb.rstrip("e")[1:])
        if runez.DRYRUN:
            outcome = "Would state: "

        elif not manifest:  # pragma: no cover, for auto-heal output
            # We failed to upgrade, in non-fatal mode (auto-heal)
            logger = LOG.error
            outcome = f"{runez.red('Failed')} to "
            action = verb.lower()

        note = f" (upgrade reason: {upgrade_reason}) in {runez.represented_duration(time.time() - started)}"
        logger(f"{outcome}{action} {runez.bold(pspec.canonical_name)} v{runez.bold(pspec.target_version)}{runez.dim(note)}")
        if manifest:
            pspec.groom_installation()


def _find_base_from_program_path(path: Path):
    if not path or len(path.parts) <= 1:
        return None

    if path.name in (bstrap.DOT_META, ".pickley"):
        return path.parent  # We're running from an installed pickley

    if path.name == ".venv":
        return path / "dev_mode"  # Convenience for development

    return _find_base_from_program_path(path.parent)


def find_base(path=None):
    base_path = CFG.resolved_path(os.environ.get("PICKLEY_ROOT"))
    if base_path:
        if not base_path.is_dir():
            runez.abort(f"PICKLEY_ROOT points to non-existing directory {runez.red(base_path)}")

        runez.log.trace(f"Using base PICKLEY_ROOT={runez.short(base_path)}")
        return base_path

    path = CFG.resolved_path(path or sys.argv[0])
    return _find_base_from_program_path(path) or path.parent


@click.group()
@click.pass_context
@click.version_option(message="%(version)s", version=CFG.pickley_version)
@click.option("--verbose", "-v", "--debug", count=True, default=0, help="Show verbose output")
@runez.click.dryrun("-n")
@runez.click.color()
@click.option("--config", "-c", metavar="PATH", help="Optional additional configuration to use")
@click.option("--index", "-i", metavar="PATH", help="Pypi index to use")
@click.option("--python", "-p", "-P", metavar="PATH", help="Python interpreter to use")
@click.option("--delivery", "-d", help="Delivery method to use")
@click.option("--package-manager", type=click.Choice(("uv", "pip")), help="What to use to create venvs? (default: uv)")
def main(ctx, verbose, config, index, python, delivery, package_manager):
    """Install python CLIs that keeps themselves up-to-date"""
    runez.system.AbortException = SystemExit
    console_level = logging.INFO
    if ctx.invoked_subcommand == "package":
        console_level = logging.DEBUG
        if package_manager is None:
            package_manager = "pip"  # Default to pip for package command for now

    if verbose > 1:
        os.environ["TRACE_DEBUG"] = "1"

    CFG.verbosity = verbose
    runez.log.setup(
        debug=verbose or os.environ.get("TRACE_DEBUG"),
        console_level=console_level,
        console_format="%(message)s",
        console_stream=sys.stderr,
        file_level=logging.DEBUG,
        locations=None,
        trace="TRACE_DEBUG+: ",
    )
    # Don't pollute audit.log with dryrun or non-essential commands
    essential = ("auto-heal", "auto-upgrade", "install", "run", "upgrade")
    special_commands = ("bootstrap", "uninstall")
    CFG.use_audit_log = not runez.DRYRUN and (ctx.invoked_subcommand in essential or ctx.invoked_subcommand in special_commands)
    Reporter.capture_trace()
    runez.log.trace(runez.log.spec.argv)
    bstrap.clean_env_vars()
    if runez.SYS_INFO.platform_id.is_macos and "ARCHFLAGS" not in os.environ and runez.SYS_INFO.platform_id.arch:
        # Ensure the proper platform is used on macos
        archflags = f"-arch {runez.SYS_INFO.platform_id.arch}"
        runez.log.trace(f"Setting ARCHFLAGS={archflags}")
        os.environ["ARCHFLAGS"] = archflags

    CFG.set_cli(config, delivery, index, python, package_manager)
    if ctx.invoked_subcommand not in ("bootstrap", "package"):
        CFG.set_base(find_base())
        runez.Anchored.add(CFG.base)

    bstrap.set_mirror_env_vars(index or CFG.index)
    if ctx.invoked_subcommand in essential and "uv" not in sys.argv and ctx.invoked_subcommand not in ("bootstrap", "uninstall"):
        # `uv` is a special case, it does not need a python venv, it is used to determine what to install for all other packages.
        # We auto-check for new versions of `uv` twice per day, this can be hastened by user running an explicit `pickley upgrade uv`.
        if (bstrap.USE_UV or package_manager == "uv") and not CFG.uv_bootstrap.freshly_bootstrapped:
            auto_upgrade_uv()


def auto_upgrade_uv(cooldown_hours=12):
    """
    Automatic periodic upgrade of `uv`.

    Parameters
    ----------
    cooldown_hours : int
        Cooldown period in hours, auto-upgrade won't be attempted any more frequently than that.
    """
    if not CFG.uv_bootstrap.freshly_bootstrapped:
        cooldown_path = CFG.cache / "uv.cooldown"
        if not cooldown_hours or not runez.file.is_younger(cooldown_path, cooldown_hours * runez.date.SECONDS_IN_ONE_HOUR):
            runez.touch(cooldown_path)
            settings = TrackedSettings()
            settings.auto_upgrade_spec = "uv"
            pspec = PackageSpec("uv", settings=settings)

            # Automatic background upgrade of `uv` is not treated as fatal, for more resilience
            perform_upgrade(pspec, fatal=False)


@main.command()
def auto_heal():
    """
    Automatically re-install unhealthy packages

    \b
    Reasons a package wouldn't be "healthy" anymore:
    - Base python used to install the packages' venv is not available anymore
    - The pickley-generated wrapper points to files that have now been deleted
    """
    CFG.require_bootstrap()
    total = healed = 0
    for spec in CFG.installed_specs():
        total += 1
        reason = spec.upgrade_reason()
        if not reason:
            print("%s is healthy and up-to-date" % runez.bold(spec))
            continue

        healed += 1
        perform_upgrade(spec, fatal=False, logger=LOG.info, verb="auto-heal")

    print("Auto-healed %s / %s packages" % (healed, total))


@main.command()
@click.option("--force", is_flag=True, help="Force auto-upgrade check, even if recently checked")
@click.argument("package", required=True)
def auto_upgrade(force, package):
    """Background auto-upgrade command (called by wrapper)"""
    if package != PICKLEY:
        # Auto-upgrade of pickley will mark current installation as bootstrapped
        CFG.require_bootstrap()

    canonical_name = CFG.required_canonical_name(package)
    pspec = PackageSpec(canonical_name)
    cooldown_path = CFG.cache / f"{canonical_name}.cooldown"
    if not force and runez.file.is_younger(cooldown_path, CFG.version_check_delay):
        LOG.debug("Skipping auto-upgrade, checked recently")
        return

    lock_path = CFG.soft_lock_path(canonical_name)
    if runez.file.is_younger(lock_path, CFG.install_timeout(canonical_name)):
        LOG.debug("Lock file '%s' present, another installation is in progress", runez.short(lock_path))
        return

    runez.touch(cooldown_path)
    perform_upgrade(pspec)


@main.command()
@click.argument("what", required=False)
def base(what):
    """Show pickley base folder"""
    path = CFG.base
    if what:
        paths = {"audit.log": CFG.meta / "audit.log", "config.json": CFG.meta / "config.json", "uv": CFG.base}
        path = paths.get(what)
        if not path:
            all_installed = set(CFG.scan_installed())
            if what in all_installed:
                pspec = PackageSpec(what)
                path = pspec.target_installation_folder()

        if not path:
            options = [runez.green(s) for s in sorted(paths)]
            runez.abort(f"Can't find '{runez.red(what)}', try: {', '.join(options)}, or the name of an installed package")

    print(path)


@main.command(hidden=True)
@click.argument("base_folder", required=True)
@click.argument("pickley_spec", required=False)
def bootstrap(base_folder, pickley_spec):
    """
    Bootstrap pickley.

    \b
    This is an internal command, called by the bootstrap `bstrap.py` script from a temporary venv.
    """
    base_folder = CFG.resolved_path(base_folder)
    if not base_folder.exists():
        runez.abort(f"Folder {runez.red(runez.short(base_folder))} does not exist, please create it first")

    CFG.set_base(base_folder)
    runez.Anchored.add(CFG.base)
    setup_audit_log()
    if bstrap.USE_UV:
        auto_upgrade_uv()

    bootstrap_marker = CFG.manifests / ".bootstrap.json"
    if not bootstrap_marker.exists():
        # Some older pickley versions get confused by `uv` not being a wrapper anymore, clean things up first
        runez.delete(CFG.base / bstrap.PICKLEY)
        runez.delete(CFG.base / ".pickley")  # Clean up very old pickley `DOT_META` folder (in case one is still out there)
        CFG.version_check_delay = 0  # Implies a --force install

    pspec = PackageSpec(pickley_spec or bstrap.PICKLEY, authoritative=bool(pickley_spec))
    perform_install(pspec)


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force check, even if checked recently")
@click.argument("packages", nargs=-1, required=False)
def check(force, packages):
    """Check whether specified packages need an upgrade"""
    CFG.require_bootstrap()
    if force:
        CFG.version_check_delay = 0

    code = 0
    packages = CFG.package_specs(packages) or CFG.installed_specs()
    if not packages:
        print("No packages installed")
        sys.exit(0)

    for pspec in packages:
        dv = pspec.target_version
        if pspec.problem:
            msg = pspec.problem
            code = 1

        elif not pspec.currently_installed_version:
            msg = runez.dim(f"(v{dv} available)")
            if pspec.is_healthily_installed(entrypoints_only=True):
                msg = f"present, but not installed by pickley {msg}"

            else:
                msg = f"not installed {msg}"

            if not pspec.is_facultative:
                code = 1

        else:
            msg = f"v{runez.bold(dv)}"
            upgrade_reason = pspec.upgrade_reason()
            if upgrade_reason:
                msg += f" available (upgrade reason: {upgrade_reason})"

            else:
                msg += " up-to-date"

        if pspec.canonical_name != pspec.auto_upgrade_spec:
            msg += runez.dim(f" (tracks: {pspec.auto_upgrade_spec})")

        print(f"{runez.bold(pspec.canonical_name)}: {msg}")

    sys.exit(code)


@main.command()
def config():
    """Show current configuration"""
    print(CFG.represented())


@main.command()
@click.argument("packages", nargs=-1, required=True)
def describe(packages):
    """Describe a package spec (version and entrypoints)"""
    problems = 0
    for package_spec in packages:
        runez.abort_if(not package_spec, runez.red("Can't describe empty package spec"))
        package_spec = CFG.absolute_package_spec(package_spec)
        settings = TrackedSettings.from_cli(package_spec)
        info = ResolvedPackage()
        info.logger = LOG.debug
        info.resolve(settings)
        text = f"{runez.bold(info.canonical_name)}:"
        if info.version:
            text += f" version {runez.bold(info.version)}"

        text += runez.dim(f" ({info.resolution_reason})")
        print(text)
        if info.problem:
            problems += 1
            print(f"  problem: {runez.red(info.problem)}")

        ep = runez.joined(info.entrypoints, delimiter=", ") or runez.brown("-no entry points-")
        print(f"  entry-points: {runez.bold(ep)}")
        metadata = info._metadata
        if metadata and metadata.values:
            for k, v in sorted(metadata.values.items()):
                if k not in ("location", "name", "version") and not k.startswith("editable"):
                    print(f"  {k}: {v}")

    sys.exit(problems)


def _diagnostics():
    yield "base", CFG.base
    yield "preferred python", CFG.available_pythons.find_python(CFG.get_value("python"))
    yield "default index", CFG.default_index
    yield "pip.conf", CFG.pip_conf


@main.command()
def diagnostics():
    """Show diagnostics info"""
    print(PrettyTable.two_column_diagnostics(_diagnostics(), runez.SYS_INFO.diagnostics(), CFG.available_pythons.representation()))


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force installation, even if already installed")
@click.argument("packages", nargs=-1, required=True)
def install(force, packages):
    """Install a package from pypi"""
    CFG.require_bootstrap()
    if force:
        CFG.version_check_delay = 0

    setup_audit_log()
    specs = CFG.package_specs(packages, authoritative=True)
    runez.abort_if(not specs, f"Can't install '{runez.joined(packages)}', not configured")
    for pspec in specs:
        perform_install(pspec)


@main.command(name="list")
@runez.click.border("-b", default="github")
@click.option("--format", "-f", type=click.Choice(["csv", "json", "tsv", "yaml"]), help="Representation format")
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
def cmd_list(border, format, verbose):
    """List installed packages"""
    CFG.require_bootstrap()
    report = TabularReport("Package,Version,Python", additional="Delivery,PM,Track", border=border, verbose=verbose)
    default_package_manager = bstrap.default_package_manager()
    for pspec in CFG.installed_specs(include_pickley=True):
        manifest = pspec.manifest
        name = runez.bold(pspec.canonical_name)
        if pspec.canonical_name != pspec.auto_upgrade_spec:
            name += "📌"

        python = runez.dim("-not needed-") if pspec.is_uv else manifest and manifest.python_executable
        delivery = runez.dim("-") if pspec.is_uv else manifest and manifest.delivery
        package_manager = manifest and manifest.package_manager
        if package_manager and package_manager != default_package_manager:  # pragma: no cover, uncommon
            name += "👴"

        report.add_row(
            Package=name,
            Version=manifest and manifest.version,
            Python=python,
            Delivery=delivery,
            PM=runez.dim("-") if pspec.is_uv else package_manager,
            Track=manifest and manifest.settings and manifest.settings.auto_upgrade_spec,
        )

    print(report.represented(format))


@main.command()
@click.argument("command", type=click.Choice(["check", "freeze", "list", "show", "tree"]), required=True)
@click.argument("package", required=True)
def pip(command, package):
    """Run a diagnostics `pip` command in specified package"""
    from pickley.package import PythonVenv

    CFG.require_bootstrap()
    for pspec in CFG.package_specs(package):
        runez.abort_if(pspec.is_uv, f"This command does not apply to {runez.bold(pspec.canonical_name)}")
        cv = pspec.currently_installed_version
        runez.abort_if(not cv, f"{runez.red(pspec.canonical_name)} was not installed with pickley")
        venv_settings = pspec.settings.venv_settings()
        folder = CFG.meta / f"{pspec.canonical_name}-{cv}"
        venv = PythonVenv(folder, venv_settings)
        runez.log.trace(f"Using venv {venv}")
        venv.run_pip(command, pspec.canonical_name if command == "show" else None, stdout=None, stderr=None)


@main.command(add_help_option=False, context_settings={"ignore_unknown_options": True})
@click.argument("command")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def run(command, args):
    """
    Run a python CLI (auto-install it if needed)

    \b
    Examples:
         pickley run black
         pickley run pip-compile
         pickley run flake8 src/
    """
    CFG.require_bootstrap()
    if command == "--help":
        click.echo(click.get_current_context().get_help())
        return

    RunSetup.perform_run(command, args)


@main.command()
@click.option("--all", is_flag=True, help="Uninstall everything pickley-installed, including pickley itself")
@click.argument("packages", nargs=-1, required=False)
def uninstall(all, packages):
    """Uninstall packages"""
    CFG.require_bootstrap()
    runez.abort_if(packages and all, f"Either specify packages to uninstall, or --all (but {runez.bold('not both')})")
    runez.abort_if(not packages and not all, f"Specify {runez.bold('packages to uninstall')}, or {runez.bold('--all')}")
    packages = CFG.package_specs(packages) or CFG.installed_specs()
    if not all:
        special = [runez.bold(p.canonical_name) for p in packages if p.canonical_name in (bstrap.PICKLEY, "uv")]
        if special and not all:
            special = runez.joined(special, delimiter=" or ")
            runez.abort(f"Run '{runez.bold('uninstall --all')}' if you wish to uninstall {special}")

    setup_audit_log()
    for pspec in packages:
        runez.abort_if(not pspec.currently_installed_version, f"{runez.bold(pspec.canonical_name)} was not installed with pickley")
        for ep in pspec.manifest.entrypoints:
            runez.delete(CFG.base / ep)

        pspec.uninstall_all_files()
        action = "Would uninstall" if runez.DRYRUN else "Uninstalled"
        LOG.info("%s %s", action, pspec)

    if all:
        runez.delete(CFG.base / bstrap.PICKLEY)
        runez.delete(CFG.meta)
        msg = "Would uninstall" if runez.DRYRUN else "Uninstalled"
        overview = runez.short(runez.joined(sorted(packages), delimiter=", "))
        msg = f"{msg} pickley and {runez.plural(packages, 'package')}: {overview}"
        LOG.info(msg)


@main.command()
@click.argument("packages", nargs=-1, required=False)
def upgrade(packages):
    """Upgrade an installed package"""
    CFG.require_bootstrap()
    setup_audit_log()
    for pspec in CFG.package_specs(packages):
        perform_upgrade(pspec, logger=LOG.info, verb="upgrade")


@main.command()
@click.option("--system", "-s", is_flag=True, help="Look at system PATH (not just pickley installs)")
@click.argument("programs", nargs=-1)
def version_check(system, programs):
    """Check that programs are present with a minimum version"""
    runez.abort_if(not programs, "Specify at least one program to check")
    specs = []
    for program_spec in programs:
        program, _, min_version = program_spec.partition(":")
        min_version = Version(min_version)
        runez.abort_if(not program or not min_version.is_valid, f"Invalid argument '{program_spec}', expecting format <program>:<version>")
        specs.append((program, min_version))

    overview = []
    for program, min_version in specs:
        if system:
            full_path = runez.which(program)
            runez.abort_if(not full_path, f"{program} is not installed")

        else:
            full_path = CFG.base / program

        version = CFG.program_version(full_path)
        runez.abort_if(not version, f"{runez.red(program)} is not installed in {runez.bold(runez.short(CFG.base))}")
        if version < min_version:
            runez.abort(f"{runez.bold(runez.short(full_path))} version too low: {runez.red(version)} (need {runez.bold(min_version)}+)")

        overview.append(f"{program} {version}")

    if overview:
        print(runez.short(runez.joined(overview, delimiter=" ; ")))


@main.command()
@click.option("--base", "-b", help="Folder to use as base folder")
@click.option("--dist", "-d", default="./dist", show_default=True, help="Folder where to produce package")
@click.option("--symlink", "-s", help="Create symlinks for debian-style packaging, example: root:root/usr/local/bin")
@click.option("--no-compile", is_flag=True, help="Don't byte-compile packaged venv")
@click.option("--sanity-check", default=None, show_default=True, help="Args to invoke produced package as a sanity check")
@click.option("--requirement", "-r", multiple=True, help="Install from the given requirements file (can be used multiple times)")
@click.argument("project", required=True)
@click.argument("additional", nargs=-1)
def package(base, dist, symlink, no_compile, sanity_check, project, requirement, additional):
    """Package a project from source checkout"""
    if not base:
        base = CFG.resolved_path(".")
        if ".tox" not in base.parts and "build" not in base.parts:
            # Make sure we're running from a git-ignored folder
            base = base / ".tox/_pickley_package"
            runez.ensure_folder(base)
            LOG.info("Using '%s/' as base folder", runez.short(base))

    CFG.set_base(base)
    project = CFG.resolved_path(project)
    runez.log.spec.default_logger = LOG.info
    with runez.CurrentFolder(project, anchor=True):
        finalizer = PackageFinalizer(project, dist, symlink, requirement, additional)
        finalizer.sanity_check = sanity_check
        finalizer.compile = not no_compile
        finalizer.produce_package()


class TabularReport:
    """Reports that can be shown as table, csv or json"""

    def __init__(self, columns, additional=None, border="github", verbose=False):
        """
        Args:
            columns (str | list): Column headers
            additional (str | list | None): Additional column headers (showed when verbose is True)
            border (str): Tabel border to use
            verbose (bool): If True, show additional columns as well
        """
        self.border = border
        self.verbose = verbose
        cols = (columns, additional) if verbose else columns
        self.columns = runez.flattened(cols, split=",")
        self.table = PrettyTable(self.columns, border=border)
        self.mapped_values = []
        self.values = []

    @staticmethod
    def _json_key(key):
        return runez.joined(runez.words(key.lower()), delimiter="-")

    def add_row(self, **kwargs):
        values = [kwargs.get(n) for n in self.columns]
        self.mapped_values.append({self._json_key(k): runez.uncolored(v) for k, v in kwargs.items() if k in self.columns})
        self.values.append(values)
        self.table.add_row(values)

    def represented(self, fmt):
        if fmt in ("csv", "tsv"):
            delimiter = "\t" if fmt == "tsv" else ","
            lines = [runez.joined(self.columns, delimiter=delimiter)]
            lines.extend(runez.joined([runez.uncolored(x) for x in v], delimiter=delimiter) for v in self.values)
            return runez.joined(lines, delimiter="\n")

        if fmt == "json":
            return runez.represented_json(self.mapped_values)

        if fmt == "yaml":
            lines = []
            for value in self.mapped_values:
                text = [f" {k}: {value[k]}" for k in sorted(value)]
                text = runez.joined(text, delimiter="\n ")
                lines.append(f"-{text}")

            return runez.joined(lines, delimiter="\n")

        return self.table.get_string()


class RunSetup:
    """Convenience defaults to use for 'run' commands"""

    def __init__(self, command, package=None, pinned=None):
        """
        Args:
            command (str): Name of command to run
            package (str | None): Pypi package name to auto-install in venv (default: same as `command`)
            pinned (str | None): Pinned version to use (implies per-project install)
        """
        self.command = command
        self.package = package
        self.pinned = pinned

    def __repr__(self):
        return self.specced if self.package == self.command else f"{self.specced}:{self.command}"

    @property
    def specced(self):
        return f"{self.package}=={self.pinned}" if self.pinned else self.package

    @classmethod
    def from_cli(cls, command):
        package = command
        if ":" in command:
            # Allows to support cases where command name is different from pypi package name, ex: awscli:aws
            package, _, command = command.rpartition(":")

        package, pinned_package = CFG.despecced(package)
        command, pinned_command = CFG.despecced(command)
        pinned = pinned_package or pinned_command
        rs = getattr(cls, "cmd_" + command.replace("-", "_"), None)
        if rs:
            rs = rs()
            if pinned:
                rs.pinned = pinned

            return rs

        return cls(command, package=package, pinned=pinned)

    @classmethod
    def perform_run(cls, command, args):
        rs = cls.from_cli(command)
        pspec = PackageSpec(rs.specced, authoritative=bool(rs.pinned))
        if not pspec.currently_installed_version:
            perform_install(pspec, quiet=True)

        runez.log.progress.stop()
        r = runez.run(CFG.base / rs.command, args, stdout=None, stderr=None, fatal=False)
        sys.exit(r.exit_code)

    @classmethod
    def cmd_aws(cls):
        return cls("aws", package="awscli")

    @classmethod
    def cmd_pip_compile(cls):
        return cls("pip-compile", package="pip-tools")


class PackageFinalizer:
    """
    This class allows to have an early check on provided settings, and wrap them up
    """

    pspec = None  # type: PackageSpec

    def __init__(self, project: Path, dist: str, symlink: Optional[str], requirement_files: Sequence[str], additional: Sequence[str]):
        """
        Parameters
        ----------
        project : Path
            Folder where project to be packaged resides (must have a setup.py)
        dist : str
            Relative path to folder to use as 'dist' (where to deliver package)
        symlink : str | None
            Symlink specification, of the form 'root:root/...'
        requirement_files : Sequence[str]
            Requirement files to use
        additional : Sequence[str]
            Additional requirements
        """
        self.folder = project
        self.dist = dist
        self.symlink = Symlinker(symlink) if symlink else None
        self.sanity_check = None
        self.compile = True
        self.border = "reddit"
        if not requirement_files:
            default_req = CFG.resolved_path("requirements.txt", base=self.folder)
            if default_req.exists():
                requirement_files = default_req

        requirement_files = [CFG.resolved_path(r, base=self.folder) for r in runez.flattened(requirement_files)]
        self.requirements = Requirements(requirement_files, additional, self.folder)

    def produce_package(self):
        """Run sanity check and/or symlinks, and return a report"""
        runez.abort_if(not self.folder.is_dir(), f"Folder {runez.red(runez.short(self.folder))} does not exist")
        self.pspec = PackageSpec(str(self.folder), authoritative=True)
        runez.abort_if(not self.pspec.canonical_name, f"Could not determine package name: {self.pspec.problem}")
        if self.dist.startswith("root/"):
            # Special case: we're targeting 'root/...' probably for a debian, use target in that case to avoid venv relocation issues
            target = self.dist[4:]
            if os.path.isdir(target):
                LOG.debug("debian mode: %s -> %s", self.dist, target)
                self.dist = target

            parts = self.dist.split("/")
            if len(parts) <= 2:
                # Auto-add package name to targets of the form root/subfolder (most typical case)
                self.dist = os.path.join(self.dist, self.pspec.canonical_name)

        self.dist = CFG.resolved_path(self.dist, base=CFG.base)
        with runez.Anchored(self.folder):
            dist_folder = CFG.resolved_path(self.dist)
            exes = VenvPackager.package(self.pspec, dist_folder, self.requirements, self.compile)
            problem = None
            summary = []
            for exe in exes:
                if self.symlink and exe.exists():
                    runez.symlink(exe, self.symlink.target / exe.name)

                msg = f"- {runez.short(exe)}"
                if self.sanity_check:
                    r = runez.run(exe, self.sanity_check, fatal=False)
                    overview = runez.short(runez.first_line(r.full_output) or "-no-output-")
                    msg += f", {self.sanity_check}: {overview}"
                    if r.failed:  # pragma: no cover, --sanity-check is seldom used, will retire entire `package` command in the future
                        problem = problem or overview
                        overview = overview.lower()
                        if "usage:" in overview or "unrecognized" in overview:
                            msg += f" (does not respond to {self.sanity_check})"

                summary.append(msg)

            if not problem and not self.compile:
                clean_compiled_artifacts(dist_folder)

            msg = "Would package" if runez.DRYRUN else "Packaged"
            print(f"\n{msg} {runez.plural(exes, 'executable')}:")
            print(runez.joined(summary, " ", delimiter="\n"))
            runez.abort_if(problem)


class Symlinker:
    def __init__(self, spec):
        self.base, _, self.target = spec.partition(":")
        runez.abort_if(not self.base or not self.target, f"Invalid symlink specification '{spec}'")
        self.target = CFG.resolved_path(self.target, base=CFG.base)


def should_clean(basename):
    return basename == "__pycache__" or basename.endswith((".pyc", ".pyo"))


def clean_compiled_artifacts(folder):
    """Remove usual byte-code compiled artifacts from `folder`"""
    # See https://www.debian.org/doc/packaging-manuals/python-policy/ch-module_packages.html
    deleted = runez.delete(folder / "share" / "python-wheels", fatal=False)
    dirs_to_be_deleted = []
    for root, dirs, files in os.walk(folder):
        for basename in dirs[:]:
            if should_clean(basename):
                dirs.remove(basename)
                dirs_to_be_deleted.append(os.path.join(root, basename))

        for basename in files:
            if should_clean(basename.lower()):
                deleted += runez.delete(os.path.join(root, basename), fatal=False)

    for path in dirs_to_be_deleted:
        deleted += runez.delete(path, fatal=False)

    if deleted:
        LOG.info("Deleted %s compiled artifacts", deleted)
