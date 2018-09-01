
import os

from pex.bin.pex import main as pex_main
from pip._internal import main as pip_main

from pickley import capture_output, system
from pickley.settings import SETTINGS


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
        args = system.flattened([self.prelude_args(), args], unique=False)

        if system.DRYRUN:
            system.debug("Would run: %s %s", self.name, system.represented_args(args))
            return None

        system.ensure_folder(self.cache, folder=True)
        system.debug("Running %s %s", self.name, system.represented_args(args))
        with capture_output(self.cache, env=self.custom_env()) as captured:
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

    def shebang(self, python_interpreter):
        """
        :param str|None python_interpreter: Python interpreter to use
        :return str|None: Suitable shebang
        """
        if not python_interpreter:
            return None
        if os.path.isabs(python_interpreter):
            return python_interpreter
        return "/usr/bin/env %s" % python_interpreter

    def build(self, script_name, package_name, version, destination, python_interpreter=None):
        """
        :param str script_name: Entry point name
        :param str package_name: Pypi package name
        :param str version: Specific version of 'package_name' to use
        :param str destination: Path where to generate pex
        :param str|None python_interpreter: Python interpreter to use
        :return str|None: None if successful, problem description otherwise
        """
        system.delete_file(destination)
        args = []
        args.extend(["-c%s" % script_name, "-o%s" % destination, "%s==%s" % (package_name, version)])
        if not python_interpreter:
            if self.is_universal(package_name, version):
                python_interpreter = "python"
            else:
                python_interpreter = system.PYTHON
        if python_interpreter != system.PYTHON and python_interpreter != "python":
            args.append("--python=%s" % python_interpreter)
        shebang = self.shebang(python_interpreter)
        if shebang:
            args.append("--python-shebang=%s" % shebang)
        return self.run(args)
