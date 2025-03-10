"""
This script is designed to be run standalone, and will bootstrap pickley in a given base folder.

Usage example:
    /usr/bin/python3 bstrap.py --base ~/.local/bin --mirror https://example.org/pypi
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

expanduser = os.path.expanduser  # Overridden in conftest.py, to ensure tests never look at `~`
DEFAULT_BASE = "~/.local/bin"
DOT_META = ".pk"
DRYRUN = False
VERBOSITY = 0
HOME = expanduser("~")
PICKLEY = "pickley"
PIP_CONFS = ("~/.config/pip/pip.conf", "/etc/pip.conf")
DEFAULT_MIRROR = "https://pypi.org/simple"
CURRENT_PYTHON_MM = sys.version_info[:2]
UV_CUTOFF = (3, 8)
USE_UV = CURRENT_PYTHON_MM >= UV_CUTOFF  # Default to `uv` for python versions >= this
KNOWN_ENTRYPOINTS = {PICKLEY: (PICKLEY,), "tox": ("tox",), "uv": ("uv", "uvx")}


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
    def __init__(self, args):
        self.mirror = _groomed_mirror_url(args.mirror)
        self.package_manager = args.package_manager or default_package_manager()
        if self.package_manager not in ("uv", "pip"):
            Reporter.abort(f"Unsupported package manager '{self.package_manager}', state 'uv' or 'pip'")

        self.pickley_base = Path(find_base(args.base)).absolute()
        self.pickley_spec = args.pickley_spec
        if self.mirror:
            seed_mirror(self.mirror, "~/.config/pip/pip.conf", "global")
            seed_mirror(self.mirror, "~/.config/uv/uv.toml", "pip")

        else:
            self.mirror, _ = globally_configured_pypi_mirror()

        set_mirror_env_vars(self.mirror)

    def seed_pickley_config(self, desired_cfg):
        pickley_config = self.pickley_base / DOT_META / "config.json"
        if not pickley_config.exists():
            msg = f"{short(pickley_config)} with {desired_cfg}"
            if not hdry(f"Would seed {msg}"):
                Reporter.inform(f"Seeding {msg}")
                ensure_folder(pickley_config.parent)
                payload = json.dumps(desired_cfg, sort_keys=True, indent=2)
                pickley_config.write_text(f"{payload}\n")

    def bootstrap_pickley(self):
        """Run `pickley bootstrap` in a temporary venv"""
        # Venv in .cache/ will be auto-cleaned up after 24 hours, leaving it there as it can be useful for inspection (if bootstrap fails)
        venv_folder = self.pickley_base / DOT_META / ".cache/pickley-bootstrap-venv"
        if self.package_manager == "pip":
            self.bootstrap_pickley_with_pip(venv_folder)

        else:
            self.bootstrap_pickley_with_uv(venv_folder)

        args = []
        if VERBOSITY > 0:
            vv = "v" * VERBOSITY
            args.append(f"-{vv}")

        args.append("bootstrap")
        args.append(self.pickley_base)
        if self.pickley_spec:
            # Not explicitly stating pickley spec to use makes bootstrap use previous authoritative spec
            args.append(self.pickley_spec)

        run_program(venv_folder / f"bin/{PICKLEY}", *args)

    def bootstrap_pickley_with_uv(self, venv_folder: Path, uv_path=None):
        if uv_path is None:
            uv_bootstrap = UvBootstrap(self.pickley_base)
            uv_bootstrap.auto_bootstrap_uv()
            uv_path = uv_bootstrap.uv_path

        run_program(uv_path, "venv", "-p", sys.executable, venv_folder)
        env = dict(os.environ)
        env["VIRTUAL_ENV"] = venv_folder
        args = []
        if self.pickley_spec and self.pickley_spec.startswith("/"):
            # Testing or troubleshooting: bootstrapping pickley from a local folder checkout
            args.append("-e")

        args.append(self.pickley_spec or PICKLEY)
        run_program(uv_path, "-q", "pip", "install", *args, env=env)

    def bootstrap_pickley_with_pip(self, venv_folder: Path):
        pip = venv_folder / "bin/pip"
        needs_virtualenv = run_program(sys.executable, "-mvenv", "--clear", venv_folder, fatal=False)
        if not needs_virtualenv and not DRYRUN:
            needs_virtualenv = not is_executable(pip)

        if needs_virtualenv:  # pragma: no cover, not testing py3.6 fallback anymore
            Reporter.inform("-mvenv failed, falling back to virtualenv")
            pv = ".".join(str(x) for x in CURRENT_PYTHON_MM)
            zipapp = venv_folder.parent / ".cache/virtualenv.pyz"
            if not zipapp.exists():
                url = f"https://bootstrap.pypa.io/virtualenv/{pv}/virtualenv.pyz"
                download(zipapp, url)

            run_program(sys.executable, zipapp, "-q", "-p", sys.executable, venv_folder)

        run_program(pip, "-q", "install", "-U", *pip_auto_upgrade())
        run_program(pip, "-q", "install", self.pickley_spec or PICKLEY)


def default_package_manager(*parts):
    """Decide which package manager to use by default"""
    if not parts:
        parts = CURRENT_PYTHON_MM

    return "uv" if parts >= UV_CUTOFF else "pip"


class UvBootstrap:
    """Download uv from official releases"""

    def __init__(self, pickley_base):
        self.pickley_base = pickley_base
        self.uv_path = pickley_base / "uv"
        self.freshly_bootstrapped = None  # Set by auto_bootstrap_uv, when a bootstrap was needed

    def auto_bootstrap_uv(self):
        self.freshly_bootstrapped = self.bootstrap_reason()
        if self.freshly_bootstrapped:
            Reporter.inform(f"Auto-bootstrapping uv, reason: {self.freshly_bootstrapped}")
            uv_tmp = self.download_uv()
            shutil.move(uv_tmp / "uv", self.pickley_base / "uv")
            shutil.move(uv_tmp / "uvx", self.pickley_base / "uvx")
            shutil.rmtree(uv_tmp, ignore_errors=True)

            # Touch cooldown file to let pickley know no need to check for uv upgrade for a while
            cooldown_relative_path = f"{DOT_META}/.cache/uv.cooldown"
            cooldown_path = self.pickley_base / cooldown_relative_path
            ensure_folder(cooldown_path.parent, dryrun=False)
            cooldown_path.write_text("")
            Reporter.debug(f"[bootstrap] Touched {cooldown_relative_path}")

            # Let pickley know which version of uv is installed
            uv_version = run_program(self.uv_path, "--version", fatal=False, dryrun=False)
            if uv_version:
                m = re.search(r"(\d+\.\d+\.\d+)", uv_version)
                if m:
                    uv_version = m.group(1)
                    manifest_relative_path = f"{DOT_META}/.manifest/uv.manifest.json"
                    manifest_path = self.pickley_base / manifest_relative_path
                    manifest = {
                        "entrypoints": KNOWN_ENTRYPOINTS["uv"],
                        "tracked_settings": {"auto_upgrade_spec": "uv"},
                        "version": uv_version,
                    }
                    ensure_folder(manifest_path.parent, dryrun=False)
                    manifest_path.write_text(json.dumps(manifest))
                    Reporter.debug(f"[bootstrap] Saved {manifest_relative_path}")

    def bootstrap_reason(self):
        if not self.uv_path.exists():
            return "uv not present"

        if not is_executable(self.uv_path) or self.uv_path.is_symlink():
            return "invalid uv file"

        if os.path.getsize(self.uv_path) < 50000:
            # Small size means previous iteration wrapper, we want the real uv
            return "replacing uv wrapper"

    @staticmethod
    def uv_url(version):
        if version:
            return f"https://github.com/astral-sh/uv/releases/download/{version}/uv-installer.sh"

        return "https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh"

    def download_uv(self, version=None, dryrun=False):
        ts = int(time.time() * 1000)  # Avoid dealing with cache issues, .pk/.cache is auto cleaned once per day
        uv_tmp = self.pickley_base / DOT_META / f".cache/uv-{ts}"
        script = uv_tmp / ".uv-installer.sh"
        url = self.uv_url(version)
        download(script, url, dryrun=dryrun)
        env = dict(os.environ)
        env["CARGO_DIST_FORCE_INSTALL_DIR"] = str(uv_tmp)
        env["INSTALLER_NO_MODIFY_PATH"] = "1"
        env["INSTALLER_PRINT_QUIET"] = "1"
        env["UV_UNMANAGED_INSTALL"] = str(uv_tmp)  # See https://github.com/astral-sh/uv/issues/6965#issuecomment-2448300149
        env.setdefault("HOME", str(uv_tmp))  # uv's installer assumes HOME is always defined (it is not on some CI systems)
        run_program("/bin/sh", script, env=env, dryrun=dryrun)
        return uv_tmp


def built_in_download(target, url):
    request = Request(url)
    response = urlopen(request, timeout=10)
    target.write_bytes(response.read())


def clean_env_vars(keys=("__PYVENV_LAUNCHER__", "CLICOLOR_FORCE", "PYTHONPATH")):
    """
    Clean up any problematic env vars.

    For __PYVENV_LAUNCHER__: see https://github.com/python/cpython/pull/9516
    CLICOLOR_FORCE: it forces `uv` to do colored output, which is problematic when parsing output of `uv pip show` for example
    """
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
        ensure_folder(target.parent, dryrun=dryrun)
        try:
            return built_in_download(target, url)

        except Exception:
            Reporter.inform(f"Built-in download of {url} failed, trying curl or wget")
            return curl_download(target, url)


def ensure_folder(path, dryrun=None):
    if path and not path.is_dir() and not hdry(f"Would create {short(path)}", dryrun=dryrun):
        Reporter.trace(f"Creating folder {short(path)}")
        os.makedirs(path)


def find_base(base):
    candidates = base.split(os.pathsep)
    for c in candidates:
        c = expanduser(c)
        if c and os.path.isdir(c) and is_writable(c):
            return c

    Reporter.abort(f"Make sure '{candidates[0]}' exists and is writeable.")


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
            config.read(expanduser(pip_conf_path))
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
        Reporter.inform(f"Running: {description}")
        if fatal:
            stdout = stderr = None

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
        config_path = Path(expanduser(path))
        if not config_path.exists():
            ensure_folder(config_path.parent)
            msg = f"{short(config_path)} with {mirror}"
            if not hdry(f"Would seed {msg}"):
                Reporter.inform(f"Seeding {msg}")
                if section == "pip" and not mirror.startswith('"'):
                    # This assumes user passed a reasonable URL as --mirror, no further validation is done
                    # We only ensure the URL is quoted, as uv.toml requires it
                    mirror = f'"{mirror}"'

                config_path.write_text(f"[{section}]\nindex-url = {mirror}\n")

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
    parser.add_argument("pickley_spec", nargs="?", help="Spec to use (default: 'pickley')")
    args = parser.parse_args(args=args)

    VERBOSITY = args.verbose
    DRYRUN = args.dryrun
    clean_env_vars()
    bstrap = Bootstrap(args)
    message = f"Using {short(sys.executable)}, base: {short(bstrap.pickley_base)}"
    if bstrap.mirror and bstrap.mirror != DEFAULT_MIRROR:
        message += f", mirror: {bstrap.mirror}"

    Reporter.inform(message)
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

    bstrap.bootstrap_pickley()


if __name__ == "__main__":
    main()
