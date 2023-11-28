"""
Bootstrap pickley
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

DRYRUN = False
HOME = os.path.expanduser("~")
TMP_FOLDER = None  # type: (str | None)
RX_VERSION = re.compile(r"^\D*(\d+)\.(\d+).*$")
DEFAULT_BASE = "~/.local/bin"


def abort(message):
    sys.exit("--------\n\n%s\n\n--------" % message)


def built_in_download(target, url):
    from urllib.request import Request, urlopen

    request = Request(url)
    response = urlopen(request, timeout=5)
    with open(target, "wb") as fh:
        fh.write(response.read())


def download(target, url, dryrun=None):
    if not hdry("Would download %s" % url, dryrun=dryrun):
        try:
            return built_in_download(target, url)

        except Exception:
            print("Built-in download of %s failed, trying curl or wget" % url)

        curl = which("curl")
        if curl:
            return run_program(curl, "-fsSL", "-o", target, url, dryrun=dryrun)

        wget = which("wget")
        if wget:
            return run_program(wget, "-q", "-O", target, url, dryrun=dryrun)

        abort("No curl, nor wget, can't download %s to %s" % (url, target))


def ensure_folder(path):
    if path and not os.path.isdir(path):
        if not hdry("Would create %s" % short(path)):
            os.makedirs(path)


def find_base(base):
    if not base:
        base = which("pickley")
        if base:
            print("Found existing %s" % short(base))
            return os.path.dirname(base)

        base = DEFAULT_BASE

    candidates = base.split(os.pathsep)
    for c in candidates:
        c = os.path.expanduser(c)
        if c and os.path.isdir(c) and is_writable(c):
            return c

    abort("Make sure %s is writeable." % candidates[0])


def get_latest_pickley_version():
    pickley_meta = os.path.join(TMP_FOLDER, "pickley-meta.json")
    download(pickley_meta, "https://pypi.org/pypi/pickley/json", dryrun=False)
    data = read_json(pickley_meta)
    return data["info"]["version"]


def hdry(message, dryrun=None):
    """Helps handle dryrun"""
    if dryrun is None:
        dryrun = DRYRUN

    if dryrun:
        print(message)
        return True


def is_executable(path):
    return path and os.path.isfile(path) and os.access(path, os.X_OK)


def is_writable(path):
    return path and os.access(path, os.W_OK)


def read_json(path):
    if path.startswith("{"):
        return json.loads(path)

    with open(path) as fh:
        return json.load(fh)


def read_optional_json(path):
    if path:
        try:
            return read_json(path)

        except Exception:
            return None


def run_program(program, *args, **kwargs):
    capture = kwargs.pop("capture", False)
    dryrun = kwargs.pop("dryrun", None)
    fatal = kwargs.pop("fatal", True)
    description = "%s %s" % (short(program), " ".join(short(x) for x in args))
    if not hdry("Would run: %s" % description, dryrun=dryrun):
        if capture:
            stdout = stderr = subprocess.PIPE

        else:
            stdout = stderr = None
            print("Running: %s" % description)

        p = subprocess.Popen([program] + list(args), stdout=stdout, stderr=stderr)
        if capture:
            output, _ = p.communicate()
            if output is not None:
                output = output.decode("utf-8").strip()

            return None if p.returncode else output

        p.wait()
        if fatal and p.returncode:
            abort("'%s' exited with code %s" % (short(program), p.returncode))

        return p.returncode


def seed_config(pickley_base, desired_cfg, force=False):
    """Seed pickley config"""
    if desired_cfg:
        desired_cfg = read_json(desired_cfg)
        if desired_cfg and isinstance(desired_cfg, dict):
            pickley_config = os.path.join(pickley_base, ".pk", "config.json")
            cfg = read_optional_json(pickley_config)
            if force or cfg != desired_cfg:
                msg = "%s with %s" % (short(pickley_config), desired_cfg)
                if not hdry("Would seed %s" % msg):
                    print("Seeding %s" % msg)
                    ensure_folder(os.path.dirname(pickley_config))
                    with open(pickley_config, "wt") as fh:
                        json.dump(desired_cfg, fh, sort_keys=True, indent=2)
                        fh.write("\n")


def seed_mirror(mirror, force=False):
    if mirror:
        pip_conf = os.path.expanduser("~/.config/pip/pip.conf")
        if force or not os.path.exists(pip_conf):
            ensure_folder(os.path.dirname(pip_conf))
            msg = "%s with %s" % (short(pip_conf), mirror)
            if not hdry("Would seed %s" % msg):
                print("Seeding %s" % msg)
                with open(pip_conf, "wt") as fh:
                    fh.write("[global]\nindex-url = %s\n" % mirror)


def short(text):
    if TMP_FOLDER:
        text = text.replace(TMP_FOLDER + os.path.sep, "")

    return text.replace(HOME, "~")


def which(program):
    prefix_bin = os.path.join(sys.prefix, "bin")
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p != prefix_bin:
            fp = os.path.join(p, program)
            if fp and is_executable(fp):
                return fp


def create_virtualenv(tmp_folder, python_exe, venv_folder, runner=None, dryrun=None):
    """
    Args:
        tmp_folder (str): Temp folder to use, virtualenv.pyz is downloaded there
        python_exe (str): Target python executable
        venv_folder (str): Target venv folder
        runner (callable): Function to use to run virtualenv
    """
    pv = "%s.%s" % (sys.version_info[0], sys.version_info[1])
    zipapp = os.path.join(tmp_folder, "virtualenv-%s.pyz" % pv)
    if not os.path.exists(zipapp):
        url = "https://bootstrap.pypa.io/virtualenv/%s/virtualenv.pyz" % pv
        download(zipapp, url, dryrun=dryrun)

    if runner is None:
        runner = run_program

    return runner(sys.executable, zipapp, "-q", "-p", python_exe, venv_folder)


def find_venv_exe(folder, name):
    for bname in (name, "%s3" % name):
        path = os.path.join(folder, "bin", bname)
        if is_executable(path):
            return path


def main(args=None):
    """Bootstrap pickley"""
    global DRYRUN
    global TMP_FOLDER

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--dryrun", "-n", action="store_true", help="Perform a dryrun")
    parser.add_argument("--base", "-b", help="Base folder to use (default: ~/.local/bin)")
    parser.add_argument("--cfg", "-c", help="Seed pickley config with given contents (file or serialized json)")
    parser.add_argument("--force", "-f", action="store_true", help="Force a rerun (even if already done)")
    parser.add_argument("--mirror", "-m", help="Seed pypi mirror in pip.conf")
    parser.add_argument("version", nargs="?", help="Version to bootstrap (default: latest)")
    args = parser.parse_args(args=args)

    DRYRUN = args.dryrun
    if "__PYVENV_LAUNCHER__" in os.environ:
        del os.environ["__PYVENV_LAUNCHER__"]

    pickley_base = find_base(args.base)
    if args.cfg:
        # When --cfg is used, make sure chosen base is in PATH
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        if pickley_base not in path_dirs:
            abort("Make sure %s is in your PATH environment variable." % pickley_base)

    print("Using %s, base: %s" % (sys.executable, short(pickley_base)))
    seed_config(pickley_base, args.cfg, force=args.force)
    seed_mirror(args.mirror, force=args.force)
    TMP_FOLDER = os.path.realpath(tempfile.mkdtemp())
    try:
        pickley_exe = os.path.join(pickley_base, "pickley")
        pickley_version = args.version or get_latest_pickley_version()
        if not args.force and is_executable(pickley_exe):
            v = run_program(pickley_exe, "--version", capture=True, dryrun=False)
            if v == pickley_version:
                print("%s version %s is already installed" % (short(pickley_exe), v))
                sys.exit(0)

            if v and len(v) < 24:  # If long output -> old pickley is busted (stacktrace)
                print("Replacing older pickley %s" % v)

        pickley_venv = os.path.join(pickley_base, ".pk", "pickley-%s" % pickley_version)
        needs_virtualenv = run_program(sys.executable, "-mvenv", "--clear", pickley_venv, fatal=False)
        if not needs_virtualenv and not DRYRUN:
            needs_virtualenv = not find_venv_exe(pickley_venv, "pip")

        if needs_virtualenv:
            print("-mvenv failed, falling back to virtualenv")
            create_virtualenv(TMP_FOLDER, sys.executable, pickley_venv)

        if sys.version_info[:2] <= (3, 7):
            # TODO: remove this when py3.6 and 3.7 are truly buried
            run_program(os.path.join(pickley_venv, "bin", "pip"), "-q", "install", "-U", "pip==21.3.1")

        run_program(os.path.join(pickley_venv, "bin", "pip"), "-q", "install", "pickley==%s" % pickley_version)
        run_program(os.path.join(pickley_venv, "bin", "pickley"), "base", "bootstrap-own-wrapper")

    finally:
        shutil.rmtree(TMP_FOLDER, ignore_errors=True)
        TMP_FOLDER = None


if __name__ == "__main__":
    main()
