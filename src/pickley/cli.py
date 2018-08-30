"""
See https://github.com/zsimic/pickley
"""

import logging
import logging.config
import os
import sys
from logging.handlers import RotatingFileHandler

import click

from pickley import abort, cd, ensure_folder, inform, python, represented_args, resolved_path, run_program, short
from pickley.package import Packager, PACKAGERS
from pickley.settings import meta_cache, SETTINGS


LOG = logging.getLogger(__name__)
PICKLEY = "pickley"


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
            if "type" not in kwargs:
                kwargs.setdefault("metavar", "<%s>" % name.replace("-", ""))
                kwargs.setdefault("type", str)
        if not name.startswith("-"):
            name = "--%s" % name
        return click.option(name, *args, **kwargs)(f)

    return decorator


def packager_option(**kwargs):
    """Packager to use"""
    def _callback(ctx, param, value):
        return PACKAGERS.get(value)

    return _option(packager_option, "-p", name="packager", type=click.Choice(PACKAGERS.names()), callback=_callback, **kwargs)


def get_packager(name, packager=None, cache=None):
    """
    :param str name: Name of pypi package
    :param Packager|None packager:
    :param str|None cache: Optional custom cache folder to use
    :return Packager: Packager to use
    """
    pkg = packager or PACKAGERS.resolved(name)
    if not pkg:
        abort("Can't determine packager to use for '%s'" % name)
    if issubclass(pkg, Packager):
        return pkg(name, cache=cache)
    abort("Invalid packager implementation for '%s': %s" % (name, pkg.__class__.__name__))


def setup_audit_log():
    """Log to <base>/audit.log"""
    path = SETTINGS.cache.full_path("audit.log")
    ensure_folder(path)
    handler = RotatingFileHandler(path, maxBytes=500 * 1024, backupCount=1)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(process)s] %(levelname)s - %(message)s"))
    logging.root.addHandler(handler)
    LOG.debug("Arguments: %s" % represented_args(sys.argv))


def bootstrap():
    """
    Bootstrap pickley: re-install it as venv if need be

    Packaged as a pex, pickley is easy to distribute,
    however there are some edge cases where running pip from a pex-packaged CLI doesn't work very well
    So, first thing we do is re-package ourselves as a venv on the target machine
    """
    if not sys.argv:
        # Shouldn't happen
        return
    if sys.argv[0] != SETTINGS.base.full_path(PICKLEY):
        # We're not running from base location, don't bootstrap
        return

    p = get_packager(PICKLEY)
    p.refresh_current()
    if p.current.packager == p.implementation_name:
        # We're already packaged correctly, no need to bootstrap
        return

    # Re-install ourselves with correct packager
    LOG.debug("Bootstrapping %s with %s", PICKLEY, p.implementation_name)
    p.install(intent="bootstrap")
    p.cleanup()

    # Rerun with same args, to pick up freshly bootstrapped installation
    run_program(*sys.argv, dryrun=SETTINGS.dryrun, stdout=sys.stdout, stderr=sys.stderr)
    if SETTINGS.dryrun:
        return
    sys.exit(0)


@click.group(context_settings=dict(help_option_names=['-h', '--help'], max_content_width=160), epilog=__doc__)
@click.version_option()
@click.option('--debug', is_flag=True, help="Show debug logs")
@click.option('--dryrun', '-n', is_flag=True, help="Perform a dryrun")
def main(debug, dryrun):
    """
    Package manager for python CLIs
    """
    SETTINGS.dryrun = dryrun
    SETTINGS.add(["~/.config/pickley.json", 'pickley.json'])

    # Disable logging.config, as pip tries to get smart and configure all logging...
    logging.config.dictConfig = lambda x: None
    logging.getLogger('pip').setLevel(logging.WARNING)

    logging.root.setLevel(logging.DEBUG)

    # Format to use for stderr logging (this will be potentially consumed by calling processes, don't show timestamp etc)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if debug else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logging.root.addHandler(console)


@main.command()
@click.argument('packages', nargs=-1, required=False)
def check(packages):
    """
    Check whether specified packages need an upgrade
    """
    setup_audit_log()
    code = 0
    packages = SETTINGS.resolved_packages(packages) or SETTINGS.current_names()
    if not packages:
        inform("No packages installed")

    else:
        for name in packages:
            p = get_packager(name)
            p.refresh_current()
            p.refresh_desired()
            LOG.debug("desired: %s", repr(p.desired))
            LOG.debug("current: %s", repr(p.current))
            if not p.desired.valid:
                inform(p.desired, logger=LOG.error)
                code = 1
            elif not p.current.version:
                inform("%s not installed" % p.desired)
                code = 1
            elif not p.current.valid:
                inform(p.current)
                code = 1
            elif p.current.version != p.desired.version:
                inform("%s can be upgraded to %s" % (p.name, p.desired))
                code = 1
            else:
                inform("%s is installed" % p.desired)
            p.cleanup()

    sys.exit(code)


@main.command()
def list():
    """
    List installed packages
    """
    setup_audit_log()
    packages = SETTINGS.current_names()
    if not packages:
        inform("No packages installed")

    else:
        for name in packages:
            p = get_packager(name)
            p.refresh_current()
            inform(p.current)


@main.command()
@packager_option()
@click.option('--force', '-f', is_flag=True, help="Force installation, even if already installed")
@click.argument('packages', nargs=-1, required=True)
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
        p.cleanup()

    sys.exit(0)


@main.command()
@click.option('--dist', '-d', default='./dist', show_default=True, help="Folder where to produce package")
@click.option('--build', '-b', default='./build', show_default=True, help="Folder to use as build cache")
@packager_option(default="pex")
@click.argument('folder', required=True)
def package(dist, build, packager, folder):
    """
    Package a project from source checkout
    """
    dist = resolved_path(dist)
    build = resolved_path(build)
    folder = resolved_path(folder)

    SETTINGS.cache = meta_cache(build)
    setup_audit_log()

    if not os.path.isdir(folder):
        abort("Folder %s does not exist" % short(folder))

    setup_py = os.path.join(folder, "setup.py")
    if not os.path.exists(setup_py):
        abort("No setup.py in %s" % short(folder))

    with cd(folder):
        # Some setup.py's assume their working folder is the folder where they're in
        name = run_program(sys.executable, setup_py, "--name", fatal=False)
        if not name:
            abort("Could not determine package name from %s" % short(setup_py))

    p = get_packager(name, packager, cache=build)
    if hasattr(p, 'package'):
        r = p.package(destination=dist, wheel_source=folder)
        inform("Packaged %s successfully, produced: %s" % (short(folder), represented_args(r, base=folder)))
        sys.exit(0)

    abort("Packaging folders via '%s' is not supported" % p.implementation_name)


@main.command()
@click.option('--diagnostics', '-d', is_flag=True, help="Show diagnostics info")
def settings(diagnostics):
    """
    Show settings
    """
    setup_audit_log()
    if diagnostics:
        inform("python interpreter: %s" % short(python()))
        inform("sys.argv          : %s" % represented_args(sys.argv))
        inform("sys.executable    : %s" % short(sys.executable))
        inform("sys.prefix        : %s" % short(getattr(sys, 'prefix', None)))
        inform("sys.real_prefix   : %s" % short(getattr(sys, 'real_prefix', None)))
        inform("")

    inform(SETTINGS.represented())


if __name__ == "__main__":
    main()      # Only useful for convenient debugging in say PyCharm
