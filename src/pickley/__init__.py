"""
Brew style python CLI installation
"""

import sys

import runez


__version__ = runez.get_version(__name__)


def pickley_program_path():
    """
    :return str: Path to pickley executable, with special case for test runs
    """
    path = sys.argv[0]
    path = "/dev/null/pytest" if "pycharm" in path.lower() else path
    return path
