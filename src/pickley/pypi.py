import json
import logging
import os
import re

import runez


LOG = logging.getLogger(__name__)
RE_BASENAME = re.compile(r'href=".+/([^/#]+)\.(tar\.gz|whl)#', re.IGNORECASE)
RE_VERSION = re.compile(r"^((\d+)((\.(\d+))+)((a|b|c|rc)(\d+))?(\.(dev|post)(\d+))?).*$")


class PepVersion(object):
    """
    Parse versions according to PEP-0440, ordering for non pre-releases is well supported
    Pre-releases are partially supported, no complex combinations (such as .post.dev) are paid attention to
    """

    components = None
    prerelease = None

    def __init__(self, text):
        self.text = text
        m = RE_VERSION.match(text)
        if not m:
            return

        self.text, major, main_part, pre, pre_num, rel, rel_num = m.group(1, 2, 3, 7, 8, 10, 11)
        components = (major + main_part).split(".")
        if len(components) > 3:
            return  # Invalid version

        while len(components) < 3:
            components.append(0)

        components.append(rel_num if rel == "post" else 0)  # Using imaginary 4th component to hold post-release
        self.components = tuple(map(int, components))
        if pre:
            self.prerelease = ("c" if pre == "rc" else pre, int(pre_num))

        if rel == "dev":
            self.prerelease = ("dev", int(rel_num))

    def __repr__(self):
        return self.text

    def __hash__(self):
        return hash(self.text)

    def __eq__(self, other):
        return isinstance(other, PepVersion) and self.components == other.components and self.prerelease == other.prerelease

    def __lt__(self, other):
        if isinstance(other, PepVersion):
            if self.components == other.components:
                if self.prerelease:
                    return other.prerelease and self.prerelease < other.prerelease

                return bool(other.prerelease)

            return self.components < other.components


class Requestor(object):
    """
    Allows to query pypi using different ways, this was done to:
    - avoid bundling requests (makes pickley deployable ~70% smaller)
    - work around the fact that some system pythons (example: OSX) have urllib with buggy SSL
    """

    def __init__(self):
        self.ready = None
        self.failed = None
        self.name = self.__class__.__name__.replace("Requestor", "").lower()

    def __repr__(self):
        return "%s%s" % (self.name, "*" if self.failed else "")

    def prepare(self):
        if self.ready is None:
            try:
                self._do_prepare()
                self.ready = True

            except Exception as e:
                self.ready = False
                self.failed = e

        return self.ready

    def _do_prepare(self):
        """Effective implementation"""

    def get(self, url):
        """
        Args:
            url (str): URL to query

        Returns:
            (str): Body of returned GET query
        """
        try:
            return self._do_get(url)

        except Exception as e:
            self.ready = False
            self.failed = e

    def _do_get(self, url):
        """Effective implementation"""


class UrllibRequestor(Requestor):
    """Query pypi via urllib"""

    def _do_prepare(self):
        import ssl
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError

        self.Request = Request
        self.urlopen = urlopen
        self.HTTPError = HTTPError

        ssl._create_default_https_context = ssl._create_unverified_context

    def _do_get(self, url):
        try:
            request = self.Request(url)
            response = self.urlopen(request).read()
            return response and runez.decode(response).strip()

        except self.HTTPError as e:
            if e.code == 404:
                return None

            raise


class CurlRequestor(Requestor):
    """Query pypi via curl"""

    def _do_get(self, url):
        result = runez.run("curl", "-s", url, dryrun=False, fatal=False)
        if result.failed:
            raise Exception("curl failed: %s" % result.full_output)

        return result.output


class RequestsRequestor(Requestor):
    """Query pypi via https://pypi.org/project/requests/"""

    def _do_prepare(self):
        import requests

        self.session = requests.sessions.Session()

    def _do_get(self, url):
        r = self.session.get(url, timeout=30)
        return r.text if r.status_code != 404 else "does not exist"


class RequestorChain(object):
    """Allows to have multiple ways of querying pypi"""

    def __init__(self, available=None):
        if available is None:
            available = [CurlRequestor(), UrllibRequestor(), RequestsRequestor()]

        self.current = None
        self.initial = available
        self.available = list(available)
        self.failed = []

    def __repr__(self):
        return ", ".join(str(s) for s in self.initial)

    def get_current(self):
        """
        Returns:
            (Requestor): Current requestor to use
        """
        if self.current is not None and self.current.ready:
            return self.current

        while True:
            if not self.available:
                return None

            self.current = self.available.pop()
            self.current.prepare()
            if self.current.ready:
                return self.current

            self.failed.append(self.current)

    def get(self, url):
        while True:
            current = self.get_current()
            if current is None:
                raise Exception("Internal error: can't query %s, all access methods failed" % url)

            r = current.get(url)
            if current.failed:
                self.failed.append(self.current)
                continue

            return r


class PypiInfo(object):

    latest = None  # type: str

    def __init__(self, index, pspec, include_prerelease=False, chain=RequestorChain()):
        """
        Args:
            index (str | None): URL to pypi index to use (default: pypi.org)
            pspec (pickley.PackageSpec): Pypi package name to lookup
            include_prerelease (bool): If True, include latest pre-release
            chain (RequestorChain): Allows to have multiple ways of querying pypi
        """
        self.index = index or pspec.cfg.default_index
        self.pspec = pspec
        self.problem = None
        if "{name}" in self.index:
            self.url = self.index.format(name=self.pspec.dashed)

        else:
            # Assume legacy only for now for custom pypi indices
            self.url = "%s/" % os.path.join(self.index, self.pspec.dashed)

        data = chain.get(self.url)
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
                    version = PepVersion(text)
                    if version.components:
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
