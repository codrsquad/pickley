"""
Brew style python CLI installation
"""

import logging
import os
import shutil
import subprocess   # nosec
import sys

import six


LOG = logging.getLogger(__name__)
SECONDS_IN_ONE_MINUTE = 60
SECONDS_IN_ONE_HOUR = 60 * SECONDS_IN_ONE_MINUTE
SECONDS_IN_ONE_DAY = 24 * SECONDS_IN_ONE_HOUR


def decode(value):
    """ Python 2/3 friendly decoding of output """
    if isinstance(value, bytes) and not isinstance(value, str):
        return value.decode('utf-8')
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


class system:
    """
    Functionality for the whole app, easily importable via one name
    """

    DRYRUN = False
    PRINT = True
    QUIET = False
    PICKLEY = "pickley"
    DOT_PICKLEY = ".pickley"
    HOME = os.path.expanduser('~')

    if hasattr(sys, "real_prefix"):
        PYTHON = os.path.join(sys.real_prefix, "bin", "python")
    else:
        PYTHON = sys.executable

    @classmethod
    def debug(cls, message, *args, **kwargs):
        if not cls.QUIET:
            LOG.debug(message, *args, **kwargs)

    @classmethod
    def info(cls, message, *args, **kwargs):
        LOG.info(message, *args, **kwargs)
        if not cls.QUIET and cls.PRINT:
            print(str(message) % args)

    @classmethod
    def warning(cls, message, *args, **kwargs):
        LOG.warning(message, *args, **kwargs)
        if cls.PRINT:
            print("WARNING: %s" % (str(message) % args))

    @classmethod
    def error(cls, message, *args, **kwargs):
        LOG.error(message, *args, **kwargs)
        if cls.PRINT:
            print("ERROR: %s" % (str(message) % args))

    @classmethod
    def abort(cls, message, *args, **kwargs):
        cls.error(message, *args, **kwargs)
        sys.exit(1)

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
    def to_str(cls, text):
        """Pex and pip want all their args to be str in python2"""
        if sys.version_info.major < 3:
            text = text.encode('ascii', 'ignore')
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
            cls.debug("Would create %s", short(path))
            return
        try:
            os.makedirs(folder)
        except Exception as e:
            cls.error("Can't create folder %s: %s", short(folder), e)
            raise

    @classmethod
    def delete_file(cls, path):
        """ Delete file/folder with 'path' """
        islink = os.path.islink(path)
        if not islink and not os.path.exists(path):
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
            cls.error("%s does not exist, can't make it executable", short(path))
            return

        try:
            os.chmod(path, 0o755)   # nosec

        except Exception as e:
            cls.error("Can't chmod %s: %s", short(path), e)

    @classmethod
    def is_executable(cls, path):
        """
        :param str path: Path to file
        :return bool: True if file exists and is executable
        """
        return path and os.path.isfile(path) and os.access(path, os.X_OK)

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
        for p in os.environ.get('PATH', '').split(':'):
            fp = os.path.join(p, program)
            if cls.is_executable(fp):
                return cls.to_str(fp)
        return None

    @classmethod
    def run_program(cls, program, *args, **kwargs):
        """Run 'program' with 'args'"""
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
        args = [full_path] + cls.flattened(args, unique=False)
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
                    if output or error:
                        info = ": %s\n%s" % (error, output)
                    else:
                        info = ""
                    cls.abort("%s exited with code %s%s", program, p.returncode, info)
                return None

            return output or error or "<no output>"

        except Exception as e:
            system.abort("%s failed: %s", os.path.basename(program), e, exc_info=e)

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
            if not text or " " in text:
                sep = "'" if '"' in text else '"'
                result.append("%s%s%s" % (sep, text, sep))
            else:
                result.append(text)
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
        self.settings = settings    # type: pickley.settings.Settings
        self.map = {}

    @property
    def default_name(self):
        """
        :return str: Default name from settings
        """
        name = self.settings.resolved_value(self.key)
        if not name:
            names = self.names()
            if names:
                name = names[0]
        return name

    @property
    def default(self):
        """
        :return type: Default implementation to use
        """
        imp = self.get(self.settings.resolved_value(self.key))
        if not imp:
            names = self.names()
            if names:
                imp = self.map[names[0]]
        return imp

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
        :return type: Corresponding implementation class
        """
        configured = self.settings.resolved_value(self.key, package_name=package_name)
        imp = self.get(configured)
        if not imp:
            imp = self.default
        return imp


class cd:
    """Context manager for changing the current working directory"""
    def __init__(self, destination):
        self.destination = system.resolved_path(destination)

    def __repr__(self):
        return self.destination

    def __enter__(self):
        self.current_folder = os.getcwd()
        os.chdir(self.destination)

    def __exit__(self, *_):
        os.chdir(self.current_folder)


class capture_output:
    """
    Context manager allowing to temporarily grab stdout/stderr output.
    Output is captured and made available only for the duration of the context.

    Sample usage:

    with capture_output() as logged:
        ... do something that generates output ...
        assert "some message" in logged
    """
    def __init__(self, folder, stdout=True, stderr=True, env=None):
        self.current_folder = os.getcwd()
        self.folder = folder
        self.env = env
        self.old_env = {}
        self.old_out = sys.stdout
        self.old_err = sys.stderr
        self.old_handlers = logging.root.handlers

        self.out_buffer = six.StringIO() if stdout else self.old_out
        self.err_buffer = six.StringIO() if stderr else self.old_err

        self.handler = logging.StreamHandler(stream=self.err_buffer)
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s - %(message)s'))

    def __repr__(self):
        return self.to_string()

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
            if value:
                if value != os.environ.get(key):
                    system.debug("Restoring env %s=%s", key, value)
                    os.environ[key] = value
            elif key in os.environ:
                system.debug("Removing env %s", key)
                del os.environ[key]

    def __contains__(self, item):
        return item is not None and item in self.to_string()

    def __add__(self, other):
        return "%s %s" % (self, other)

    @property
    def error(self):
        if not self.err_buffer:
            return None
        return decode(self.err_buffer.getvalue())

    @property
    def output(self):
        if not self.out_buffer:
            return None
        return decode(self.out_buffer.getvalue())

    def to_string(self):
        result = ''
        if self.out_buffer:
            result += decode(self.out_buffer.getvalue())
        if self.err_buffer:
            result += decode(self.err_buffer.getvalue())
        return result


def duration_unit(count, name, short):
    if short:
        name = name[0]
    else:
        name = ' %s%s' % (name, '' if count == 1 else 's')
    return "%s%s" % (count, name)


def represented_duration(seconds, short=True, top=2, separator=' '):
    """
    :param int|float seconds: Duration in seconds
    :param bool short: If True, use short form
    :param int|None top: If specified, return 'top' most significant components
    :param str separator: Separator to use
    :return str: Human friendly duration representation
    """
    if seconds is None:
        return ''

    result = []
    if isinstance(seconds, float):
        seconds = int(seconds)

    if not isinstance(seconds, int):
        return str(seconds)

    # First, separate seconds and days
    days = seconds // SECONDS_IN_ONE_DAY
    seconds -= days * SECONDS_IN_ONE_DAY

    # Break down days into years, weeks and days
    years = days // 365
    days -= years * 365
    weeks = days // 7
    days -= weeks * 7

    # Break down seconds into hours, minutes and seconds
    hours = seconds // SECONDS_IN_ONE_HOUR
    seconds -= hours * SECONDS_IN_ONE_HOUR
    minutes = seconds // SECONDS_IN_ONE_MINUTE
    seconds -= minutes * SECONDS_IN_ONE_MINUTE

    if years:
        result.append(duration_unit(years, 'year', short))
    if weeks:
        result.append(duration_unit(weeks, 'week', short))
    if days:
        result.append(duration_unit(days, 'day', short))

    if hours:
        result.append(duration_unit(hours, 'hour', short))
    if minutes:
        result.append(duration_unit(minutes, 'minute', short))
    if seconds or not result:
        result.append(duration_unit(seconds, 'second', short))
    if top:
        result = result[:top]

    return separator.join(result)
