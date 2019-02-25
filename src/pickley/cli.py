"""
See https://github.com/zsimic/pickley
"""

import logging
import logging.config
import os
import sys

import click
import runez

from pickley import system
from pickley.lock import SoftLockException
from pickley.package import DELIVERERS, PACKAGERS
from pickley.settings import meta_folder
from pickley.system import short
from pickley.uninstall import uninstall_existing


LOG = logging.getLogger(__name__)
AUDITED = ["install", "uninstall"]


@runez.click.group()
@click.pass_context
@runez.click.version(message="%(version)s")
@runez.click.debug()
@runez.click.dryrun("-n")
@click.option("--base", "-b", metavar="PATH", help="Base installation folder to use (default: folder containing pickley)")
@click.option("--index", "-i", metavar="PATH", help="Pypi index to use")
@click.option("--config", "-c", metavar="PATH", help="Extra config to load")
@click.option("--python", "-P", metavar="PATH", help="Python interpreter to use")
@click.option("--delivery", "-d", type=click.Choice(DELIVERERS.names()), help="Delivery method to use")
@click.option("--packager", "-p", help="Packager to use (one of: %s)" % ",".join(PACKAGERS.names()))
def main(ctx, debug, dryrun, base, index, config, python, delivery, packager):
    """
    Package manager for python CLIs
    """
    if dryrun:
        debug = True

    if base:
        base = runez.resolved_path(base)
        if not os.path.exists(base):
            runez.abort("Can't use %s as base: folder does not exist", short(base))
        system.SETTINGS.set_base(base)

    if not dryrun and ctx.invoked_subcommand in AUDITED:
        file_location = system.SETTINGS.meta.full_path("audit.log")

    else:
        file_location = None

    runez.log.setup(
        debug=debug,
        dryrun=dryrun,
        greetings=":: {argv}",
        console_format="%(levelname)s %(message)s" if debug else "%(message)s",
        console_level=logging.INFO,
        console_stream=sys.stdout,
        file_format="%(asctime)s %(timezone)s [%(process)d] %(context)s%(levelname)s - %(message)s",
        file_level=logging.DEBUG,
        file_location=file_location,
        locations=None,
        rotate="size:500k,1",
    )
    runez.log.silence("pip")

    # Disable logging.config, as pip tries to get smart and configure all logging...
    logging.config.dictConfig = lambda x: None

    system.SETTINGS.load_config(config=config, delivery=delivery, index=index, packager=packager)
    system.DESIRED_PYTHON = python


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force check, even if checked recently")
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
@click.argument("packages", nargs=-1, required=False)
def check(force, verbose, packages):
    """
    Check whether specified packages need an upgrade
    """
    code = 0
    packages = system.SETTINGS.resolved_packages(packages) or system.installed_names()
    if not packages:
        LOG.info("No packages installed")

    else:
        for name in packages:
            p = PACKAGERS.resolved(name)
            p.refresh_desired(force=force)
            if not p.desired.valid:
                LOG.error(p.desired.representation(verbose))
                code = 1
            elif not p.current.version or not p.current.valid:
                LOG.info(p.desired.representation(verbose, note="is not installed"))
                code = 1
            elif p.current.version != p.desired.version:
                LOG.info(p.current.representation(verbose, note="can be upgraded to %s" % p.desired.version))
                code = 1
            else:
                LOG.info(p.current.representation(verbose, note="is installed"))

    sys.exit(code)


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
def list(verbose):
    """
    List installed packages
    """
    packages = system.installed_names()
    if not packages:
        LOG.info("No packages installed")

    else:
        for name in packages:
            p = PACKAGERS.resolved(name)
            LOG.info(p.current.representation(verbose))


@main.command()
@click.option("--force", "-f", is_flag=True, help="Force installation, even if already installed")
@click.argument("packages", nargs=-1, required=True)
def install(force, packages):
    """
    Install a package from pypi
    """
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
    packages = system.SETTINGS.resolved_packages(packages)
    errors = 0
    for name in packages:
        p = PACKAGERS.resolved(name)
        if not force and not p.current.file_exists:
            errors += 1
            LOG.error("%s was not installed with pickley", name)
            continue

        eps = p.entry_points
        ep_uninstalled = 0
        ep_missed = 0
        meta_deleted = runez.delete(system.SETTINGS.meta.full_path(name), fatal=False)
        if not eps and force:
            eps = {name: ""}
        if eps and meta_deleted >= 0:
            for entry_point in eps:
                path = system.SETTINGS.base.full_path(entry_point)
                handler = runez.delete if meta_deleted > 0 else uninstall_existing
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
            LOG.info("Nothing to uninstall for %s" % name)
            continue

        message = "Would uninstall" if runez.DRYRUN else "Uninstalled"
        message = "%s %s" % (message, name)
        if ep_uninstalled > 1:
            message += " (%s entry points)" % ep_uninstalled
        LOG.info(message)

    if errors:
        runez.abort()


@main.command()
@click.argument("source", required=True)
@click.argument("destination", required=True)
def copy(source, destination):
    """
    Copy file or folder, relocate venvs accordingly (if any)
    """
    system.copy(source, destination)
    LOG.info("Copied %s -> %s", short(source), short(destination))


@main.command()
@click.argument("source", required=True)
@click.argument("destination", required=True)
def move(source, destination):
    """
    Copy file or folder, relocate venvs accordingly (if any)
    """
    system.move(source, destination)
    LOG.info("Moved %s -> %s", short(source), short(destination))


@main.command()
@click.option("--build", "-b", default="./build", show_default=True, help="Folder to use as build cache")
@click.option("--dist", "-d", default="./dist", show_default=True, help="Folder where to produce package")
@click.option("--symlink", "-s", help="Create symlinks for debian-style packaging, example: root:root/usr/local/bin")
@click.option("--relocatable/--absolute", is_flag=True, default=True, help="Create a relocatable venv or not  [default: relocatable]")
@click.option("--sanity-check", default="--version", show_default=True, help="Args to invoke produced package for sanity check")
@click.argument("folder", required=True)
def package(build, dist, symlink, relocatable, sanity_check, folder):
    """
    Package a project from source checkout
    """
    build = runez.resolved_path(build)
    dist = runez.resolved_path(dist)
    folder = runez.resolved_path(folder)

    system.SETTINGS.meta = meta_folder(build)

    if not os.path.isdir(folder):
        runez.abort("Folder %s does not exist", short(folder))

    setup_py = os.path.join(folder, "setup.py")
    if not os.path.exists(setup_py):
        runez.abort("No setup.py in %s", short(folder))

    with runez.CurrentFolder(folder):
        # Some setup.py's assume their working folder is the folder where they're in
        name = system.run_python(setup_py, "--name", fatal=False, dryrun=False)
        if not name:
            runez.abort("Could not determine package name from %s", short(setup_py))

    runez.Anchored.add(folder)
    p = PACKAGERS.resolved(name)
    p.build_folder = build
    p.dist_folder = dist
    p.relocatable = relocatable
    p.source_folder = folder
    p.package()
    p.create_symlinks(symlink)
    p.sanity_check(sanity_check)
    LOG.info("Packaged %s successfully, produced: %s", short(folder), runez.represented_args(p.executables))
    runez.Anchored.pop(folder)


@main.command()
@click.option("--diagnostics", "-d", is_flag=True, help="Show diagnostics info")
def settings(diagnostics):
    """
    Show settings
    """
    if diagnostics:
        prefix = getattr(sys, "prefix", None)
        real_prefix = getattr(sys, "real_prefix", None)
        LOG.info("python         : %s", short(system.target_python(desired=system.INVOKER, fatal=None), meta=False))
        LOG.info("sys.executable : %s", short(sys.executable, meta=False))
        LOG.info("sys.prefix     : %s", short(prefix, meta=False))
        if real_prefix:
            LOG.info("sys.real_prefix: %s", short(real_prefix, meta=False))
        if not system.SETTINGS.meta.path.startswith(system.PICKLEY_PROGRAM_PATH):
            LOG.info("pickley        : %s" % short(system.PICKLEY_PROGRAM_PATH, meta=False))
        LOG.info("meta           : %s" % short(system.SETTINGS.meta.path, meta=False))
        LOG.info("")

    LOG.info(system.SETTINGS.represented())


@main.command(name="auto-upgrade")
@click.option("--force", "-f", is_flag=True, help="Force auto-upgrade check, even if recently checked")
@click.argument("package", required=True)
def auto_upgrade(force, package):
    """
    Auto-upgrade a package
    """
    p = PACKAGERS.resolved(package)
    if not p.current.valid:
        runez.abort("%s is not currently installed", package)

    ping = system.SETTINGS.meta.full_path(package, ".ping")
    if not force and runez.is_younger(ping, system.SETTINGS.version_check_seconds):
        # We checked for auto-upgrade recently, no need to check again yet
        runez.abort("Skipping auto-upgrade, checked recently", code=0)
    runez.touch(ping)

    try:
        p.internal_install()

    except SoftLockException:
        runez.abort("Skipping auto-upgrade, %s is currently being installed by another process" % package, code=0)
