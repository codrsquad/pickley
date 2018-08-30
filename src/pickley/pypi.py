import json
import logging
import os
import re
from distutils.version import StrictVersion

from six.moves.urllib.request import Request, urlopen

from pickley import decode


LOG = logging.getLogger(__name__)
RE_HTML_VERSION = re.compile(r'href=".+/([^/]+)\.tar\.gz#')


def request_get(url):
    """
    :param str url: URL to query
    :return str: Response body
    """
    try:
        LOG.debug("GET %s" % url)
        request = Request(url)              # nosec
        response = urlopen(request).read()  # nosec
        return response and decode(response).strip()

    except Exception as e:
        LOG.debug("GET %s failed: %s", url, e, exc_info=e)

    return None


def latest_pypi_version(url, name):
    """
    :param str|None url: Pypi index to use (default: pypi.org)
    :param str name: Pypi package name
    :return str: Determined latest version, if any
    """
    if not name:
        return None

    if not url:
        url = "https://pypi.org/pypi/%s/json" % name

    else:
        # Assume legacy only for now for custom pypi indices
        url = os.path.join(url, name)

    data = request_get(url)
    if not data:
        return None

    if data[0] == '{':
        data = json.loads(data)
        if isinstance(data, dict):
            return data.get('info', {}).get('version')

        return None

    versions = []
    prefix = "%s-" % name
    for line in data.splitlines():
        m = RE_HTML_VERSION.search(line)
        if m:
            value = m.group(1)
            if value.startswith(prefix):
                try:
                    value = StrictVersion(value[len(prefix):])
                    if not value.prerelease:
                        versions.append(value)
                except ValueError:
                    pass

    if versions:
        versions = sorted(versions, reverse=True)
        return str(versions[0])

    return None


def read_entry_points(lines):
    """
    :param lines: Contents of entry_points.txt
    :return list: List of entry points defined in 'lines'
    """
    result = []
    section = None

    for line in lines:
        line = decode(line).strip()
        if not line:
            continue
        if line.startswith('['):
            section = line.strip('[]').strip()
            continue
        if section != 'console_scripts':
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if value:
            result.append(key)

    return result
