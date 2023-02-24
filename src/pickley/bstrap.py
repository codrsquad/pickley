"""
Bootstrap pickley
"""

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess  # nosec
import sys
import tempfile

DRYRUN = False
HOME = os.path.expanduser("~")
RUNNING_FROM_VENV = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
TMP_FOLDER = None  # type: str
RX_VERSION = re.compile(r"^\D*(\d+)\.(\d+).*$")
DEFAULT_BASE = "~/.local/bin"


def abort(message):
    sys.exit("--------\n\n%s\n\n--------" % message)


def built_in_download(target, url):
    from urllib.request import Request, urlopen

    request = Request(url)
    response = urlopen(request, timeout=5, context=ssl.SSLContext())  # nosec
    with open(target, "wb") as fh:
        fh.write(response.read())


def download(target, url, dryrun=None):
    if not hdry("Would download %s" % url, dryrun=dryrun):
        try:
            return built_in_download(target, url)

        except (ImportError, Exception):
            pass

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

    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    candidates = base.split(os.pathsep)
    for c in candidates:
        c = os.path.expanduser(c)
        if c and os.path.isdir(c) and is_writable(c) and c in path_dirs:
            return c

    msg = "Make sure %s is writeable and in your PATH variable." % candidates[0]
    abort(msg)


def find_python3():
    if sys.version_info[0] == 3 and not RUNNING_FROM_VENV:
        # We're not running from a venv
        return sys.executable

    if is_executable("/usr/bin/python3"):
        return "/usr/bin/python3"

    return which("python3")


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

        p = subprocess.Popen([program] + list(args), stdout=stdout, stderr=stderr)  # nosec
        if capture:
            output, _ = p.communicate()
            output = output and output.decode("utf-8").strip()
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
            pickley_config = os.path.join(pickley_base, ".pickley", "config.json")
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
    if text == sys.executable:
        return "python"

    if TMP_FOLDER:
        text = text.replace(TMP_FOLDER + os.path.sep, "")

    return text.replace(HOME, "~")


def which(program):
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if not RUNNING_FROM_VENV or p != os.path.join(sys.prefix, "bin"):
            fp = os.path.join(p, program)
            if fp and is_executable(fp):
                return fp


def get_python_version(python3):
    pv = run_program(python3, "--version", capture=True, dryrun=False)
    m = RX_VERSION.match(pv)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2))
        return major, minor


def pip_version(python_version):
    """
    Args:
        python_version (tuple): Target python version

    Returns:
        (str | None): Pinned pip version to use, if applicable
    """
    if python_version and python_version < (3, 7):
        return "21.3.1"


def create_virtualenv(tmp_folder, python_version, python_exe, venv_folder, virtualenv_version="20.10.0", runner=None, dryrun=None):
    """
    Args:
        tmp_folder (str): Temp folder to use, virtualenv.pyz is downloaded there
        python_version (tuple): Target python version
        python_exe (str): Target python executable
        venv_folder (str): Target venv folder
        virtualenv_version (str): Virtualenv version to use
        runner (callable): Function to use to run virtualenv
    """
    zipapp = os.path.join(tmp_folder, "virtualenv-%s.pyz" % virtualenv_version)
    if not os.path.exists(zipapp):
        url = "https://bootstrap.pypa.io/virtualenv/%s/virtualenv.pyz" % ("%s.%s" % python_version[0:2])
        download(zipapp, url, dryrun=dryrun)

    cmd = virtualenv_cmd(zipapp, python_version, python_exe, venv_folder)
    if runner is None:
        runner = run_program

    return runner(*cmd)


def virtualenv_cmd(virtualenv_path, python_version, python_exe, venv_folder):
    cmd = [sys.executable, virtualenv_path, "-q", "--clear"]
    pv = pip_version(python_version)
    if pv:
        cmd.append("--pip")
        cmd.append(pv)

    else:
        cmd.append("--download")

    cmd.append("-p")
    cmd.append(python_exe)
    cmd.append(venv_folder)
    return cmd


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

    python3 = find_python3()
    if not python3:
        abort("Could not find python3 on this machine")

    pickley_base = find_base(args.base)
    print("Using %s, base: %s" % (python3, short(pickley_base)))
    seed_config(pickley_base, args.cfg, force=args.force)
    seed_mirror(args.mirror, force=args.force)
    TMP_FOLDER = os.path.realpath(tempfile.mkdtemp())
    try:
        pickley_exe = os.path.join(pickley_base, "pickley")
        pickley_version = args.version
        spec = None
        if not pickley_version:
            pickley_version = get_latest_pickley_version()

        if not spec:
            spec = "pickley==%s" % pickley_version

        if not args.force and is_executable(pickley_exe):
            v = run_program(pickley_exe, "--version", capture=True, dryrun=False)
            if v == pickley_version:
                print("%s version %s is already installed" % (short(pickley_exe), v))
                sys.exit(0)

            if v and len(v) < 24:  # If long output -> old pickley is busted (stacktrace)
                print("Replacing older pickley %s" % v)

        pickley_venv = os.path.join(pickley_base, ".pickley", "pickley", "pickley-%s" % pickley_version)
        pv = get_python_version(python3)
        needs_virtualenv = True
        if pv and pv >= (3, 7):
            needs_virtualenv = run_program(python3, "-mvenv", "--clear", pickley_venv, fatal=False)
            if not needs_virtualenv and not DRYRUN:
                needs_virtualenv = not find_venv_exe(pickley_venv, "pip")

        if needs_virtualenv:
            # For py3.6 or pythons without pip, use old virtualenv
            create_virtualenv(TMP_FOLDER, pv, python3, pickley_venv)

        run_program(os.path.join(pickley_venv, "bin", "pip"), "-q", "install", spec)
        run_program(os.path.join(pickley_venv, "bin", "pickley"), "base", "bootstrap-own-wrapper")

    finally:
        shutil.rmtree(TMP_FOLDER, ignore_errors=True)
        TMP_FOLDER = None


if __name__ == "__main__":
    main()
