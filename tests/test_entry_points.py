import sys

import six
import virtualenv

from pickley.package import find_entry_points, find_prefix, find_site_packages


INEXISTING_FILE = "does/not/exist"


def test_find():
    assert find_entry_points("", "", "") is None
    assert find_entry_points(INEXISTING_FILE, "foo", "1.0") is None
    entry_points = find_entry_points(sys.prefix, "virtualenv", virtualenv.__version__)
    assert find_entry_points(sys.prefix, "virtualenv", virtualenv.__version__ + ".0") == entry_points

    if virtualenv.__version__.endswith(".0"):
        assert entry_points == find_entry_points(sys.prefix, "virtualenv", virtualenv.__version__[:-2])

    assert find_entry_points(sys.prefix, "virtualenv", virtualenv.__version__ + ".0.0") is None

    if six.__version__.endswith(".0"):
        assert find_entry_points(sys.prefix, "six", six.__version__[:-2]) is None

    assert find_prefix({}, "") is None

    assert find_site_packages(INEXISTING_FILE) is None
