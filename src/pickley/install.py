
import logging
import os

from pex.bin.pex import main as pex_main
from pip._internal import main as pip_main

from pickley import capture_output, delete_file, ensure_folder, flattened, represented_args
from pickley.settings import SETTINGS


LOG = logging.getLogger(__name__)


def add_paths(result, env_var, *paths):
    """
    :param dict result: Where to add path customization
    :param str env_var: Env var to customize
    :param list *paths: Paths to add, if corresponding folder exists
    """
    added = 0
    current = os.environ.get(env_var, "")
    if current:
        current = current.split(":")
    else:
        current = []
    current = [x for x in current if x]
    for path in paths:
        if os.path.isdir(path) and path not in current:
            added += 1
            current.append(path)
    if added:
        result[env_var] = ":".join(current)


class Runner:

    def __init__(self, cache):
        """
        :param str cache: Path to folder to use as cache
        """
        self.name = self.__class__.__name__.replace("Runner", "").lower()
        self.cache = cache

    def run(self, *args):
        args = flattened([self.prelude_args(), args], unique=False)

        if SETTINGS.dryrun:
            LOG.debug("Would run: %s %s", self.name, represented_args(args))
            return None

        ensure_folder(self.cache, folder=True, dryrun=SETTINGS.dryrun)
        LOG.debug("Running %s %s", self.name, represented_args(args))
        with capture_output(self.cache, env=self.custom_env(), dryrun=SETTINGS.dryrun) as captured:
            try:
                exit_code = self.effective_run(args)
            except SystemExit as e:
                exit_code = e.code
            if exit_code:
                return captured.error
            return None

    def custom_env(self):
        """
        :return dict: Optional customized env vars to use
        """
        return None

    def effective_run(self, args):
        """
        :param list args: Args to run with
        :return int: Exit code
        """
        return 1

    def prelude_args(self):
        """
        :return list|None: Arguments to pass to invoked module for all invocations
        """
        pass


class PipRunner(Runner):

    def effective_run(self, args):
        """
        :param list args: Args to run with
        :return int: Exit code
        """
        return pip_main(args)

    def prelude_args(self):
        """
        :return list|None: Arguments to pass to invoked module for all invocations
        """
        return ["--disable-pip-version-check", "--cache-dir", self.cache]

    def wheel(self, *package_names):
        return self.run("wheel", "-i", SETTINGS.index, "--wheel-dir", self.cache, *package_names)


class PexRunner(Runner):

    def custom_env(self):
        """
        :return dict: Optional customized env vars to use
        """
        result = {}
        add_paths(result, "PKG_CONFIG_PATH", "/usr/local/opt/openssl/lib/pkgconfig")
        return result

    def effective_run(self, args):
        """
        :param list args: Args to run with
        :return int: Exit code
        """
        return pex_main(args)

    def prelude_args(self):
        """
        :return list|None: Arguments to pass to invoked module for all invocations
        """
        return ["--no-pypi", "--cache-dir", self.cache, "--repo", self.cache]

    def is_universal(self, package_name, version):
        """
        :param str package_name: Pypi package name
        :param str version: Specific version of 'package_name' to examine
        :return bool: True if wheel exists and is universal
        """
        if not os.path.isdir(self.cache):
            return False
        prefix = "%s-%s-" % (package_name, version)
        for fname in os.listdir(self.cache):
            if fname.startswith(prefix) and fname.endswith('.whl'):
                return "py2.py3-none" in fname
        return False

    def build(self, script_name, package_name, version, destination):
        """
        :param str script_name: Entry point name
        :param str package_name: Pypi package name
        :param str version: Specific version of 'package_name' to use
        :param str destination: Path where to generate pex
        :return str|None: None if successful, problem description otherwise
        """
        delete_file(destination, dryrun=SETTINGS.dryrun)
        args = []
        args.extend(["-c%s" % script_name, "-o%s" % destination, "%s==%s" % (package_name, version)])
        if self.is_universal(package_name, version):
            args.append('--python-shebang=/usr/bin/env python')
        return self.run(args)
