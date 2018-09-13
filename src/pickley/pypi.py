import json
import os
import re
from distutils.version import StrictVersion

from six.moves.urllib.request import Request, urlopen

from pickley import decode, system


RE_HTML_VERSION = re.compile(r'href=".+/([^/]+)\.tar\.gz#')


def request_get(url):
    """
    :param str url: URL to query
    :return str: Response body
    """
    try:
        system.debug("GET %s", url)
        request = Request(url)  # nosec
        response = urlopen(request).read()  # nosec
        return response and decode(response).strip()

    except Exception as e:
        code = getattr(e, "code", None)
        if isinstance(code, int) and code >= 400 and code < 500:
            return None

        try:
            # Some old python installations have trouble with SSL (OSX for example), try curl
            data = system.run_program("curl", "-s", url, dryrun=False, fatal=False)
            return data and decode(data).strip()

        except Exception as e:
            system.debug("GET %s failed: %s", url, e, exc_info=e)

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
        return "can't determine latest version from '%s'" % url

    if data[0] == "{":
        # See https://warehouse.pypa.io/api-reference/json/
        try:
            data = json.loads(data)
            return data.get("info", {}).get("version")

        except Exception as e:
            system.warning("Failed to parse pypi json: %s\n%s", e, data)

        return "can't determine latest version from '%s'" % url

    # Legacy mode: parse returned HTML
    prefix = "%s-" % name
    latest = None
    latest_text = None
    for line in data.splitlines():
        m = RE_HTML_VERSION.search(line)
        if m:
            value = m.group(1)
            if value.startswith(prefix):
                try:
                    version_text = value[len(prefix):]
                    canonical_version = version_text
                    if "+" in canonical_version:
                        canonical_version, _, _ = canonical_version.partition("+")
                    value = StrictVersion(canonical_version)
                    if not value.prerelease and latest is None or latest < value:
                        latest = value
                        latest_text = version_text
                except ValueError:
                    pass

    return latest_text or "can't determine latest version from '%s'" % url


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
        if line.startswith("["):
            section = line.strip("[]").strip()
            continue
        if section != "console_scripts":
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value:
            result.append(key)

    return result
