"""
This script is designed to be run standalone, and will bootstrap pickley in a given base folder.

Usage example:
    /usr/bin/python3 bstrap.py --base ~/.local/bin --mirror https://example.org/pypi
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

DOT_META = ".pk"
DRYRUN = False
HOME = os.path.expanduser("~")
PICKLEY = "pickley"


def abort(message):
    sys.exit(f"--------\n\n{message}\n\n--------")


class Bootstrap:
    def __init__(self, pickley_base, pickley_version):
        self.pickley_base = Path(pickley_base)
        self.pickley_version = pickley_version or get_latest_version(PICKLEY)
        self.pickley_exe = self.pickley_base / PICKLEY
        self.pk_path = self.pickley_base / DOT_META
        self.mirror = None

    def seed_mirror(self, mirror):
        self.mirror = mirror
        if mirror:
            seed_mirror(mirror, "~/.config/pip/pip.conf", "global")
            seed_mirror(mirror, "~/.config/uv/uv.toml", "pip")

    def seed_pickley_config(self, desired_cfg):
        pickley_config = self.pk_path / "config.json"
        cfg = read_optional_json(pickley_config)
        if cfg != desired_cfg:
            msg = f"{short(pickley_config)} with {desired_cfg}"
            if not hdry(f"Would seed {msg}"):
                print(f"Seeding {msg}")
                ensure_folder(os.path.dirname(pickley_config))
                with open(pickley_config, "wt") as fh:
                    json.dump(desired_cfg, fh, sort_keys=True, indent=2)
                    fh.write("\n")

    def find_uv(self):
        uv_path = self.pickley_base / "uv"
        if is_executable(uv_path):
            v = run_program(uv_path, "--version", dryrun=False, fatal=False)
            if v and len(v) < 64 and v.startswith("uv "):
                return uv_path

        uv_base = self.pk_path / ".uv"
        uv_path = uv_base / "bin/uv"
        if not is_executable(uv_path):
            download_uv(self.pk_path / ".cache", uv_base)

        return uv_path


def uv_url(version):
    if version:
        return f"https://github.com/astral-sh/uv/releases/download/{version}/uv-installer.sh"

    return "https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh"


def download_uv(pk_cache, target, version=None, dryrun=None):
    ensure_folder(pk_cache, dryrun=dryrun)
    script = os.path.join(pk_cache, "uv-installer.sh")
    url = uv_url(version)
    download(script, url, dryrun=dryrun)
    env = dict(os.environ)
    env["CARGO_DIST_FORCE_INSTALL_DIR"] = str(target)
    env.setdefault("HOME", str(target))  # uv's installer unfortunately assumes HOME is always defined (it is not in tox tests)
    run_program("/bin/sh", script, "--no-modify-path", env=env, dryrun=dryrun)


def http_get(url, timeout=5):
    try:
        request = Request(url)
        with urlopen(request, timeout=timeout) as response:
            data = response.read()

    except URLError:  # py3.6 ssl error
        import tempfile

        with tempfile.NamedTemporaryFile() as tmpf:
            tmpf.close()
            curl_download(tmpf.name, url, dryrun=False)
            with open(tmpf.name, "rb") as fh:
                data = fh.read()

    except Exception as e:
        abort(f"Failed to fetch {url}: {e}")

    if data:
        data = data.decode("utf-8").strip()

    return data


def built_in_download(target, url):
    request = Request(url)
    response = urlopen(request, timeout=5)
    with open(target, "wb") as fh:
        fh.write(response.read())


def curl_download(target, url, dryrun=None):
    curl = which("curl")
    if curl:
        return run_program(curl, "-fsSL", "-o", target, url, dryrun=dryrun)

    wget = which("wget")
    if wget:
        return run_program(wget, "-q", "-O", target, url, dryrun=dryrun)

    abort(f"No `curl` nor `wget`, can't download {url} to '{target}'")


def download(target, url, dryrun=None):
    if not hdry(f"Would download {url}", dryrun=dryrun):
        ensure_folder(os.path.dirname(target), dryrun=dryrun)
        try:
            return built_in_download(target, url)

        except Exception:
            print(f"Built-in download of {url} failed, trying curl or wget")
            return curl_download(target, url)


def ensure_folder(path, dryrun=None):
    if path and not os.path.isdir(path) and not hdry(f"Would create {short(path)}", dryrun=dryrun):
        os.makedirs(path)


def find_base(base):
    candidates = base.split(os.pathsep)
    for c in candidates:
        c = os.path.expanduser(c)
        if c and os.path.isdir(c) and is_writable(c):
            return c

    abort(f"Make sure '{candidates[0]}' is writeable.")


def get_latest_version(package_name):
    data = http_get(f"https://pypi.org/pypi/{package_name}/json")
    data = json.loads(data)
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


def read_optional_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)

    except Exception:  # pragma: no cover
        return None


def run_program(program, *args, **kwargs):
    fatal = kwargs.pop("fatal", True)
    description = " ".join(short(x) for x in args)
    description = f"{short(program)} {description}"
    if not hdry(f"Would run: {description}", dryrun=kwargs.pop("dryrun", None)):
        if fatal:
            stdout = stderr = None
            print(f"Running: {description}")

        else:
            stdout = stderr = subprocess.PIPE

        p = subprocess.Popen([program, *args], stdout=stdout, stderr=stderr, env=kwargs.pop("env", None))
        if fatal:
            p.wait()
            if p.returncode:
                abort(f"'{short(program)}' exited with code {p.returncode}")

            return p.returncode

        output, _ = p.communicate()
        if output is not None:
            output = output.decode("utf-8").strip()

        return None if p.returncode else output


def seed_mirror(mirror, path, section):
    config_path = os.path.expanduser(path)
    if not os.path.exists(config_path):
        ensure_folder(os.path.dirname(config_path))
        msg = f"{short(config_path)} with {mirror}"
        if not hdry(f"Would seed {msg}"):
            print(f"Seeding {msg}")
            with open(config_path, "wt") as fh:
                fh.write(f"[{section}]\nindex-url = {mirror}\n")


def short(text):
    return str(text).replace(HOME, "~")


def _add_uv_env(env, env_var, value):
    if value:
        env[env_var] = value
        return f"{env_var}={short(value)}"


def uv_env(mirror=None, python=None, venv=None, logger=None):
    if python or mirror or venv:
        env = dict(os.environ)
        logged = (
            _add_uv_env(env, "UV_PYTHON", python),
            _add_uv_env(env, "UV_INDEX_URL", mirror),
            _add_uv_env(env, "VIRTUAL_ENV", venv),
        )
        if logger:
            logger(", ".join(x for x in logged if x))

        return env


def which(program):
    prefix_bin = os.path.join(sys.prefix, "bin")
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p != prefix_bin:
            fp = os.path.join(p, program)
            if fp and is_executable(fp):
                return Path(fp)


def main(args=None):
    """Bootstrap pickley"""
    global DRYRUN

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--dryrun", "-n", action="store_true", help="Perform a dryrun")
    parser.add_argument("--base", "-b", default="~/.local/bin", help="Base folder to use (default: ~/.local/bin)")
    parser.add_argument("--check-path", action="store_true", help="Verify that stated --base is on PATH env var")
    parser.add_argument("--cfg", "-c", help="Seed pickley config with given contents (file or serialized json)")
    parser.add_argument("--force", "-f", action="store_true", help="Force bootstrap (even if already done)")
    parser.add_argument("--mirror", "-m", help="Seed pypi mirror in pip.conf")
    parser.add_argument("--venv-packager", help="Venv packager to use (default: `uv` latest version)")
    parser.add_argument("version", nargs="?", help="Version to bootstrap (default: latest)")
    args = parser.parse_args(args=args)

    DRYRUN = args.dryrun
    if "__PYVENV_LAUNCHER__" in os.environ:
        del os.environ["__PYVENV_LAUNCHER__"]

    bstrap = Bootstrap(find_base(args.base), args.version)
    if args.check_path:
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        if bstrap.pickley_base not in path_dirs:
            abort(f"Make sure '{bstrap.pickley_base}' is in your PATH environment variable.")

    print(f"Using {sys.executable}, base: {short(bstrap.pickley_base)}")
    bstrap.seed_mirror(args.mirror)
    if args.cfg:
        if not args.cfg.startswith("{") or not args.cfg.endswith("}"):
            abort(f"--config must be a serialized json object, invalid json: {args.cfg}")

        cfg = json.loads(args.cfg)
        if cfg and isinstance(cfg, dict):
            bstrap.seed_pickley_config(cfg)

    if not args.force and is_executable(bstrap.pickley_exe):
        v = run_program(bstrap.pickley_exe, "--version", dryrun=False, fatal=False)
        if v == bstrap.pickley_version:
            print(f"{short(bstrap.pickley_exe)} version {v} is already installed")
            sys.exit(0)

        if v and len(v) < 24:  # If long output -> old pickley is busted (stacktrace)
            print(f"Replacing older {PICKLEY} v{v}")

    venv_packager = args.venv_packager
    if venv_packager is None:
        venv_packager = "pip==21.3.1" if sys.version_info[:2] <= (3, 7) else "uv"

    pickley_venv = bstrap.pk_path / f"{PICKLEY}-{bstrap.pickley_version}"
    if venv_packager.startswith("pip"):
        needs_virtualenv = run_program(sys.executable, "-mvenv", "--clear", pickley_venv, fatal=False)
        if not needs_virtualenv and not DRYRUN:  # pragma: no cover, tricky to test, virtualenv fallback is on its way out
            needs_virtualenv = not is_executable(pickley_venv / "bin/pip")

        if needs_virtualenv:
            print("-mvenv failed, falling back to virtualenv")
            pv = "%s.%s" % (sys.version_info[0], sys.version_info[1])
            zipapp = bstrap.pk_path / f".cache/virtualenv-{pv}.pyz"
            ensure_folder(zipapp.parent)
            if not os.path.exists(zipapp):
                url = f"https://bootstrap.pypa.io/virtualenv/{pv}/virtualenv.pyz"
                download(zipapp, url)

            run_program(sys.executable, zipapp, "-q", "-p", sys.executable, pickley_venv)

        run_program(pickley_venv / "bin/pip", "-q", "install", "-U", venv_packager)
        run_program(pickley_venv / "bin/pip", "-q", "install", f"{PICKLEY}=={bstrap.pickley_version}")

    elif venv_packager == "uv":
        uv_path = bstrap.find_uv()
        run_program(uv_path, "-q", "venv", pickley_venv, env=uv_env(mirror=args.mirror, python=sys.executable))

        env = uv_env(mirror=args.mirror, venv=pickley_venv)
        run_program(uv_path, "-q", "pip", "install", f"{PICKLEY}=={bstrap.pickley_version}", env=env)

    else:
        abort(f"Unsupported venv packager '{venv_packager}', state `uv` or `pip`")

    run_program(pickley_venv / f"bin/{PICKLEY}", "base", "bootstrap-own-wrapper")


if __name__ == "__main__":
    main()
