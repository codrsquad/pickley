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
from runez.pyenv import PypiStd, Version
from runez.render import PrettyTable

from pickley import (
    __version__,
    abort,
    bstrap,
    CFG,
    despecced,
    inform,
    PackageSpec,
    parsed_version,
    ResolvedPackage,
    specced,
    TrackedManifest,
    TrackedSettings,
)
from pickley.package import VenvPackager

LOG = logging.getLogger(__name__)


class Requirements(NamedTuple):
    requirement_files: Sequence[Path]
    additional_packages: Optional[Sequence[str]]
    project: Path


def setup_audit_log():
    """Setup audit.log log file handler"""
    if runez.DRYRUN:
        if not runez.log.console_handler or runez.log.spec.console_level > logging.INFO:
            runez.log.setup(console_level=logging.INFO)

        return

    if not runez.log.file_handler:
        if runez.color.is_coloring():
            runez.log.progress.start(message_color=runez.dim, spinner_color=runez.bold)

        log_path = CFG.meta / "audit.log"
        runez.log.trace("Logging to %s", log_path)
        runez.ensure_folder(CFG.meta)
        runez.log.setup(
            default_logger=LOG.debug,
            file_format="%(asctime)s %(timezone)s [%(process)s] %(context)s%(levelname)s - %(message)s",
            file_level=logging.DEBUG,
            file_location=str(log_path),
            greetings=":: {argv}",
            rotate="size:500k",
            rotate_count=1,
        )


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
            if runez.DRYRUN:
                print(f"Would acquire {runez.short(self.lock_path)}")

            else:
                runez.log.trace(f"Acquired {runez.short(self.lock_path)}")

            runez.write(self.lock_path, runez.joined(os.getpid(), runez.quoted(sys.argv[1:]), delimiter="\n"), logger=False)

        return self

    def __exit__(self, *_):
        """Release lock"""
        if self.lock_path:
            if runez.DRYRUN:
                print(f"Would release {runez.short(self.lock_path)}")

            else:
                runez.log.trace(f"Released {runez.short(self.lock_path)}")

            runez.delete(self.lock_path, logger=False)


def perform_install(pspec, is_upgrade=False, quiet=False, verb=None):
    """
    Args:
        pspec (PackageSpec): Package spec to install
        is_upgrade (bool): If True, intent is an upgrade (not a new install)
        quiet (bool): If True, don't chatter
        verb (str): Verb to use to convey what kind of installation is being done (ex: auto-heal)

    Returns:
        (pickley.TrackedManifest): Manifest is successfully installed (or was already up-to-date)
    """
    if not verb:
        verb = "upgrade" if is_upgrade else "install"

    if pspec.resolved_info.problem:
        abort(f"Can't {verb} {pspec}: {runez.red(pspec.resolved_info.problem)}")

    with SoftLock(pspec.canonical_name):
        started = time.time()
        skip_reason = pspec.skip_reason()
        if skip_reason:
            inform(f"Skipping installation of {pspec}: {runez.bold(skip_reason)}")
            return

        if is_upgrade and not pspec.currently_installed_version and not quiet:
            abort(f"'{runez.red(pspec)}' is not installed")

        if CFG.version_check_delay and pspec.is_up_to_date:
            if not quiet:
                status = "up-to-date" if is_upgrade else "installed"
                inform(f"{pspec.canonical_name} v{runez.bold(pspec.currently_installed_version)} is already {status}")

            pspec.groom_installation()
            return

        setup_audit_log()
        manifest = VenvPackager.install(pspec)
        if manifest and not quiet:
            note = f" in {runez.represented_duration(time.time() - started)}"
            verb += "d" if verb.endswith("e") else "ed"
            action = "%s%s" % (verb[0].upper(), verb[1:])
            if runez.DRYRUN:
                action = f"Would state: {action}"

            inform(f"{action} {pspec.canonical_name} v{runez.bold(pspec.target_version)}{runez.dim(note)}")

        pspec.groom_installation()


def _find_base_from_program_path(path: Path):
    if not path or len(path.parts) <= 1:
        return None

    if path.name in (bstrap.DOT_META, ".pickley"):
        return path.parent  # We're running from an installed pickley

    if path.name == ".venv":
        return path / "root"  # Convenience for development

    return _find_base_from_program_path(path.parent)


def find_base(path=None):
    base_path = CFG.resolved_path(os.environ.get("PICKLEY_ROOT"))
    if base_path:
        if not base_path.is_dir():
            abort(f"PICKLEY_ROOT points to non-existing directory {runez.red(base_path)}")

        return base_path

    path = CFG.resolved_path(path or sys.argv[0])
    return _find_base_from_program_path(path) or path.parent


def find_symbolic_invoker() -> str:
    """Symbolic major/minor symlink to invoker, when applicable"""
    invoker = runez.SYS_INFO.invoker_python
    folder = invoker.real_exe.parent.parent
    v = Version.extracted_from_text(folder.name)
    if v and v.given_components_count == 3:
        # For setups that provide a <folder>/pythonM.m -> <folder>/pythonM.m.p symlink, prefer the major/minor variant
        candidates = [folder.parent / folder.name.replace(v.text, v.mm), folder.parent / f"python{v.mm}"]
        for path in candidates:
            if path.exists():
                return path

    return invoker.executable  # pragma: no cover


@runez.click.group()
@click.pass_context
@runez.click.version(message="%(version)s", version=__version__)
@click.option("--verbose", "-v", "--debug", count=True, default=0, help="Show verbose output")
@runez.click.dryrun("-n")
@runez.click.color()
@click.option("--config", "-c", metavar="PATH", help="Optional additional configuration to use")
@click.option("--index", "-i", metavar="PATH", help="Pypi index to use")
@click.option("--python", "-P", metavar="PATH", help="Python interpreter to use")
@click.option("--delivery", "-d", help="Delivery method to use")
@click.option("--package-manager", type=click.Choice(("uv", "pip")), help="What to use to create venvs? (default: uv)")
def main(ctx, verbose, config, index, python, delivery, package_manager):
    """Install python CLIs that keeps themselves up-to-date"""
    runez.system.AbortException = SystemExit
    level = logging.WARNING
    if ctx.invoked_subcommand == "package":
        # Default to using invoker for 'package' subcommand
        level = logging.INFO
        package_manager = package_manager or "pip"  # Default to pip for 'package' subcommand
        python = python or find_symbolic_invoker()

    if verbose > 1:
        os.environ["TRACE_DEBUG"] = "1"

    runez.log.setup(
        debug=verbose or os.environ.get("PICKLEY_TRACE"),
        default_logger=LOG.debug,
        console_format="%(levelname)s %(message)s" if verbose else "%(message)s",
        console_level=level,
        console_stream=sys.stderr,
        locations=None,
        trace="TRACE_DEBUG+:: ",
    )
    bstrap.clean_env_vars()
    if runez.SYS_INFO.platform_id.is_macos and "ARCHFLAGS" not in os.environ and runez.SYS_INFO.platform_id.arch:
        # Ensure the proper platform is used on macos
        archflags = f"-arch {runez.SYS_INFO.platform_id.arch}"
        runez.log.trace(f"Setting ARCHFLAGS={archflags}")
        os.environ["ARCHFLAGS"] = archflags

    CFG.set_cli(config, delivery, index, python, package_manager)
    if ctx.invoked_subcommand != "package":
        CFG.set_base(find_base())

    runez.Anchored.add(CFG.base)
    bstrap.set_mirror_env_vars(CFG.index)


@main.command()
def auto_heal():
    """
    Automatically re-install packages that have stopped working.

    \b
    Reasons a package wouldn't be "healthy" anymore:
    - Base python used to install the packages' venv is not available anymore
    - The pickley-generated wrapper points to files that have now been deleted
    """
    total = healed = 0
    for spec in CFG.installed_specs():
        total += 1
        if spec.is_healthily_installed:
            print("%s is healthy" % runez.bold(spec))
            continue

        healed += 1
        perform_install(spec, verb="auto-heal")

    print("Auto-healed %s / %s packages" % (healed, total))


@main.command()
@click.option("--force", is_flag=True, help="Force auto-upgrade check, even if recently checked")
@click.argument("package", required=True)
def auto_upgrade(force, package):
    """Background auto-upgrade command (called by wrapper)"""
    if force:
        CFG.version_check_delay = 0

    canonical_name = PypiStd.std_package_name(package)
    runez.abort_if(not canonical_name, f"Invalid package name '{package}'")
    cache_path = CFG.resolution_cache_path(canonical_name)
    if CFG.version_check_delay and runez.file.is_younger(cache_path, CFG.version_check_delay):
        LOG.debug("Skipping auto-upgrade, checked recently")
        sys.exit(0)

    lock_path = CFG.soft_lock_path(canonical_name)
    if lock_path and runez.file.is_younger(lock_path, CFG.install_timeout(canonical_name)):
        LOG.debug("Lock file present, another installation is in progress")
        sys.exit(0)

    manifest = TrackedManifest.from_file(CFG.manifest_path(canonical_name))
    runez.abort_if(not manifest, f"{canonical_name} not installed with pickley")
    pspec = PackageSpec(manifest.settings.auto_upgrade_spec)
    perform_install(pspec, is_upgrade=True, quiet=True)


@main.command()
@click.argument("what", required=False)
def base(what):
    """Show pickley base folder"""
    if what == "bootstrap-own-wrapper":
        # Internal: called by bootstrap script
        pspec = PackageSpec(f"{bstrap.PICKLEY}=={__version__}")
        delivery = pspec.delivery_method
        delivery.install(pspec)

        if bstrap.USE_UV:
            tmp_uv = CFG.meta / ".uv"
            uv_spec = PackageSpec("uv")
            if runez.is_executable(tmp_uv / "bin/uv"):
                # Use the .uv/bin/uv obtained during bootstrap
                runez.move(tmp_uv, uv_spec.target_installation_folder, overwrite=True)
                delivery = pspec.delivery_method
                delivery.install(uv_spec)

            elif not uv_spec.is_healthily_installed:
                perform_install(uv_spec)

            runez.delete(tmp_uv)

        return

    path = CFG.base
    if what:
        paths = {
            "audit": CFG.meta / "audit.log",
            "cache": CFG.cache,
            "config": CFG.meta / "config.json",
            "meta": CFG.meta,
        }
        paths["audit.log"] = paths["audit"]
        paths["config.json"] = paths["config"]
        path = paths.get(what)
        if not path:
            options = [runez.green(s) for s in sorted(paths)]
            abort(f"Unknown base folder reference '{runez.red(what)}', try one of: {', '.join(options)}")

    print(path)


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force check, even if checked recently")
@click.argument("packages", nargs=-1, required=False)
def check(force, packages):
    """Check whether specified packages need an upgrade"""
    if force:
        CFG.version_check_delay = 0

    code = 0
    packages = CFG.package_specs(packages, canonical_only=False)
    if not packages:
        print("No packages installed")
        sys.exit(0)

    for pspec in packages:
        skip_reason = pspec.skip_reason()
        if skip_reason:
            print(f"{pspec}: {runez.bold('skipped')}, {runez.dim(skip_reason)}]")
            continue

        dv = pspec.target_version
        if pspec.resolved_info.problem:
            msg = pspec.resolved_info.problem
            code = 1

        elif not pspec.currently_installed_version:
            msg = f"{runez.bold(dv)} not installed"
            code = 1

        elif pspec.is_up_to_date:
            msg = f"{dv} up-to-date"

        else:
            msg = f"currently {pspec.currently_installed_version}"
            if not pspec.is_healthily_installed:
                msg += runez.red(" unhealthy")

            msg = f"{runez.bold(dv)} ({msg})"

        print(f"{pspec}: {msg}")

    sys.exit(code)


@main.command()
def config():
    """Show current configuration"""
    print(CFG.represented())


@main.command()
@click.argument("packages", nargs=-1, required=True)
def describe(packages):
    """Show current configuration"""
    problems = 0
    for package_spec in packages:
        settings = TrackedSettings.from_config(package_spec)
        info = ResolvedPackage()
        info.logger = LOG.debug
        info.resolve(settings)
        text = f"{runez.bold(info)}"
        if info.canonical_name and info.canonical_name != info.given_package_spec:
            text += f": {runez.bold(info.canonical_name)}"

        if info.version:
            text += f" version {runez.bold(info.version)}"

        print(text)
        if info.pip_spec:
            text = runez.bold(runez.joined(info.pip_spec))
            if info.resolution_reason:
                text += runez.dim(f" ({info.resolution_reason})")

            print(f"  pip spec: {text}")

        if info.problem:
            problems += 1
            print(f"  problem: {runez.red(info.problem)}")

        else:
            ep = runez.joined(info.entrypoints, delimiter=", ") or runez.brown("-no entry points-")
            print(f"  entry points: {runez.bold(ep)}")

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
    if force:
        CFG.version_check_delay = 0

    setup_audit_log()
    specs = CFG.package_specs(packages, canonical_only=False, include_pickley=packages and packages[0].startswith("bundle:"))
    for pspec in specs:
        perform_install(pspec)


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


@main.command(name="list")
@runez.click.border("-b", default="github")
@click.option("--format", "-f", type=click.Choice(["csv", "json", "tsv", "yaml"]), help="Representation format")
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
def cmd_list(border, format, verbose):
    """List installed packages"""
    packages = CFG.package_specs(include_pickley=verbose)
    if not packages:
        print("No packages installed")
        sys.exit(0)

    report = TabularReport("Package,Version,Python", additional="Delivery,PackageManager", border=border, verbose=verbose)
    for pspec in packages:
        manifest = pspec.manifest
        python = manifest and manifest.settings.python and CFG.available_pythons.find_python(manifest.settings.python)
        report.add_row(
            Package=pspec.canonical_name,
            Version=manifest and manifest.version,
            Python=python,
            Delivery=manifest and manifest.settings.delivery,
            PackageManager=manifest and manifest.settings.package_manager,
        )

    print(report.represented(format))


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
        return specced(self.package, self.pinned)

    @classmethod
    def from_cli(cls, command):
        package = command
        if ":" in command:
            # Allows to support cases where command name is different from pypi package name, ex: awscli:aws
            package, _, command = command.rpartition(":")

        package, pinned_package = despecced(package)
        command, pinned_command = despecced(command)
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
        pspec = PackageSpec(rs.specced)
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
    if command == "--help":
        click.echo(click.get_current_context().get_help())
        return

    RunSetup.perform_run(command, args)


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
        perform_install(pspec, is_upgrade=True)


@main.command()
@click.option("--all", is_flag=True, help="Uninstall everything pickley-installed, including pickley itself")
@click.argument("packages", nargs=-1, required=False)
def uninstall(all, packages):
    """Uninstall packages"""
    if packages and all:
        abort("Either specify packages to uninstall, or --all (but not both)")

    if not packages and not all:
        abort("Specify packages to uninstall, or --all")

    if packages and bstrap.PICKLEY in packages:
        abort("Run 'uninstall --all' if you wish to uninstall pickley itself (and everything it installed)")

    setup_audit_log()
    for pspec in CFG.package_specs(packages):
        if not pspec.currently_installed_version:
            abort(f"{runez.bold(pspec.canonical_name)} was not installed with pickley")

        for ep in pspec.manifest.entrypoints:
            runez.delete(CFG.base / ep)

        pspec.delete_all_files()
        action = "Would uninstall" if runez.DRYRUN else "Uninstalled"
        inform(f"{action} {pspec}")

    if all:
        runez.delete(CFG.base / bstrap.PICKLEY)
        runez.delete(CFG.meta)
        inform(f"pickley is now {runez.red('uninstalled')}")


@main.command()
@click.option("--system", is_flag=True, help="Look at system PATH (not just pickley installs)")
@click.argument("programs", nargs=-1)
def version_check(system, programs):
    """Check that programs are present with a minimum version"""
    if not programs:
        runez.abort("Specify at least one program to check")

    specs = []
    for program_spec in programs:
        program, _, min_version = program_spec.partition(":")
        min_version = Version(min_version)
        if not program or not min_version.is_valid:
            runez.abort(f"Invalid argument '{program_spec}', expecting format <program>:<version>")

        specs.append((program, min_version))

    overview = []
    for program, min_version in specs:
        if system:
            full_path = runez.which(program)
            runez.abort_if(not full_path, f"{program} is not installed")

        else:
            full_path = CFG.base / program

        r = runez.run(full_path, "--version", logger=print if runez.DRYRUN else LOG.debug)
        if not runez.DRYRUN:
            version = parsed_version(r.output or r.error)
            if not version or version < min_version:
                runez.abort(f"{runez.short(full_path)} version too low: {version} (need {min_version}+)")

            overview.append(f"{program} {version}")

    if overview:
        print(runez.short(runez.joined(overview, delimiter=" ; ")))


@main.command()
@click.option("--base", "-b", default=".", show_default=True, help="Folder to use as base folder")
@click.option("--dist", "-d", default="./dist", show_default=True, help="Folder where to produce package")
@click.option("--symlink", "-s", help="Create symlinks for debian-style packaging, example: root:root/usr/local/bin")
@click.option("--no-compile", is_flag=True, help="Don't byte-compile packaged venv")
@click.option("--sanity-check", default=None, show_default=True, help="Args to invoke produced package as a sanity check")
@click.option("--requirement", "-r", multiple=True, help="Install from the given requirements file (can be used multiple times)")
@click.argument("project", required=True)
@click.argument("additional", nargs=-1)
def package(base, dist, symlink, no_compile, sanity_check, project, requirement, additional):
    """Package a project from source checkout"""
    started = time.time()
    CFG.set_base(base)
    project = CFG.resolved_path(project)
    runez.log.spec.default_logger = LOG.info
    with runez.CurrentFolder(project, anchor=True):
        finalizer = PackageFinalizer(project, dist, symlink, requirement, additional)
        finalizer.sanity_check = sanity_check
        finalizer.compile = not no_compile
        finalizer.resolve()
        report = finalizer.finalize()
        if report:
            inform("")
            inform(report)
            inform("")

        elapsed = f"in {runez.represented_duration(time.time() - started)}"
        inform(f"Packaged {runez.bold(runez.short(project))} successfully {runez.dim(elapsed)}")


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

    @staticmethod
    def validate_sanity_check(exe, sanity_check):
        if not exe or not sanity_check:
            return None

        r = runez.run(exe, sanity_check, fatal=False)
        if r.failed:
            if does_not_implement_cli_flag(r.output, r.error):
                return f"does not respond to {sanity_check}"

            abort(f"'{exe}' failed {sanity_check} sanity check: {r.full_output}")

        return runez.first_line(r.output or r.error)

    def resolve(self):
        if not self.folder.is_dir():
            abort(f"Folder {runez.red(runez.short(self.folder))} does not exist")

        self.pspec = PackageSpec(str(self.folder))
        if not self.pspec.canonical_name:
            runez.abort(f"Could not determine package name: {self.pspec.resolved_info.problem}")

        LOG.info("Using python: %s", self.pspec.settings.python)
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

    def finalize(self):
        """Run sanity check and/or symlinks, and return a report"""
        with runez.Anchored(self.folder):
            runez.ensure_folder(CFG.base, clean=True, logger=False)
            dist_folder = CFG.resolved_path(self.dist)
            exes = VenvPackager.package(self.pspec, dist_folder, self.requirements, self.compile)
            if exes:
                report = PrettyTable(["Packaged executable", self.sanity_check], border=self.border)
                report.header.style = "bold"
                if not self.sanity_check:
                    report.header[1].shown = False

                for exe in exes:
                    exe_info = self.validate_sanity_check(exe, self.sanity_check)
                    report.add_row(runez.quoted(str(exe)), exe_info)
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


class Symlinker:
    def __init__(self, spec):
        self.base, _, self.target = spec.partition(":")
        if not self.base or not self.target:
            abort(f"Invalid symlink specification '{spec}'")

        self.target = CFG.resolved_path(self.target, base=CFG.base)

    def apply(self, exe: Path):
        dest = self.target / exe.name
        if exe.exists():
            r = runez.symlink(exe, dest, must_exist=False)
            if r > 0:
                inform(f"Symlinked {runez.short(dest)} -> {runez.short(exe)}")

        else:
            LOG.debug("'%s' does not exist, skipping symlink", exe)


def delete_file(path):
    if runez.delete(path, fatal=False, logger=False) > 0:
        return 1

    return 0


def should_clean(basename):
    return basename == "__pycache__" or basename.endswith((".pyc", ".pyo"))


def clean_compiled_artifacts(folder):
    """Remove usual byte-code compiled artifacts from `folder`"""
    # See https://www.debian.org/doc/packaging-manuals/python-policy/ch-module_packages.html
    deleted = delete_file(folder / "share" / "python-wheels")
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
