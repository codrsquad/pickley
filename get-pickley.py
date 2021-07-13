"""
Bootstrap pickley
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile


LOG = logging.getLogger(__name__)
VIRTUALENV_URL = "https://bootstrap.pypa.io/virtualenv/virtualenv.pyz"


def is_executable(path):
    return path and os.path.isfile(path) and os.access(path, os.X_OK)


def which(program):
    for p in os.environ.get("PATH", "").split(os.pathsep):
        fp = os.path.join(p, program)
        if fp and is_executable(fp):
            return fp


def download(target, url):
    curl = which("curl")
    if is_executable(curl):
        run_program(curl, "-s", "-o", target, url)

    return run_program("wget", "-q", "-O%s" % target, url)


def run_program(*args):
    args = list(args)
    print("Running: %s" % args)
    p = subprocess.Popen(args)
    returncode = p.wait()
    if returncode:
        sys.exit("%s exited with code %s" % (args, returncode))


def find_python3():
    if sys.version_info[0] == 3 and sys.prefix == sys.base_prefix:
        # We're not running from a venv
        return sys.executable

    if is_executable("/usr/bin/python3"):
        return "/usr/bin/python3"

    return which("python3")


def main():
    """Bootstrap pickley"""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--base", default="~/.local/bin", help="Base folder to use (default: %(default)s)")
    parser.add_argument("--force", "-f", action="store_true", help="Force a rerun (even if already done)")
    parser.add_argument("version", nargs="?", help="Version to bootstrap (default: latest)")
    args = parser.parse_args()

    logging.basicConfig(format="%(levelname)s - %(message)s", level=logging.DEBUG)
    pickley_base = os.path.expanduser(args.base)
    pickley_exe = os.path.join(pickley_base, "pickley")
    if not args.force and is_executable(pickley_exe):
        LOG.info("%s is already installed" % pickley_exe)
        sys.exit(0)

    if "__PYVENV_LAUNCHER__" in os.environ:
        del os.environ["__PYVENV_LAUNCHER__"]

    python3 = find_python3()
    if not python3:
        sys.exit("Could not find python3 on this machine")

    LOG.info("Using %s" % python3)
    tmp_folder = os.path.realpath(tempfile.mkdtemp())
    try:
        pickley_version = args.version
        spec = None
        if not pickley_version:
            pickley_meta = os.path.join(tmp_folder, "pickley-meta.json")
            download(pickley_meta, "https://pypi.org/pypi/pickley/json")
            with open(pickley_meta) as fh:
                data = json.load(fh)
                pickley_version = data["info"]["version"]

        elif os.path.isdir(pickley_version):
            # Allows to test this bootstrap script itself
            spec = pickley_version
            with open(os.path.join(spec, "src/pickley/__init__.py")) as fh:
                contents = fh.read()
                pickley_version = contents[contents.index("__version__") + 15:].partition('"')[0]

        if not spec:
            spec = "pickley==%s" % pickley_version

        pickley_venv = os.path.join(pickley_base, ".pickley", "pickley", "pickley-%s" % pickley_version)
        zipapp = os.path.join(tmp_folder, "virtualenv.pyz")
        download(zipapp, VIRTUALENV_URL)
        run_program(sys.executable, zipapp, "-p", python3, pickley_venv)
        run_program(os.path.join(pickley_venv, "bin", "pip"), "install", spec)
        run_program(os.path.join(pickley_venv, "bin", "pickley"), "--debug", "base", "bootstrap-own-wrapper")

    finally:
        shutil.rmtree(tmp_folder, ignore_errors=True)


if __name__ == "__main__":
    main()
