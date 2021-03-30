import json
import logging
import os
import re

import runez
from runez.pyenv import Version


LOG = logging.getLogger(__name__)
RE_BASENAME = re.compile(r'href=".+/([^/#]+)\.(tar\.gz|whl)#', re.IGNORECASE)


class RequestsRequestor(object):
    """GET via https://pypi.org/project/requests/"""

    def prepare(self):
        import requests

        self.session = requests.sessions.Session()

    def __call__(self, url):
        r = self.session.get(url, timeout=30)
        return r.text if r.status_code != 404 else "does not exist"


class UrllibRequestor(object):
    """GET via urllib"""

    def prepare(self):
        import ssl
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError

        self.Request = Request
        self.urlopen = urlopen
        self.HTTPError = HTTPError

        ssl._create_default_https_context = ssl._create_unverified_context

    def __call__(self, url):
        try:
            request = self.Request(url)
            response = self.urlopen(request).read()
            return response and runez.decode(response).strip()

        except self.HTTPError as e:
            if e.code == 404:
                return None

            raise


def curl_get(url):
    """GET via curl"""
    result = runez.run("curl", "-s", url, dryrun=False, fatal=False)
    if result.failed:
        raise Exception("curl failed: %s" % result.full_output)

    return result.output


SIMPLE_GET = runez.FallbackChain(RequestsRequestor(), UrllibRequestor(), curl_get)


class PypiInfo(object):

    latest = None  # type: str

    def __init__(self, index, pspec, include_prerelease=False, pypi_get=SIMPLE_GET):
        """
        Args:
            index (str | None): URL to pypi index to use (default: pypi.org)
            pspec (pickley.PackageSpec): Pypi package name to lookup
            include_prerelease (bool): If True, include latest pre-release
            pypi_get (runez.FallbackChain): Allows to have multiple ways of querying pypi
        """
        self.index = index or pspec.cfg.default_index
        self.pspec = pspec
        self.problem = None
        if "{name}" in self.index:
            self.url = self.index.format(name=self.pspec.dashed)

        else:
            # Assume legacy only for now for custom pypi indices
            self.url = "%s/" % os.path.join(self.index, self.pspec.dashed)

        data = pypi_get(self.url)
        if not data:
            self.problem = "no data for %s, check your connection" % self.url
            return

        if data[0] == "{":  # See https://warehouse.pypa.io/api-reference/json/
            try:
                data = json.loads(data)
                self.latest = data.get("info", {}).get("version")

            except Exception as e:
                LOG.warning("Failed to parse pypi json from %s: %s\n%s", self.url, e, data)
                self.problem = "invalid json received from %s" % self.index

            return

        # Parse legacy pypi HTML
        lines = data.strip().splitlines()
        if not lines or "does not exist" in lines[0]:
            self.problem = "does not exist on %s" % self.index
            return

        releases = set()
        prereleases = set()
        for line in lines:
            m = RE_BASENAME.search(line)
            if m:
                text = self.version_part(m.group(1))
                if text:
                    version = Version(text)
                    if version.is_valid:
                        if version.prerelease:
                            prereleases.add(version)

                        else:
                            releases.add(version)

        if include_prerelease or not releases:
            releases = releases | prereleases

        if releases:
            releases = sorted(releases)
            self.latest = releases[-1].text
            return

        self.problem = "no versions published on %s" % self.index

    def __repr__(self):
        return "%s %s" % (self.pspec, self.latest)

    def _version_part(self, filename):
        if filename:
            filename = filename.lower()
            n = len(self.pspec.wheelified) + 1
            if filename.startswith("%s-" % self.pspec.wheelified.lower()):
                return filename[n:]

            n = len(self.pspec.dashed) + 1
            if filename.startswith("%s-" % self.pspec.dashed):
                return filename[n:]

            n = len(self.pspec.original) + 1
            if filename.startswith("%s-" % self.pspec.original.lower()):
                return filename[n:]

    def version_part(self, filename):
        """
        Args:
            filename (str): Filename to examine

        Returns:
            (str | None): Version extracted from `filename`, if applicable to current package spec
        """
        vp = self._version_part(filename)
        if vp and vp[0].isdigit():
            return vp
