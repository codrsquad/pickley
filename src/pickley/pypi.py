import json
import logging
import os
import re
from distutils.version import StrictVersion

import runez
from six.moves.urllib.request import Request, urlopen


LOG = logging.getLogger(__name__)
DEFAULT_PYPI = "https://pypi.org/pypi/{name}/json"
RE_BASENAME = re.compile(r'href=".+/([^/#]+)\.(tar\.gz|whl)#', re.IGNORECASE)
RE_VERSION = re.compile(r"([^-]+)")


def request_get(url):
    """
    :param str url: URL to query
    :return str: Response body
    """
    try:
        LOG.debug("GET %s", url)
        request = Request(url)  # nosec
        response = urlopen(request).read()  # nosec
        return response and runez.decode(response).strip()

    except Exception as e:
        code = getattr(e, "code", None)
        if isinstance(code, int) and 400 <= code < 500:
            return None

        try:
            # Some old python installations have trouble with SSL (OSX for example), try curl
            data = runez.run("curl", "-s", url, dryrun=False, fatal=False)
            return data and runez.decode(data).strip()

        except Exception as e:
            LOG.debug("GET %s failed: %s", url, e, exc_info=e)

    return None


def latest_pypi_version(url, package_spec):
    """
    :param str|None url: Pypi index to use (default: pypi.org)
    :param system.PackageSpec package_spec: Pypi package
    :return str: Determined latest version, if any
    """
    if not url:
        url = DEFAULT_PYPI

    if "{name}" in url:
        url = url.format(name=package_spec.dashed)

    else:
        # Assume legacy only for now for custom pypi indices
        url = os.path.join(url, package_spec.dashed)

    data = request_get(url)
    if not data:
        return "error: can't determine latest version from '%s'" % url

    if data[0] == "{":
        # See https://warehouse.pypa.io/api-reference/json/
        try:
            data = json.loads(data)
            return data.get("info", {}).get("version")

        except Exception as e:
            LOG.warning("Failed to parse pypi json from %s: %s\n%s", url, e, data)

        return "error: can't determine latest version from '%s'" % url

    return _legacy_pypi_version(package_spec, url, data)


def _legacy_pypi_version(package_spec, url, data):
    """
    Args:
        package_spec (system.PackageSpec): Pypi package
        url (str): Pypi url that delivered 'data'
        data (str): HTML from pypi/simple

    Returns:
        (str): Latest usable version, or problem (string starting with 'error:')
    """
    latest = None
    latest_text = None
    prereleases = []
    for line in data.splitlines():
        m = RE_BASENAME.search(line)
        if not m:
            continue

        version_part = package_spec.version_part(m.group(1))
        if not version_part:
            continue

        m = RE_VERSION.match(version_part)
        if m:
            try:
                version_text = m.group(1)
                canonical_version = version_text
                if "+" in canonical_version:
                    canonical_version, _, _ = canonical_version.partition("+")
                value = StrictVersion(canonical_version)
                if value.prerelease:
                    prereleases.append(version_text)
                elif latest is None or latest < value:
                    latest = value
                    latest_text = version_text

            except ValueError:
                pass

    if not latest_text:
        if prereleases:
            latest_text = "error: all published versions are pre-releases"
        else:
            latest_text = "error: can't determine latest version from '%s'" % url

    return latest_text
