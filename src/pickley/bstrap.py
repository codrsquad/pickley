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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE = "~/.local/bin"
DOT_META = ".pk"
DRYRUN = False
VERBOSITY = 0
HOME = os.path.expanduser("~")
PICKLEY = "pickley"
PIP_CONFS = ("~/.config/pip/pip.conf", "/etc/pip.conf")
DEFAULT_MIRROR = "https://pypi.org/simple"
CURRENT_PYTHON_MM = sys.version_info[:2]
UV_CUTOFF = (3, 7)
USE_UV = CURRENT_PYTHON_MM >= UV_CUTOFF  # Default to `uv` for python versions >= this
_UV_PATH = None


class _Reporter:
    @staticmethod
    def abort(message):
        sys.exit(f"--------\n\n{message}\n\n--------")

    @staticmethod
    def trace(message):
        if VERBOSITY > 1:
            print(message)

    @staticmethod
    def debug(message):
        if VERBOSITY > 0:
            print(message)

    @staticmethod
    def inform(message):
        print(message)


Reporter = _Reporter


def set_mirror_env_vars(mirror):
    if mirror and mirror != DEFAULT_MIRROR:
        Reporter.trace(f"Setting PIP_INDEX_URL and UV_INDEX_URL to {mirror}")
        os.environ["PIP_INDEX_URL"] = mirror
        os.environ["UV_INDEX_URL"] = mirror


class Bootstrap:
    def __init__(self, pickley_base, mirror):
        self.pickley_base = Path(pickley_base)
        if mirror:
            seed_mirror(mirror, "~/.config/pip/pip.conf", "global")
            seed_mirror(mirror, "~/.config/uv/uv.toml", "pip")

        else:
            mirror, _ = globally_configured_pypi_mirror()

        self.mirror = mirror
        if mirror and mirror != DEFAULT_MIRROR:
            os.environ["PIP_INDEX_URL"] = mirror
            os.environ["UV_INDEX_URL"] = mirror

    def seed_pickley_config(self, desired_cfg):
        pickley_config = self.pickley_base / DOT_META / "config.json"
        if not pickley_config.exists():
            msg = f"{short(pickley_config)} with {desired_cfg}"
            if not hdry(f"Would seed {msg}"):
                Reporter.inform(f"Seeding {msg}")
                ensure_folder(os.path.dirname(pickley_config))
                with open(pickley_config, "wt") as fh:
                    json.dump(desired_cfg, fh, sort_keys=True, indent=2)
                    fh.write("\n")

    def get_latest_pickley_version(self):
        # This is a temporary measure, eventually we'll use `uv describe` for this
        url = os.path.dirname(self.mirror)
        url = f"{url}/pypi/{PICKLEY}/json"
        data = http_get(url)
        if data:
            data = json.loads(data)
            version = data["info"]["version"]
            Reporter.debug(f"Latest {PICKLEY} version: {version}")
            return version


def default_package_manager(*parts):
    if not parts:
        parts = CURRENT_PYTHON_MM

    return "uv" if parts >= UV_CUTOFF else "pip"


def find_uv(pickley_base):
    """Find path to `uv` to use during this run."""
    global _UV_PATH

    if _UV_PATH is None:
        _UV_PATH = pickley_base / "uv"
        if is_executable(_UV_PATH):
            v = run_program(_UV_PATH, "--version", dryrun=False, fatal=False)
            if v and len(v) < 64 and v.startswith("uv "):
                # `<pickley-base>/uv` is available
                Reporter.trace(f"Using {short(_UV_PATH)}")
                return _UV_PATH

        # For bootstrap, download uv in <pickley-base>/.pk/.uv/bin/uv
        # It will later get properly wrapped (<pickley-base>/uv -> .pk/uv-<version>/bin/uv) by `pickley base bootstrap-own-wrapper`
        uv_tmp_target = pickley_base / DOT_META / ".uv"
        _UV_PATH = uv_tmp_target / "bin/uv"
        Reporter.trace(f"Using {short(_UV_PATH)}")
        if not is_executable(_UV_PATH):
            download_uv(uv_tmp_target)

    return _UV_PATH


def uv_url(version):
    if version:
        return f"https://github.com/astral-sh/uv/releases/download/{version}/uv-installer.sh"

    return "https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh"


def download_uv(target, version=None, dryrun=None):
    ensure_folder(target, dryrun=dryrun)
    script = os.path.join(target, ".uv-installer.sh")
    url = uv_url(version)
    download(script, url, dryrun=dryrun)
    env = dict(os.environ)
    env["CARGO_DIST_FORCE_INSTALL_DIR"] = str(target)
    env["INSTALLER_NO_MODIFY_PATH"] = "1"
    env["INSTALLER_PRINT_QUIET"] = "1"
    env.setdefault("HOME", str(target))  # uv's installer unfortunately assumes HOME is always defined (it is not in tox tests)
    run_program("/bin/sh", script, env=env, dryrun=dryrun)


def http_get(url, timeout=10):
    Reporter.trace(f"Querying {url}")
    try:
        request = Request(url)
        with urlopen(request, timeout=timeout) as response:
            data = response.read()

    except HTTPError as e:
        if e.code == 404:
            return None

        Reporter.abort(f"Failed to fetch {url}: {e}")

    except URLError as e:  # py3.6 ssl error
        if "ssl" not in str(e).lower():
            Reporter.abort(f"Failed to fetch {url}: {e}")

        import tempfile

        with tempfile.NamedTemporaryFile() as tmpf:
            tmpf.close()
            curl_download(tmpf.name, url, dryrun=False)
            with open(tmpf.name, "rb") as fh:
                data = fh.read()

    except Exception as e:
        Reporter.abort(f"Failed to fetch {url}: {e}")

    if data:
        data = data.decode("utf-8").strip()

    return data


def built_in_download(target, url):
    request = Request(url)
    response = urlopen(request, timeout=10)
    with open(target, "wb") as fh:
        fh.write(response.read())


def clean_env_vars(keys=("__PYVENV_LAUNCHER__", "PYTHONPATH")):
    """See https://github.com/python/cpython/pull/9516"""
    for key in keys:
        if key in os.environ:
            Reporter.trace(f"Unsetting env var {key}")
            del os.environ[key]


def curl_download(target, url, dryrun=None):
    curl = which("curl")
    if curl:
        return run_program(curl, "-fsSL", "-o", target, url, dryrun=dryrun)

    wget = which("wget")
    if wget:
        return run_program(wget, "-q", "-O", target, url, dryrun=dryrun)

    Reporter.abort(f"No `curl` nor `wget`, can't download {url} to '{target}'")


def download(target, url, dryrun=None):
    if not hdry(f"Would download {url}", dryrun=dryrun):
        ensure_folder(os.path.dirname(target), dryrun=dryrun)
        try:
            return built_in_download(target, url)

        except Exception:
            Reporter.inform(f"Built-in download of {url} failed, trying curl or wget")
            return curl_download(target, url)


def ensure_folder(path, dryrun=None):
    if path and not os.path.isdir(path) and not hdry(f"Would create {short(path)}", dryrun=dryrun):
        Reporter.trace(f"Creating folder {short(path)}")
        os.makedirs(path)


def find_base(base):
    candidates = base.split(os.pathsep)
    for c in candidates:
        c = os.path.expanduser(c)
        if c and os.path.isdir(c) and is_writable(c):
            return c

    Reporter.abort(f"Make sure '{candidates[0]}' is writeable.")


def _groomed_mirror_url(mirror):
    if isinstance(mirror, str):
        return mirror.rstrip("/")


def globally_configured_pypi_mirror(paths=None):
    """Configured pypi index from pip.conf"""
    if paths is None:
        paths = PIP_CONFS

    for pip_conf_path in paths:
        try:
            import configparser

            config = configparser.ConfigParser()
            config.read(os.path.expanduser(pip_conf_path))
            mirror = _groomed_mirror_url(config["global"]["index-url"])
            if mirror:
                return mirror, Path(pip_conf_path)

        except (KeyError, OSError):
            continue

        except Exception as e:
            # Ignore any issue reading pip.conf, not necessary for bootstrap
            Reporter.inform(f"Could not read '{pip_conf_path}': {e}")
            continue

    return DEFAULT_MIRROR, None


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


def run_program(program, *args, **kwargs):
    fatal = kwargs.pop("fatal", True)
    description = " ".join(short(x) for x in args)
    description = f"{short(program)} {description}"
    if not hdry(f"Would run: {description}", dryrun=kwargs.pop("dryrun", None)):
        if fatal:
            stdout = stderr = None
            Reporter.inform(f"Running: {description}")

        else:
            stdout = stderr = subprocess.PIPE

        p = subprocess.Popen([program, *args], stdout=stdout, stderr=stderr, env=kwargs.pop("env", None))
        if fatal:
            p.wait()
            if p.returncode:
                Reporter.abort(f"'{short(program)}' exited with code {p.returncode}")

            return p.returncode

        output, _ = p.communicate()
        if output is not None:
            output = output.decode("utf-8").strip()

        return None if p.returncode else output


def seed_mirror(mirror, path, section):
    try:
        config_path = os.path.expanduser(path)
        if not os.path.exists(config_path):
            ensure_folder(os.path.dirname(config_path))
            msg = f"{short(config_path)} with {mirror}"
            if not hdry(f"Would seed {msg}"):
                Reporter.inform(f"Seeding {msg}")
                with open(config_path, "wt") as fh:
                    if section == "pip" and not mirror.startswith('"'):
                        # This assumes user passed a reasonable URL as --mirror, no further validation is done
                        # We only ensure the URL is quoted, as uv.toml requires it
                        mirror = f'"{mirror}"'

                    fh.write(f"[{section}]\nindex-url = {mirror}\n")

    except Exception as e:
        Reporter.inform(f"Seeding {path} failed: {e}")


def short(text):
    return str(text).replace(HOME, "~")


def which(program):
    prefix_bin = os.path.join(sys.prefix, "bin")
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p != prefix_bin:
            fp = os.path.join(p, program)
            if fp and is_executable(fp):
                return Path(fp)


def pip_auto_upgrade():
    if CURRENT_PYTHON_MM == (3, 6):
        # Some ancient pip versions fail to upgrade themselves properly, use last known good version explicitly
        return "pip==21.3.1", "setuptools==59.6.0"

    if CURRENT_PYTHON_MM >= (3, 12):
        return ("pip",)

    return "pip", "setuptools"


def main(args=None):
    """Bootstrap pickley"""
    global VERBOSITY
    global DRYRUN

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--verbose", "-v", action="count", default=0, help="Use verbose output")
    parser.add_argument("--dryrun", "-n", action="store_true", help="Perform a dryrun")
    parser.add_argument("--base", "-b", default=DEFAULT_BASE, help="Base folder to use (default: ~/.local/bin)")
    parser.add_argument("--check-path", action="store_true", help="Verify that stated --base is on PATH env var")
    parser.add_argument("--cfg", "-c", help="Seed pickley config with given contents (file or serialized json)")
    parser.add_argument("--force", "-f", action="store_true", help="Force bootstrap (even if already done)")
    parser.add_argument("--mirror", "-m", help="Seed pypi mirror in pip.conf")
    parser.add_argument("--package-manager", help="Package manager to use (default: `uv` latest version)")
    parser.add_argument("version", nargs="?", help="Version to bootstrap (default: latest)")
    args = parser.parse_args(args=args)

    VERBOSITY = args.verbose
    DRYRUN = args.dryrun
    clean_env_vars()
    bstrap = Bootstrap(find_base(args.base), _groomed_mirror_url(args.mirror))
    message = f"Using {short(sys.executable)}, base: {short(bstrap.pickley_base)}"
    if bstrap.mirror:
        message += f", mirror: {bstrap.mirror}"

    Reporter.inform(message)
    pickley_version = args.version or bstrap.get_latest_pickley_version()
    if not pickley_version:
        Reporter.abort(f"Failed to determine latest {PICKLEY} version")

    if args.check_path:
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        if bstrap.pickley_base not in path_dirs:
            Reporter.abort(f"Make sure '{bstrap.pickley_base}' is in your PATH environment variable.")

    if args.cfg:
        if not args.cfg.startswith("{") or not args.cfg.endswith("}"):
            Reporter.abort(f"--config must be a serialized json object, invalid json: {args.cfg}")

        cfg = json.loads(args.cfg)
        if cfg and isinstance(cfg, dict):
            bstrap.seed_pickley_config(cfg)

    pickley_exe = bstrap.pickley_base / PICKLEY
    if not args.force and is_executable(pickley_exe):
        v = run_program(pickley_exe, "--version", dryrun=False, fatal=False)
        if v == pickley_version:
            Reporter.inform(f"{short(pickley_exe)} version {v} is already installed")
            sys.exit(0)

        if v and len(v) < 16:  # If long output -> old pickley is busted (stacktrace)
            Reporter.inform(f"Replacing older {PICKLEY} v{v}")

    package_manager = args.package_manager or os.getenv("PICKLEY_PACKAGE_MANAGER") or default_package_manager()
    pickley_venv = bstrap.pickley_base / DOT_META / f"{PICKLEY}-{pickley_version}"
    if package_manager == "pip":
        needs_virtualenv = run_program(sys.executable, "-mvenv", "--clear", pickley_venv, fatal=False)
        if not needs_virtualenv and not DRYRUN:  # pragma: no cover, tricky to test, virtualenv fallback is on its way out
            needs_virtualenv = not is_executable(pickley_venv / "bin/pip")

        if needs_virtualenv:
            Reporter.inform("-mvenv failed, falling back to virtualenv")
            pv = ".".join(str(x) for x in CURRENT_PYTHON_MM)
            zipapp = bstrap.pickley_base / DOT_META / f".cache/virtualenv-{pv}.pyz"
            ensure_folder(zipapp.parent)
            if not zipapp.exists():
                url = f"https://bootstrap.pypa.io/virtualenv/{pv}/virtualenv.pyz"
                download(zipapp, url)

            run_program(sys.executable, zipapp, "-q", "-p", sys.executable, pickley_venv)

        run_program(pickley_venv / "bin/pip", "-q", "install", "-U", *pip_auto_upgrade())
        run_program(pickley_venv / "bin/pip", "-q", "install", f"{PICKLEY}=={pickley_version}")

    elif package_manager == "uv":
        uv_path = find_uv(bstrap.pickley_base)
        run_program(uv_path, "-q", "venv", "-p", sys.executable, pickley_venv)
        env = dict(os.environ)
        env["VIRTUAL_ENV"] = pickley_venv
        run_program(uv_path, "-q", "pip", "install", f"{PICKLEY}=={pickley_version}", env=env)

    else:
        Reporter.abort(f"Unsupported package manager '{package_manager}', state `uv` or `pip`")

    run_program(pickley_venv / f"bin/{PICKLEY}", "base", "bootstrap-own-wrapper")


if __name__ == "__main__":
    main()
