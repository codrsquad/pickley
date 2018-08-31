import os

from pickley import short, system


USR_LOCAL_BIN = "/usr/local/bin"
BREW = os.path.join(USR_LOCAL_BIN, "brew")
BREW_CELLAR = "/usr/local/Cellar"


def uninstall_existing(target):
    """
    Clean existing non-pickley installation of same target
    """
    if not target or not os.path.exists(target):
        return
    if brew_uninstall(target):
        return
    system.abort("Please uninstall %s first", short(target))


def brew_uninstall(target):
    """
    :param str target: Path of file to uninstall
    :return bool: True if uninstallation was successful
    """
    if not target or not target.startswith(USR_LOCAL_BIN):
        return False
    real_path = os.path.realpath(target)
    if not real_path or not real_path.lower().startswith(BREW_CELLAR.lower()):
        return False
    name, _, _ = real_path[len(BREW_CELLAR) + 1:].partition("/")
    system.run_program(BREW, "uninstall", "-f", name, logger=system.info)
    system.run_program(BREW, "cleanup", fatal=False, logger=system.info)
    return True
