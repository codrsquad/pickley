import os

from pickley import get_lines, short, system, WRAPPER_MARK
from pickley.settings import SETTINGS


def report_problem(fatal, message, *args, **kwargs):
    report = system.abort if fatal else system.error
    report(message, *args, **kwargs)
    return -1


def uninstall_existing(target, fatal=False):
    """
    :param str target: Path to executable to auto-uninstall if needed
    :param bool target: Abort if True
    :return int: 1 if successfully uninstalled, 0 if nothing to do, -1 if failed
    """
    handler = find_uninstaller(target)
    if handler:
        return handler(target, fatal=fatal)

    return report_problem(fatal, "Can't automatically uninstall %s", short(target))


def find_uninstaller(target):
    if not target or not os.path.exists(target):
        # Bogus path, or dangling symlink
        return system.delete_file

    path = os.path.realpath(target)
    if path.startswith(os.path.realpath(SETTINGS.meta.path)):
        # Pickley symlink, can be simply deleted
        return system.delete_file

    if os.path.isfile(target) and os.path.getsize(target) == 0:
        # Empty file
        return system.delete_file

    content = get_lines(target)
    if content and any(line.startswith(WRAPPER_MARK) for line in content):
        # pickley's own wrapper also fine to simply delete
        return system.delete_file

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
    folder = os.path.dirname(target)
    cellar = os.path.join(os.path.dirname(folder), "Cellar")
    if not path.startswith(cellar):
        return None, None

    brew = os.path.join(folder, "brew")
    if not system.is_executable(brew):
        return None, None

    name, _, _ = path[len(cellar) + 1:].partition("/")
    return brew, name


def brew_uninstall(target, fatal=False):
    """
    :param str target: Path of file to uninstall
    :param bool target: Abort if True
    :return int: 1 if successfully uninstalled, 0 if nothing to do, -1 if failed
    """
    brew, name = find_brew_name(target)
    if not brew or not name:
        return -1

    output = system.run_program(brew, "uninstall", "-f", name, fatal=False, dryrun=system.dryrun, logger=system.info)
    if output is None:
        # Failed brew uninstall
        return report_problem(fatal, "'%s uninstall %s' failed, please check", brew, name)

    # All good
    return 1
