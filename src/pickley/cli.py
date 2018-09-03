"""
See https://github.com/zsimic/pickley
"""

import logging
import logging.config
import os
import sys
from logging.handlers import RotatingFileHandler

import click

from pickley import cd, short, system
from pickley.package import Packager, PACKAGERS, VenvPackager
from pickley.settings import meta_folder, SETTINGS


def _option(func, *args, **kwargs):
    """
    :param function func: Function defining this option
    :param list *args: Optional extra short flag name
    :param dict **kwargs: Optional attr overrides provided by caller
    :return function: Click decorator
    """

    def decorator(f):
        name = kwargs.pop("name", func.__name__)
        kwargs.setdefault("help", func.__doc__)
        kwargs.setdefault("required", False)
        if not kwargs.get("is_flag"):
            kwargs.setdefault("show_default", True)
        if not name.startswith("-"):
            name = "--%s" % name
        return click.option(name, *args, **kwargs)(f)

    return decorator


def packager_option(**kwargs):
    """Packager to use"""

    def _callback(ctx, param, value):
        return PACKAGERS.get(value)

    return _option(packager_option, "-p", name="packager", type=click.Choice(PACKAGERS.names()), callback=_callback, **kwargs)


def get_packager(name, packager=None):
    """
    :param str name: Name of pypi package
    :param Packager|None packager:
    :return Packager: Packager to use
    """
    pkg = packager
    if not pkg:
        definition = PACKAGERS.resolved(name)
        if not definition:
            system.abort("No packager configured for %s" % name)
        pkg = PACKAGERS.get(definition.value)
        if not pkg:
            system.abort("Unknown packager '%s'" % definition)
    if issubclass(pkg, Packager):
        return pkg(name)
    system.abort("Invalid packager implementation for '%s': %s", name, pkg.__class__.__name__)


def setup_audit_log():
    """Log to <base>/audit.log"""
    if system.DRYRUN or system.AUDIT_HANDLER:
        return
    path = SETTINGS.meta.full_path("audit.log")
    system.ensure_folder(path)
    system.AUDIT_HANDLER = RotatingFileHandler(path, maxBytes=500 * 1024, backupCount=1)
    system.AUDIT_HANDLER.setLevel(logging.DEBUG)
    system.AUDIT_HANDLER.setFormatter(logging.Formatter("%(asctime)s [%(process)s] %(levelname)s - %(message)s"))
    logging.root.addHandler(system.AUDIT_HANDLER)
    system.info(":: %s", system.represented_args(sys.argv), output=False)


def setup_debug_log():
    """Log to stderr"""
    if system.DEBUG_HANDLER:
        return
    system.OUTPUT = False
    system.DEBUG_HANDLER = logging.StreamHandler()
    system.DEBUG_HANDLER.setLevel(logging.DEBUG)
    system.DEBUG_HANDLER.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logging.root.addHandler(system.DEBUG_HANDLER)
    logging.root.setLevel(logging.DEBUG)


def relaunch():
    """
    Rerun with same args, to pick up freshly bootstrapped installation
    """
    system.OUTPUT = False
    system.run_program(*sys.argv, stdout=sys.stdout, stderr=sys.stderr)
    if not system.DRYRUN:
        sys.exit(0)


def bootstrap(testing=False):
    """
    Bootstrap pickley: re-install it as venv if need be

    Packaged as a pex, pickley is easy to distribute,
    however there are some edge cases where running pip from a pex-packaged CLI doesn't work very well
    So, first thing we do is re-package ourselves as a venv on the target machine
    """
    if not testing and (system.QUIET or getattr(sys, "real_prefix", None)):
        # Don't bootstrap in quiet mode, or if we're running from a venv already
        return

    p = VenvPackager(system.PICKLEY)
    p.refresh_current()
    if p.current.packager == p.implementation_name:
        # We're already packaged correctly, no need to bootstrap
        return
    p.refresh_desired()
    if not p.desired.valid:
        system.abort("Can't bootstrap %s: %s", p.name, p.desired.problem)

    # Re-install ourselves with correct packager
    system.debug("Bootstrapping %s with %s", system.PICKLEY, p.implementation_name)
    p.install(bootstrap=True)
    relaunch()


@click.group(context_settings=dict(help_option_names=["-h", "--help"], max_content_width=160), epilog=__doc__)
@click.version_option()
@click.option("--debug", is_flag=True, help="Show debug logs")
@click.option("--quiet", "-q", is_flag=True, help="Quiet mode, do not output anything")
@click.option("--dryrun", "-n", is_flag=True, help="Perform a dryrun")
@click.option("--base", "-b", metavar="PATH", help="Base installation folder to use (default: folder containing pickley)")
@click.option("--config", "-c", metavar="KEY=VALUE", multiple=True, help="Override configuration")
def main(debug, quiet, dryrun, base, config):
    """
    Package manager for python CLIs
    """
    if dryrun:
        debug = True
    if debug:
        quiet = False
    system.DRYRUN = dryrun
    system.QUIET = quiet

    if base:
        base = system.resolved_path(base)
        if not os.path.exists(base):
            system.abort("Can't use %s as base: folder does not exist", short(base))
        SETTINGS.set_base(base)

    SETTINGS.set_cli_config(config)
    SETTINGS.add(system.config_paths(system.TESTING))

    # Disable logging.config, as pip tries to get smart and configure all logging...
    logging.config.dictConfig = lambda x: None
    logging.getLogger("pip").setLevel(logging.WARNING)

    logging.root.setLevel(logging.INFO if quiet else logging.DEBUG)

    if debug:
        # Log to console with --debug or --dryrun
        setup_debug_log()


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show more information")
@click.argument("packages", nargs=-1, required=False)
def check(verbose, packages):
    """
    Check whether specified packages need an upgrade
    """
    setup_audit_log()
    code = 0
    packages = SETTINGS.resolved_packages(packages) or SETTINGS.current_names()
    if not packages:
        system.info("No packages installed")

    else:
        for name in packages:
            p = get_packager(name)
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
    setup_audit_log()
    packages = SETTINGS.current_names()
    if not packages:
        system.info("No packages installed")

    else:
        for name in packages:
            p = get_packager(name)
            p.refresh_current()
            system.info(p.current.representation(verbose))


@main.command()
@packager_option()
@click.option("--force", "-f", is_flag=True, help="Force installation, even if already installed")
@click.argument("packages", nargs=-1, required=True)
def install(packager, force, packages):
    """
    Install a package from pypi
    """
    setup_audit_log()
    bootstrap()

    packages = SETTINGS.resolved_packages(packages)
    for name in packages:
        p = get_packager(name, packager)
        p.install(force=force)

    sys.exit(0)


@main.command()
@click.option("--dist", "-d", default="./dist", show_default=True, help="Folder where to produce package")
@click.option("--build", "-b", default="./build", show_default=True, help="Folder to use as build cache")
@packager_option(default="pex")
@click.argument("folder", required=True)
def package(dist, build, packager, folder):
    """
    Package a project from source checkout
    """
    dist = system.resolved_path(dist)
    build = system.resolved_path(build)
    folder = system.resolved_path(folder)

    SETTINGS.meta = meta_folder(build)
    setup_audit_log()
    bootstrap()

    if not os.path.isdir(folder):
        system.abort("Folder %s does not exist", short(folder))

    setup_py = os.path.join(folder, "setup.py")
    if not os.path.exists(setup_py):
        system.abort("No setup.py in %s", short(folder))

    with cd(folder):
        # Some setup.py's assume their working folder is the folder where they're in
        name = system.run_program(sys.executable, setup_py, "--name", fatal=False)
        if not name:
            system.abort("Could not determine package name from %s", short(setup_py))

    p = get_packager(name, packager)
    p.dist_folder = system.resolved_path(dist)
    p.build_folder = system.resolved_path(build)
    p.source_folder = system.resolved_path(folder)
    r = p.package()
    system.info("Packaged %s successfully, produced: %s", short(folder), system.represented_args(r, base=folder))
    sys.exit(0)


@main.command()
@click.option("--diagnostics", "-d", is_flag=True, help="Show diagnostics info")
def settings(diagnostics):
    """
    Show settings
    """
    setup_audit_log()
    if diagnostics:
        system.info("python interpreter: %s", short(system.PYTHON))
        system.info("sys.argv          : %s", system.represented_args(sys.argv))
        system.info("sys.executable    : %s", short(sys.executable))
        system.info("sys.prefix        : %s", short(getattr(sys, "prefix", None)))
        system.info("sys.real_prefix   : %s", short(getattr(sys, "real_prefix", None)))
        system.info("")

    system.info(SETTINGS.represented())
