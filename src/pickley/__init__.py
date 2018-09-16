"""
Brew style python CLI installation
"""

import sys

from pkg_resources import get_distribution


try:
    __version__ = get_distribution(__name__).version
except Exception:  # pragma: no cover
    __version__ = '0.0.0'


def decode(value):
    """ Python 2/3 friendly decoding of output """
    if isinstance(value, bytes) and not isinstance(value, str):
        return value.decode("utf-8")
    return value


def pickley_program_path():
    """
    :return str: Path to pickley executable, with special case for test runs
    """
    path = sys.argv[0]
    path = "/dev/null/pytest" if "pycharm" in path.lower() else path
    return path
