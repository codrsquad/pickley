"""
Brew style python CLI installation
"""

import io
import logging
import os
import shutil
import subprocess  # nosec
import sys
import time

import six


LOG = logging.getLogger(__name__)
SECONDS_IN_ONE_MINUTE = 60
SECONDS_IN_ONE_HOUR = 60 * SECONDS_IN_ONE_MINUTE


def decode(value):
    """ Python 2/3 friendly decoding of output """
    if isinstance(value, bytes) and not isinstance(value, str):
        return value.decode("utf-8")
    return value


def short(path, base=None):
    """
    :param str path: Path to represent in its short form
    :param str|None base: Base folder to relativise paths to
    :return str: Short form, using '~' if applicable
    """
    if not path:
        return path
    if base:
        path = str(path).replace(base + "/", "")
    path = str(path).replace(system.HOME, "~")
    return path


def python_interpreter():
    """
    :return str: Path to python interpreter currently used
    """
    prefix = getattr(sys, "real_prefix", None)
    if prefix:
        return os.path.join(prefix, "bin", "python")
    else:
        return sys.executable


def pickley_program():
    """
    :return str: Path to pickley executable, with special case for test runs
    """
    path = sys.argv[0]
    path = "/dev/null/pytest" if "pycharm" in path.lower() else path
    return path


def is_test_run():
    """
    :return bool: True if we're running via pytest (or pycharm test)
    """
    return "pytest" in pickley_program().lower()


def relocate_venv_file(path, source, destination):
    """
    :param str path: Path of file to relocate (change mentions of 'source' to 'destination')
    :param str source: Where venv used to be
    :param str destination: Where venv is moved to
    :return bool: True if file with 'path' was modified
    """
    if not path or not os.path.isfile(path) or os.path.islink(path) or os.path.getsize(path) > 8192:
        # No need to relocate if symlink, or size bigger than 8k (binary)
        return False

    lines = []
    modified = False
    try:
        with io.open(path, "rt") as fh:
            for line in fh:
                if source in line:
                    line = line.replace(source, destination)
                    modified = True
                lines.append(line)

    except Exception as e:
        if "utf-8" in str(e):
            return False

        system.abort("Can't relocate venv file %s: %s", short(path), e)

    if not modified or not lines:
        return False

    try:
        with io.open(path, "wt") as fh:
            for line in lines:
                fh.write(line)

    except Exception as e:
        system.abort("Can't relocate venv file %s: %s", short(path), e)

    return True


class system:
    """
    Functionality for the whole app, easily importable via one name
    """

    DRYRUN = False
    OUTPUT = True
    QUIET = False
    PICKLEY = "pickley"
    DOT_PICKLEY = ".pickley"
    HOME = os.path.expanduser("~")
    PYTHON = python_interpreter()
    AUDIT_HANDLER = None
    DEBUG_HANDLER = None
    TESTING = is_test_run()
    PROGRAM = pickley_program()

    DEFAULT_CHANNEL = "latest"
    DEFAULT_DELIVERY = "symlink"
    DEFAULT_PACKAGER = "venv"

    CHECK_UPGRADE_DELAY = 10 * SECONDS_IN_ONE_MINUTE
    INSTALL_TIMEOUT = SECONDS_IN_ONE_HOUR

    @classmethod
    def debug(cls, message, *args, **kwargs):
        if not cls.QUIET:
            LOG.debug(message, *args, **kwargs)
        if cls.TESTING:
            print(str(message) % args)

    @classmethod
    def info(cls, message, *args, **kwargs):
        output = kwargs.pop("output", cls.OUTPUT)
        LOG.info(message, *args, **kwargs)
        if (not cls.QUIET and output) or cls.TESTING:
            print(str(message) % args)

    @classmethod
    def warning(cls, message, *args, **kwargs):
        LOG.warning(message, *args, **kwargs)
        if cls.OUTPUT or cls.TESTING:
            print("WARNING: %s" % (str(message) % args))

    @classmethod
    def error(cls, message, *args, **kwargs):
        LOG.error(message, *args, **kwargs)
        if cls.OUTPUT or cls.TESTING:
            print("ERROR: %s" % (str(message) % args))

    @classmethod
    def abort(cls, message, *args, **kwargs):
        cls.error(message, *args, **kwargs)
        sys.exit(1)

    @classmethod
    def config_paths(cls, testing):
        if testing:
            return [".pickley.json"]
        else:
            return ["~/.config/pickley.json", ".pickley.json"]

    @classmethod
    def touch(cls, path):
        """
        :param path: Path to file to touch
        """
        if path:
            if system.DRYRUN:
                cls.debug("Would touch %s", short(path))
                return
            cls.ensure_folder(path)
            with open(path, "at"):
                os.utime(path, None)

    @classmethod
    def resolved_path(cls, path, base=None):
        """
        :param str path: Path to resolve
        :param str|None base: Base folder to use for relative paths (default: current working dir)
        :return str: Absolute path
        """
        if not path:
            return path
        path = os.path.expanduser(path)
        if base and not os.path.isabs(path):
            return os.path.join(base, path)
        return os.path.abspath(path)

    @classmethod
    def parent_folder(cls, path, base=None):
        """
        :param str path: Path to file or folder
        :param str|None base: Base folder to use for relative paths (default: current working dir)
        :return str: Absolute path of parent folder of 'path'
        """
        return path and os.path.dirname(cls.resolved_path(path, base=base))

    @classmethod
    def first_line(cls, path):
        """
        :param str path: Path to file
        :return str|None: First line of file, if any
        """
        try:
            with io.open(path, "rt", errors="ignore") as fh:
                return fh.readline().strip()
        except Exception:
            return None

    @classmethod
    def to_str(cls, text):
        """Pex and pip want all their args to be str in python2"""
        if sys.version_info.major < 3:
            text = text.encode("ascii", "ignore")
        return text

    @classmethod
    def flatten(cls, result, value, separator=None, unique=True):
        """
        :param list result: Flattened values
        :param value: Possibly nested arguments (sequence of lists, nested lists)
        :param str|None separator: Split values with 'separator' if specified
        :param bool unique: If True, return unique values only
        """
        if not value:
            # Convenience: allow to filter out --foo None easily
            if value is None and not unique and result and result[-1].startswith("-"):
                result.pop(-1)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                cls.flatten(result, item, separator=separator, unique=unique)
            return
        if separator is not None and hasattr(value, "split") and separator in value:
            cls.flatten(result, value.split(separator), separator=separator, unique=unique)
            return
        if not unique or value not in result:
            if not unique:
                value = cls.to_str(value)
            result.append(value)

    @classmethod
    def flattened(cls, value, separator=None, unique=True):
        """
        :param value: Possibly nested arguments (sequence of lists, nested lists)
        :param str|None separator: Split values with 'separator' if specified
        :param bool unique: If True, return unique values only
        :return list: 'value' flattened out (leaves from all involved lists/tuples)
        """
        result = []
        cls.flatten(result, value, separator=separator, unique=unique)
        return result

    @classmethod
    def ensure_folder(cls, path, folder=False):
        """
        :param str path: Path to file or folder
        :param bool folder: If True, 'path' refers to a folder (file otherwise)
        """
        if not path:
            return
        if folder:
            folder = cls.resolved_path(path)
        else:
            folder = cls.parent_folder(path)
        if os.path.isdir(folder):
            return
        if cls.DRYRUN:
            cls.debug("Would create %s", short(folder))
            return
        try:
            os.makedirs(folder)
        except Exception as e:
            cls.error("Can't create folder %s: %s", short(folder), e)
            raise

    @classmethod
    def copy_file(cls, source, destination):
        """Copy source -> destination"""
        if source and destination:
            if cls.DRYRUN:
                cls.debug("Would copy %s -> %s", short(source), short(destination))
                return

            if not os.path.exists(source):
                cls.abort("%s does not exist, can't copy to %s", short(source), short(destination))

            cls.ensure_folder(destination)
            cls.delete_file(destination)

            if os.path.isdir(source):
                shutil.copytree(source, destination, symlinks=True)
            else:
                shutil.copy(source, destination)

            shutil.copystat(source, destination)  # Make sure last modification time is preserved

    @classmethod
    def move_file(cls, source, destination):
        """Move source -> destination"""
        if source and destination:
            if cls.DRYRUN:
                cls.debug("Would move %s -> %s", short(source), short(destination))
                return

            if not os.path.exists(source):
                cls.abort("%s does not exist, can't move to %s", short(source), short(destination))

            cls.ensure_folder(destination)
            cls.delete_file(destination)
            shutil.move(source, destination)

    @classmethod
    def move_venv(cls, source, destination):
        """
        :param str source: Folder where current venv is
        :param str destination: Folder where to move and relocate venv to
        """
        if source and destination and source != destination:
            if cls.DRYRUN:
                cls.debug("Would move venv %s -> %s", short(source), short(destination))
                return

            bin_folder = os.path.join(source, "bin")
            if not os.path.isdir(bin_folder):
                system.abort("No bin folder in venv %s, can't move to %s", short(source), short(destination))

            cls.debug("Moving venv %s -> %s", short(source), short(destination))
            for name in os.listdir(bin_folder):
                fpath = os.path.join(bin_folder, name)
                relocate_venv_file(fpath, source, destination)

            cls.move_file(source, destination)

    @classmethod
    def delete_file(cls, path):
        """ Delete file/folder with 'path' """
        islink = path and os.path.islink(path)
        if not islink and (not path or not os.path.exists(path)):
            return

        if cls.DRYRUN:
            cls.debug("Would delete %s", short(path))
            return

        cls.debug("Deleting %s", short(path))
        try:
            if islink or os.path.isfile(path):
                os.unlink(path)
            else:
                shutil.rmtree(path)

        except Exception as e:
            cls.error("Can't delete %s: %s", short(path), e)
            raise

    @classmethod
    def make_executable(cls, path):
        """
        :param str path: chmod file with 'path' as executable
        """
        if cls.is_executable(path):
            return

        if cls.DRYRUN:
            cls.debug("Would make %s executable", short(path))
            return

        if not os.path.exists(path):
            cls.abort("%s does not exist, can't make it executable", short(path))

        try:
            os.chmod(path, 0o755)  # nosec

        except Exception as e:
            cls.error("Can't chmod %s: %s", short(path), e)
            raise

    @classmethod
    def is_executable(cls, path):
        """
        :param str path: Path to file
        :return bool: True if file exists and is executable
        """
        return path and os.path.isfile(path) and os.access(path, os.X_OK)

    @classmethod
    def write_contents(cls, path, contents):
        """
        :param str path: Path to file
        :param str contents: Contents to write
        """
        if not path or not contents:
            return

        if cls.DRYRUN:
            cls.debug("Would write %s bytes to %s", len(contents), short(path))

        cls.ensure_folder(path)
        cls.debug("Writing %s bytes to %s", len(contents), short(path))
        with open(path, "wt") as fh:
            fh.write(contents)

    @classmethod
    def which(cls, program):
        """
        :param str program: Program name to find via env var PATH
        :return str|None: Full path to program, if one exists and is executable
        """
        if not program:
            return None
        if os.path.isabs(program):
            return cls.to_str(program) if cls.is_executable(program) else None
        for p in os.environ.get("PATH", "").split(":"):
            fp = os.path.join(p, program)
            if cls.is_executable(fp):
                return cls.to_str(fp)
        return None

    @classmethod
    def run_program(cls, program, *args, **kwargs):
        """Run 'program' with 'args'"""
        args = cls.flattened(args, unique=False)
        full_path = cls.which(program)

        fatal = kwargs.pop("fatal", True)
        logger = kwargs.pop("logger", cls.debug)
        dryrun = fatal and cls.DRYRUN
        message = "Would run" if dryrun else "Running"
        message = "%s: %s %s" % (message, short(full_path or program), cls.represented_args(args))
        logger(message)

        if dryrun:
            return message

        if not full_path:
            if fatal:
                cls.abort("%s is not installed", program)
            return None

        stdout = kwargs.pop("stdout", subprocess.PIPE)
        stderr = kwargs.pop("stderr", subprocess.PIPE)
        args = [full_path] + args
        try:
            p = subprocess.Popen(args, stdout=stdout, stderr=stderr)  # nosec
            output, error = p.communicate()
            output = decode(output)
            error = decode(error)
            if output:
                output = output.strip()
            if error:
                error = error.strip()

            if p.returncode:
                if fatal:
                    info = ": %s\n%s" % (error, output) if output or error else ""
                    cls.abort("%s exited with code %s%s", program, p.returncode, info)
                return None

            return output

        except Exception as e:
            system.abort("%s failed: %s", os.path.basename(program), e, exc_info=e)

    @classmethod
    def quoted(cls, text):
        """
        :param str text: Text to optionally quote
        :return str: Quoted if 'text' contains spaces
        """
        if text and " " in text:
            sep = "'" if '"' in text else '"'
            return "%s%s%s" % (sep, text, sep)
        return text

    @classmethod
    def represented_args(cls, args, base=None, separator=" ", shorten=True):
        """
        :param list|tuple args: Arguments to represent
        :param str|None base: Base folder to relativise paths to
        :param str separator: Separator to use
        :param bool shorten: If True, shorten involved paths
        :return str: Quoted as needed textual representation
        """
        result = []
        for text in args:
            if shorten:
                text = short(text, base=base)
            result.append(cls.quoted(text))
        return separator.join(result)


class ImplementationMap:
    """
    Keep track of implementations by name, configurable via settings
    """

    def __init__(self, settings, key):
        """
        :param pickley.settings.Settings: Settings to use
        :param str key: Key in setting where to lookup default to use
        """
        self.key = key
        self.settings = settings
        self.map = {}

    def register(self, implementation, name=None):
        """
        :param type implementation: Class to register
        :param str|None name: Name to register as
        """
        if not name:
            if hasattr(implementation, "class_implementation_name"):
                name = implementation.class_implementation_name()
            else:
                name = implementation.__name__
        self.map[name.lower()] = implementation
        return implementation

    def get(self, name):
        """
        :param str name: Name of implementation
        :return: Registered implementation, if any
        """
        return self.map.get(name and name.lower())

    def names(self):
        """
        :return list(str): Registered names
        """
        return sorted(self.map.keys())

    def resolved(self, package_name):
        """
        :param str package_name: Name of pypi package
        :return: Corresponding implementation to use
        """
        definition = self.settings.resolved_definition(self.key, package_name=package_name)
        if not definition or not definition.value:
            system.abort("No %s type configured for %s", self.key, package_name)

        implementation = self.get(definition.value)
        if not implementation:
            system.abort("Unknown %s type '%s'", self.key, definition.value)

        return implementation(package_name)


class CurrentFolder:
    """Context manager for changing the current working directory"""

    def __init__(self, destination):
        self.destination = system.resolved_path(destination)

    def __enter__(self):
        self.current_folder = os.getcwd()
        os.chdir(self.destination)

    def __exit__(self, *_):
        os.chdir(self.current_folder)


class PingLockException(Exception):
    """Raised when ping lock can't be acquired"""

    def __init__(self, ping_path):
        self.ping_path = ping_path


class PingLock:
    """
    Allows to manage .work/ folder with a .ping lock file
    Several pickley processes could be attempting to auto upgrade a package at the same time
    With this class, we make it so:
    - first process "grabs a lock" via a .ping file (lock based on existence of file, and its age)
    - lock consists of creating a .work/.ping file, and deleting .work/ folder once installation completes
    - other processes will avoid trying their own upgrade during that time
    - the lock remains valid for an hour, after that previous upgrade attempt is considered failed (lock re-acquired)
    """

    def __init__(self, folder, seconds=system.INSTALL_TIMEOUT):
        """
        :param str folder: Target installation folder (<base>/.pickley/<name>/.work)
        :param float seconds: Number of seconds ping file is valid for (default: 1 hour)
        """
        self.folder = folder
        self.seconds = seconds
        self.ping = os.path.join(self.folder, ".ping")

    def is_young(self, seconds=None):
        """
        :param float|None seconds: Number of seconds .ping is considered young (default: self.seconds)
        :return bool: True if .ping file exists, and is younger than 'seconds'
        """
        if not os.path.exists(self.ping):
            return False
        mtime = os.path.getmtime(self.ping)
        if seconds is None:
            seconds = self.seconds
        cutoff = time.time() - seconds
        return mtime >= cutoff

    def touch(self):
        """Touch the .ping file"""
        system.touch(self.ping)

    def __enter__(self):
        """
        Grab a folder/.ping lock if possible, raise PingLockException if not
        """
        if self.is_young():
            raise PingLockException(self.ping)
        system.delete_file(self.folder)
        self.touch()
        return self

    def __exit__(self, *_):
        """
        Delete folder (with its .ping file)
        """
        system.delete_file(self.folder)


class CaptureOutput:
    """
    Context manager allowing to temporarily grab stdout/stderr output.
    Output is captured and made available only for the duration of the context.

    Sample usage:

    with CaptureOutput() as logged:
        ... do something that generates output ...
        assert "some message" in logged
    """

    def __init__(self, folder=None, stdout=True, stderr=True, env=None, dryrun=None):
        """
        :param str|None folder: Change cwd to 'folder' when provided
        :param bool stdout: Capture stdout
        :param bool stderr: Capture stderr
        :param dict|None env: Customize PATH-like env vars when provided
        :param bool|None dryrun: Switch dryrun when provided
        """
        self.current_folder = os.getcwd()
        self.folder = folder
        self.env = env
        self.dryrun = dryrun
        self.old_env = {}
        self.old_out = sys.stdout
        self.old_err = sys.stderr
        self.old_handlers = logging.root.handlers

        self.out_buffer = six.StringIO() if stdout else self.old_out
        self.err_buffer = six.StringIO() if stderr else self.old_err

        self.handler = logging.StreamHandler(stream=self.err_buffer)
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))

    def __repr__(self):
        result = ""
        if self.out_buffer:
            result += decode(self.out_buffer.getvalue())
        if self.err_buffer:
            result += decode(self.err_buffer.getvalue())
        return result

    def __enter__(self):
        if self.folder:
            system.ensure_folder(self.folder, folder=True)

        self.old_env = {}
        for key, value in os.environ.items():
            self.old_env[key] = os.environ.get(key)

        if self.env:
            for key, value in self.env.items():
                if value:
                    if value != os.environ.get(key):
                        system.debug("Customizing env %s=%s", key, value)
                        os.environ[key] = value
                elif key in os.environ:
                    system.debug("Removing env %s", key)
                    del os.environ[key]

        sys.stdout = self.out_buffer
        sys.stderr = self.err_buffer
        logging.root.handlers = [self.handler]

        if self.dryrun is not None:
            (system.DRYRUN, self.dryrun) = (bool(self.dryrun), bool(system.DRYRUN))

        return self

    def __exit__(self, *args):
        sys.stdout = self.old_out
        sys.stderr = self.old_err
        self.out_buffer = None
        self.err_buffer = None
        logging.root.handlers = self.old_handlers

        for key in list(os.environ.keys()):
            if key not in self.old_env:
                system.debug("Cleaning up env %s", key)
                del os.environ[key]

        for key, value in self.old_env.items():
            if value != os.environ.get(key):
                system.debug("Restoring env %s=%s", key, value)
                os.environ[key] = value

        if self.dryrun is not None:
            system.DRYRUN = self.dryrun

    def __contains__(self, item):
        return item is not None and item in str(self)
