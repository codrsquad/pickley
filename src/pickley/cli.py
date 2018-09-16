"""
See https://github.com/zsimic/pickley
"""

import logging
import logging.config
import os
import sys

import click

from pickley import system
from pickley.context import CurrentFolder
from pickley.lock import SoftLockException
from pickley.package import DELIVERERS, PACKAGERS
from pickley.settings import meta_folder
from pickley.system import short
from pickley.uninstall import uninstall_existing


def bootstrap(testing=False):
    """
    Bootstrap pickley: re-install it as venv if need be

    Packaged as a pex, pickley is easy to distribute,
    however there are some edge cases where running pip from a pex-packaged CLI doesn't work very well
    So, first thing we do is re-package ourselves as a venv on the target machine
    """
    if not testing and (system.State.quiet or getattr(sys, "real_prefix", None)):
        # Don't bootstrap in quiet mode, or if we're running from a venv already
        return

    delivery = DELIVERERS.resolved_name(system.PICKLEY)
    if delivery != "wrap":
        # Only bootstrap if we're using wrapper, no point otherwise
        return

    p = PACKAGERS.resolved(system.PICKLEY)
    if p.registered_name != system.VENV_PACKAGER:
        # Also no real point bootstrapping unless target packager is venv
        return

    p.refresh_current()
    if p.current.packager == system.VENV_PACKAGER and p.current.delivery == delivery:
        # We're already packaged correctly, no need to bootstrap
        return

    try:
        # Re-install ourselves with correct packager
        p.internal_install(bootstrap=True)
        system.relaunch()

    except SoftLockException:
        return


@click.group(context_settings=dict(help_option_names=["-h", "--help"], max_content_width=140), epilog=__doc__)
@click.version_option()
@click.option("--debug", is_flag=True, help="Show debug logs")
@click.option("--quiet", "-q", is_flag=True, help="Quiet mode, do not output anything")
@click.option("--dryrun", "-n", is_flag=True, help="Perform a dryrun")
@click.option("--base", "-b", metavar="PATH", help="Base installation folder to use (default: folder containing pickley)")
@click.option("--index", "-i", metavar="PATH", help="Pypi index to use")
@click.option("--config", "-c", metavar="PATH", help="Extra config to load")
@click.option("--python", "-P", metavar="PATH", help="Python interpreter to use")
@click.option("--delivery", "-d", type=click.Choice(DELIVERERS.names()), help="Delivery method to use")
@click.option("--packager", "-p", type=click.Choice(PACKAGERS.names()), help="Packager to use")
def main(debug, quiet, dryrun, base, index, config, python, delivery, packager):
    """
    Package manager for python CLIs
    """
    if dryrun:
        debug = True
    if debug:
        quiet = False
    system.DRYRUN = bool(dryrun)
    system.State.quiet = bool(quiet)

    if base:
        base = system.resolved_path(base)
        if not os.path.exists(base):
            system.abort("Can't use %s as base: folder does not exist", short(base))
        system.SETTINGS.set_base(base)

    # Disable logging.config, as pip tries to get smart and configure all logging...
    logging.config.dictConfig = lambda x: None
    logging.getLogger("pip").setLevel(logging.WARNING)
    logging.root.setLevel(logging.INFO if quiet else logging.DEBUG)
    if debug:
        # Log to console with --debug or --dryrun
        system.setup_debug_log()

    system.SETTINGS.load_config(config=config, delivery=delivery, index=index, packager=packager)
    system.DESIRED_PYTHON = python


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
@click.argument("packages", nargs=-1, required=False)
def check(verbose, packages):
    """
    Check whether specified packages need an upgrade
    """
    code = 0
    packages = system.SETTINGS.resolved_packages(packages) or system.installed_names()
    if not packages:
        system.info("No packages installed")

    else:
        for name in packages:
            p = PACKAGERS.resolved(name)
            p.refresh_current()
            p.refresh_desired()
            if not p.desired.valid:
                system.error(p.desired.representation(verbose))
                code = 1
            elif not p.current.version or not p.current.valid:
                system.info(p.desired.representation(verbose, note="is not installed"))
                code = 1
            elif p.current.version != p.desired.version:
                system.info(p.current.representation(verbose, note="can be upgraded to %s" % p.desired.version))
                code = 1
            else:
                system.info(p.current.representation(verbose, note="is installed"))

    sys.exit(code)


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
def list(verbose):
    """
    List installed packages
    """
    packages = system.installed_names()
    if not packages:
        system.info("No packages installed")

    else:
        for name in packages:
            p = PACKAGERS.resolved(name)
            p.refresh_current()
            system.info(p.current.representation(verbose))


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force installation, even if already installed")
@click.argument("packages", nargs=-1, required=True)
def install(force, packages):
    """
    Install a package from pypi
    """
    system.setup_audit_log()
    bootstrap()

    packages = system.SETTINGS.resolved_packages(packages)
    for name in packages:
        p = PACKAGERS.resolved(name)
        p.install(force=force)


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force installation, even if already installed")
@click.argument("packages", nargs=-1, required=True)
def uninstall(force, packages):
    """
    Uninstall packages
    """
    system.setup_audit_log()
    packages = system.SETTINGS.resolved_packages(packages)
    errors = 0
    for name in packages:
        p = PACKAGERS.resolved(name)
        p.refresh_current()
        if not force and not p.current.file_exists:
            errors += 1
            system.error("%s was not installed with pickley", name)
            continue

        eps = p.entry_points
        ep_uninstalled = 0
        ep_missed = 0
        meta_deleted = system.delete_file(system.SETTINGS.meta.full_path(name), fatal=False)
        if not eps and force:
            eps = [name]
        if eps and meta_deleted >= 0:
            for entry_point in eps:
                path = system.SETTINGS.base.full_path(entry_point)
                handler = system.delete_file if meta_deleted > 0 else uninstall_existing
                r = handler(path, fatal=False)
                if r < 0:
                    ep_missed += 1
                elif r > 0:
                    ep_uninstalled += 1

        if ep_missed or meta_deleted < 0:
            # Error was already reported
            errors += 1
            continue

        if ep_uninstalled + meta_deleted == 0:
            system.info("Nothing to uninstall for %s" % name)
            continue

        message = "Would uninstall" if system.DRYRUN else "Uninstalled"
        message = "%s %s" % (message, name)
        if ep_uninstalled > 1:
            message += " (%s entry points)" % ep_uninstalled
        system.info(message)

    if errors:
        system.abort()


@main.command()
@click.argument("source", required=True)
@click.argument("destination", required=True)
def copy(source, destination):
    """
    Copy file or folder, relocate venvs accordingly (if any)
    """
    system.setup_audit_log()
    system.copy_file(source, destination)
    system.info("Copied %s -> %s", short(source), short(destination))


@main.command()
@click.argument("source", required=True)
@click.argument("destination", required=True)
def move(source, destination):
    """
    Copy file or folder, relocate venvs accordingly (if any)
    """
    system.setup_audit_log()
    system.move_file(source, destination)
    system.info("Moved %s -> %s", short(source), short(destination))


@main.command()
@click.option("--dist", "-d", default="./dist", show_default=True, help="Folder where to produce package")
@click.option("--build", "-b", default="./build", show_default=True, help="Folder to use as build cache")
@click.argument("folder", required=True)
def package(dist, build, folder):
    """
    Package a project from source checkout
    """
    dist = system.resolved_path(dist)
    build = system.resolved_path(build)
    folder = system.resolved_path(folder)

    system.SETTINGS.meta = meta_folder(build)
    system.setup_audit_log()
    bootstrap()

    if not os.path.isdir(folder):
        system.abort("Folder %s does not exist", short(folder))

    setup_py = os.path.join(folder, "setup.py")
    if not os.path.exists(setup_py):
        system.abort("No setup.py in %s", short(folder))

    with CurrentFolder(folder):
        # Some setup.py's assume their working folder is the folder where they're in
        name = system.run_python(setup_py, "--name", fatal=False)
        if not name:
            system.abort("Could not determine package name from %s", short(setup_py))

    p = PACKAGERS.resolved(name)
    p.dist_folder = system.resolved_path(dist)
    p.build_folder = system.resolved_path(build)
    p.source_folder = system.resolved_path(folder)
    r = p.package()
    system.info("Packaged %s successfully, produced: %s", short(folder), system.represented_args(r, shorten=folder))


@main.command()
@click.option("--diagnostics", "-d", is_flag=True, help="Show diagnostics info")
def settings(diagnostics):
    """
    Show settings
    """
    if diagnostics:
        prefix = getattr(sys, "prefix", None)
        real_prefix = getattr(sys, "real_prefix", None)
        system.info("python         : %s", short(system.target_python(fatal=False), meta=False))
        system.info("sys.executable : %s", short(sys.executable, meta=False))
        system.info("sys.prefix     : %s", short(prefix, meta=False))
        if real_prefix:
            system.info("sys.real_prefix: %s", short(real_prefix, meta=False))
        if not system.SETTINGS.meta.path.startswith(system.PICKLEY_PROGRAM_PATH):
            system.info("pickley        : %s" % short(system.PICKLEY_PROGRAM_PATH, meta=False))
        system.info("meta           : %s" % short(system.SETTINGS.meta.path, meta=False))
        system.info("")

    system.info(system.SETTINGS.represented())


@main.command(name="auto-upgrade")
@click.argument("package", required=True)
def auto_upgrade(package):
    """
    Auto-upgrade a package
    """
    p = PACKAGERS.resolved(package)
    p.refresh_current()
    if not p.current.valid:
        system.abort("%s is not currently installed", package)

    ping = system.SETTINGS.meta.full_path(package, ".ping")
    if system.file_younger(ping, system.SETTINGS.version_check_seconds):
        # We checked for auto-upgrade recently, no need to check again yet
        system.abort("Skipping auto-upgrade, checked recently", code=0)
    system.touch(ping)

    try:
        p.internal_install()

    except SoftLockException:
        system.abort("Skipping auto-upgrade, %s is currently being installed by another process" % package, code=0)
