import logging
import os

import runez

from pickley import system
from pickley.settings import short


LOG = logging.getLogger(__name__)


def uninstall_existing(target, fatal=True):
    """
    :param str target: Path to executable to auto-uninstall if needed
    :param bool|None fatal: Abort execution on failure if True
    :return int: 1 if successfully uninstalled, 0 if nothing to do, -1 if failed
    """
    handler = find_uninstaller(target)
    if handler:
        return handler(target, fatal=fatal)

    return runez.abort("Can't automatically uninstall %s", short(target), fatal=(fatal, -1))


def find_uninstaller(target):
    if not target or not os.path.exists(target):
        # Bogus path, or dangling symlink
        return runez.delete

    path = os.path.realpath(target)
    if path.startswith(os.path.realpath(system.SETTINGS.meta.path)):
        # Pickley symlink, can be simply deleted
        return runez.delete

    if os.path.isfile(target) and os.path.getsize(target) == 0:
        # Empty file
        return runez.delete

    content = runez.get_lines(target, fatal=None)
    if content and any(line.startswith(system.WRAPPER_MARK) for line in content):
        # pickley's own wrapper also fine to simply delete
        return runez.delete

    brew, name = find_brew_name(target)
    if brew and name:
        return brew_uninstall

    return None


def find_brew_name(target):
    """
    :param str target: Path to executable file
    :return str, str: Name of brew formula, if target was installed with brew
    """
    if not os.path.islink(target):
        return None, None

    path = os.path.realpath(target)
    folder = runez.parent_folder(target)
    cellar = os.path.join(runez.parent_folder(folder), "Cellar")
    if not path.startswith(cellar):
        return None, None

    brew = os.path.join(folder, "brew")
    if not runez.is_executable(brew):
        return None, None

    name, _, _ = path[len(cellar) + 1:].partition("/")
    return brew, name


def brew_uninstall(target, fatal=False):
    """
    :param str target: Path of file to uninstall
    :param bool fatal: Abort if True
    :return int: 1 if successfully uninstalled, 0 if nothing to do, -1 if failed
    """
    brew, name = find_brew_name(target)
    if not brew or not name:
        return -1

    output = runez.run(brew, "uninstall", "-f", name, fatal=False, logger=LOG.info)
    if output is False:
        # Failed brew uninstall
        return runez.abort("'%s uninstall %s' failed, please check", brew, name, fatal=(fatal, -1))

    # All good
    return 1
