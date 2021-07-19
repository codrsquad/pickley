"""
Bootstrap pickley
"""

import argparse
import json
import os
import shutil
import ssl
import subprocess  # nosec
import sys
import tempfile
from urllib.request import Request, urlopen

DRYRUN = False
HOME = os.path.expanduser("~")
VIRTUALENV_URL = "https://bootstrap.pypa.io/virtualenv/virtualenv.pyz"
TMP_FOLDER = None  # type: str


def download(target, url, dryrun=False):
    if dryrun:
        print("Would download %s" % url)
        return

    try:
        request = Request(url)
        response = urlopen(request, timeout=30, context=ssl.SSLContext())  # nosec
        with open(target, "wb") as fh:
            fh.write(response.read())

        return

    except Exception as e:
        print("GET %s failed, trying with curl: %s" % (url, e))

    curl = which("curl")
    if curl:
        return run_program(curl, "-fsSL", "-o", target, url)

    return run_program("wget", "-q", "-O%s" % target, url)


def find_python3():
    if sys.version_info[0] == 3 and sys.prefix == sys.base_prefix:
        # We're not running from a venv
        return sys.executable

    if is_executable("/usr/bin/python3"):
        return "/usr/bin/python3"

    return which("python3")


def get_latest_pickley_version():
    pickley_meta = os.path.join(TMP_FOLDER, "pickley-meta.json")
    download(pickley_meta, "https://pypi.org/pypi/pickley/json")
    with open(pickley_meta) as fh:
        data = json.load(fh)
        return data["info"]["version"]


def is_executable(path):
    return path and os.path.isfile(path) and os.access(path, os.X_OK)


def merged_output(*args):
    result = []
    for text in args:
        if isinstance(text, bytes):
            text = text.decode("utf-8").strip()

        if text:
            result.append(text)

    return "\n".join(result).strip()


def run_program(*args, capture=False):
    if capture:
        stdout = stderr = subprocess.PIPE

    else:
        stdout = stderr = None
        description = " ".join(short(x) for x in args)
        print("%s: %s" % ("Would run" if DRYRUN else "Running", description))
        if DRYRUN:
            return

    p = subprocess.Popen(args, stdout=stdout, stderr=stderr)  # nosec
    out, err = p.communicate()
    if capture:
        out = merged_output(out, err, "exited with code %s" % p.returncode if p.returncode else None)
        return out

    if p.returncode:
        sys.exit("%s exited with code %s" % (args[0], "%s: %s" % (p.returncode, out) if out else "%s" % p.returncode))


def short(text):
    if text == sys.executable:
        return "python"

    if TMP_FOLDER:
        text = text.replace(TMP_FOLDER + os.path.sep, "")

    return text.replace(HOME, "~")


def which(program):
    for p in os.environ.get("PATH", "").split(os.pathsep):
        fp = os.path.join(p, program)
        if fp and is_executable(fp):
            return fp


def main(args=None):
    """Bootstrap pickley"""
    global DRYRUN
    global TMP_FOLDER

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--base", "-b", default="~/.local/bin", help="Base folder to use (default: %(default)s)")
    parser.add_argument("--dryrun", "-n", action="store_true", help="Perform a dryrun")
    parser.add_argument("--force", "-f", action="store_true", help="Force a rerun (even if already done)")
    parser.add_argument("version", nargs="?", help="Version to bootstrap (default: latest)")
    args = parser.parse_args(args=args)

    DRYRUN = args.dryrun
    if "__PYVENV_LAUNCHER__" in os.environ:
        del os.environ["__PYVENV_LAUNCHER__"]

    python3 = find_python3()
    if not python3:
        sys.exit("Could not find python3 on this machine")

    TMP_FOLDER = os.path.realpath(tempfile.mkdtemp())
    try:
        pickley_base = os.path.expanduser(args.base)
        pickley_exe = os.path.join(pickley_base, "pickley")
        pickley_version = args.version
        spec = None
        if not pickley_version:
            pickley_version = get_latest_pickley_version()

        if not spec:
            spec = "pickley==%s" % pickley_version

        if not args.force and is_executable(pickley_exe):
            v = run_program(pickley_exe, "--version", capture=True)
            if v == pickley_version:
                print("%s version %s is already installed" % (short(pickley_exe), v))
                sys.exit(0)

            if v and len(v) < 24:  # If long output -> old pickley is busted (stacktrace)
                print("Replacing older pickley %s" % v)

        pickley_venv = os.path.join(pickley_base, ".pickley", "pickley", "pickley-%s" % pickley_version)
        zipapp = os.path.join(TMP_FOLDER, "virtualenv.pyz")
        download(zipapp, VIRTUALENV_URL, dryrun=DRYRUN)
        run_program(sys.executable, zipapp, "-q", "--clear", "--download", "-p", python3, pickley_venv)
        run_program(os.path.join(pickley_venv, "bin", "pip"), "-q", "install", spec)
        run_program(os.path.join(pickley_venv, "bin", "pickley"), "base", "bootstrap-own-wrapper")

    finally:
        shutil.rmtree(TMP_FOLDER, ignore_errors=True)
        TMP_FOLDER = None


if __name__ == "__main__":
    main()
