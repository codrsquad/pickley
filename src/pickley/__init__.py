"""
Brew style python CLI installation
"""

import os
import sys

from pkg_resources import get_distribution


HOME = os.path.expanduser("~")

try:
    __version__ = get_distribution(__name__).version
except Exception:  # pragma: no cover
    __version__ = '0.0.0'


def decode(value):
    """ Python 2/3 friendly decoding of output """
    if isinstance(value, bytes) and not isinstance(value, str):
        return value.decode("utf-8")
    return value


def short(path, base=None):
    """
    :param path: Path to represent in its short form
    :param str|list|set|None base: Base folder(s) to relativise paths to
    :return str: Short form, using '~' if applicable
    """
    if not path:
        return path
    path = str(path)
    if base:
        if not isinstance(base, (list, set)):
            base = [base]
        for b in base:
            if b:
                path = path.replace(b + "/", "")
    path = path.replace(HOME, "~")
    return path


def python_interpreter():
    """
    :return str: Path to python interpreter currently used
    """
    prefix = getattr(sys, "real_prefix", None)
    if prefix:
        return os.path.join(prefix, "bin", "python")
    else:
        return sys.executable


def pickley_program_path():
    """
    :return str: Path to pickley executable, with special case for test runs
    """
    path = sys.argv[0]
    path = "/dev/null/pytest" if "pycharm" in path.lower() else path
    return path
