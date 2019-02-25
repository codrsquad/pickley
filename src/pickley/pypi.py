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
RE_VERSION = re.compile(r"[^-]+-([^-]+)-.*")


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


def pypi_url():
    conf = runez.get_conf(runez.resolved_path("~/.config/pip/pip.conf"), fatal=None, default={})
    return conf.get("global", {}).get("index-url", DEFAULT_PYPI)


def latest_pypi_version(url, name):
    """
    :param str|None url: Pypi index to use (default: pypi.org)
    :param str name: Pypi package name
    :return str: Determined latest version, if any
    """
    if not name:
        return None

    if not url:
        url = pypi_url()

    if "{name}" in url:
        url = url.format(name=name)

    else:
        # Assume legacy only for now for custom pypi indices
        url = os.path.join(url, name)

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

    # Legacy mode: parse returned HTML
    latest = None
    latest_text = None
    prereleases = []
    for line in data.splitlines():
        m = RE_BASENAME.search(line)
        if m:
            basename = m.group(1)
            m = RE_VERSION.match(basename)
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
