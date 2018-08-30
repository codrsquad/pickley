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
HOME = os.path.expanduser('~')


def decode(value):
    """ Python 2/3 friendly decoding of output """
    if isinstance(value, bytes) and not isinstance(value, str):
        return value.decode('utf-8')
    return value


def inform(message, logger=None):
    """
    :param str message: Message to show on stdout (as well as audit log)
    :param callable|None logger: Function to use to log
    """
    if not logger:
        logger = LOG.info
        print(message)
    logger(message)


def abort(message):
    LOG.error(message)
    sys.exit(1)


def python():
    """
    :return str: Path to python interpreter in use
    """
    if hasattr(sys, "real_prefix"):
        return os.path.join(sys.real_prefix, "bin", "python")
    else:
        return sys.executable


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
    path = str(path).replace(HOME, "~")
    return path


def resolved_path(path, base=None):
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


def flatten(result, value, separator=None, unique=True):
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
            flatten(result, item, separator=separator, unique=unique)
        return
    if separator is not None and hasattr(value, "split") and separator in value:
        flatten(result, value.split(separator), separator=separator, unique=unique)
        return
    if not unique or value not in result:
        if not unique and sys.version_info.major < 3:
            # Pex and pip want all their args to be str in python2
            value = value.encode('ascii', 'ignore')
        result.append(value)


def flattened(value, separator=None, unique=True):
    """
    :param value: Possibly nested arguments (sequence of lists, nested lists)
    :param str|None separator: Split values with 'separator' if specified
    :param bool unique: If True, return unique values only
    :return list: 'value' flattened out (leaves from all involved lists/tuples)
    """
    result = []
    flatten(result, value, separator=separator, unique=unique)
    return result


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
        name = self.settings.get_value(self.key)
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
        imp = self.get(self.settings.get_value(self.key))
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
        configured = self.settings.get_value(self.key, package_name=package_name)
        imp = self.get(configured)
        if not imp:
            imp = self.default
        return imp


class cd:
    """Context manager for changing the current working directory"""
    def __init__(self, destination):
        self.destination = resolved_path(destination)

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
    def __init__(self, folder, stdout=True, stderr=True, env=None, dryrun=False):
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
        self.handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s - %(message)s'))

    def __repr__(self):
        return self.to_string()

    def __enter__(self):
        if self.folder:
            ensure_folder(self.folder, folder=True, dryrun=self.dryrun)

        self.old_env = {}
        for key, value in os.environ.items():
            self.old_env[key] = os.environ.get(key)

        if self.env:
            for key, value in self.env.items():
                if value:
                    if value != os.environ.get(key):
                        LOG.debug("Customizing env %s=%s" % (key, value))
                        os.environ[key] = value
                elif key in os.environ:
                    LOG.debug("Removing env %s" % key)
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
                LOG.debug("Cleaning up env %s" % key)
                del os.environ[key]

        for key, value in self.old_env.items():
            if value:
                if value != os.environ.get(key):
                    LOG.debug("Restoring env %s=%s" % (key, value))
                    os.environ[key] = value
            elif key in os.environ:
                LOG.debug("Removing env %s" % key)
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


def ensure_folder(path, folder=False, dryrun=False):
    """
    :param str path: Path to file or folder
    :param bool folder: If True, 'path' refers to a folder (file otherwise)
    :param bool dryrun: If True, don't effectively create anything
    :return str:
    """
    if path:
        path = resolved_path(path)
        folder = path if folder else os.path.dirname(path)
        if not os.path.isdir(folder):
            try:
                if dryrun:
                    LOG.debug("Would create %s", short(path))
                else:
                    os.makedirs(folder)

            except Exception as e:
                LOG.error("Can't create folder %s: %s", short(folder), e)
                raise


def copy_file(source, dest, dryrun):
    """ Copy file 'source' -> 'dest' """
    if dryrun:
        LOG.debug("Would copy %s -> %s", short(source), short(dest))
        return

    if not os.path.exists(source):
        LOG.error("Can't copy %s -> %s, source does not exist", short(source), short(dest))
        return

    LOG.debug("Copying %s -> %s", short(source), short(dest))
    ensure_folder(dest, dryrun=dryrun)

    if os.path.isdir(source):
        try:
            shutil.copytree(source, dest, symlinks=False)
        except Exception as e:
            LOG.error("Can't copy folder %s -> %s: %s", short(source), short(dest), e)
            raise
    else:
        try:
            if os.path.exists(dest):
                os.unlink(dest)
            shutil.copy(source, dest)
        except Exception as e:
            LOG.error("Can't copy file %s -> %s: %s", short(source), short(dest), e)
            raise

    try:
        # Make sure last modification time is preserved
        shutil.copystat(source, dest)
    except Exception as e:
        LOG.warning("Can't copy stats %s -> %s: %s", short(source), short(dest), e)


def symlink(linkpath, target, dryrun):
    """
    Create symlink linkpath -> target

    :param str linkpath: Path to the symlink to be created
    :param str target: Path to where that symlink should point to
    :param bool dryrun: If True, don't do anything only show what would be done
    """
    actual_target = target
    if os.path.isabs(target) and os.path.isabs(linkpath):
        linkfolder = os.path.dirname(linkpath)
        if os.path.dirname(target).startswith(linkfolder):
            # Use relative path if target is under linkpath
            actual_target = os.path.relpath(target, linkfolder)

    if dryrun:
        LOG.debug("Would symlink %s -> %s", short(linkpath), short(actual_target))
        return

    if not os.path.exists(target):
        LOG.error("%s does not exist, can't symlink %s to it", short(target), short(linkpath))
        return

    try:
        delete_file(linkpath)
        LOG.debug("Creating symlink %s -> %s", short(linkpath), short(actual_target))
        os.symlink(actual_target, linkpath)
    except Exception as e:
        LOG.error("Can't symlink %s -> %s: %s", short(linkpath), short(actual_target), e)


def delete_file(path, dryrun=False):
    """ Delete file/folder with 'path' """
    if not os.path.exists(path) and not os.path.islink(path):
        return

    if dryrun:
        LOG.debug("Would delete %s", short(path))
        return

    LOG.debug("Deleting %s", short(path))
    if os.path.isfile(path) or os.path.islink(path):
        try:
            os.unlink(path)
        except Exception as e:
            LOG.error("Can't delete %s: %s", short(path), e)
            raise
        return

    try:
        shutil.rmtree(path)
    except Exception as e:
        LOG.error("Can't delete folder %s: %s", short(path), e)
        raise


def is_executable(path):
    """
    :param str path: Path to file
    :return bool: True if file exists and is executable
    """
    return path and os.path.isfile(path) and os.access(path, os.X_OK)


def executable_names(folder):
    """
    :param str folder: Folder to examine
    :return list: List of basenames of exectuable files in 'folder'
    """
    result = []
    if folder and os.path.isdir(folder):
        for name in os.listdir(folder):
            fpath = os.path.join(folder, name)
            if is_executable(fpath):
                result.append(name)
    return result


def which(program):
    """
    :param str program: Program name to find via env var PATH
    :return str|None: Full path to program, if one exists and is executable
    """
    if not program:
        return None
    if os.path.isabs(program):
        if is_executable(program):
            return program
        return None
    for p in os.environ.get('PATH', '').split(':'):
        fp = os.path.join(p, program)
        if is_executable(fp):
            return fp
    return None


def represented_args(args, base=None, separator=" "):
    """
    :param list|tuple args: Arguments to represent
    :param str|None base: Base folder to relativise paths to
    :param str separator: Separator to use
    :return str: Quoted as needed textual representation
    """
    result = []
    for text in args:
        if not text or " " in text:
            sep = "'" if '"' in text else '"'
            result.append("%s%s%s" % (sep, short(text, base=base), sep))
        else:
            result.append(short(text, base=base))
    return separator.join(result)


def run_program(program, *args, **kwargs):
    """Run 'program' with 'args'"""
    full_path = which(program)

    dryrun = kwargs.pop("dryrun", False)
    fatal = kwargs.pop("fatal", True)
    message = "Would run" if dryrun else "Running"
    message = "%s: %s %s" % (message, short(full_path or program), represented_args(args))
    LOG.debug(message)

    if dryrun:
        return message

    if not full_path:
        LOG.error("%s is not installed" % program)
        if fatal:
            sys.exit(1)
        return None

    stdout = kwargs.pop("stdout", subprocess.PIPE)
    stderr = kwargs.pop("stderr", subprocess.PIPE)
    args = [full_path] + list(args)
    p = subprocess.Popen(args, stdout=stdout, stderr=stderr)  # nosec
    output, error = p.communicate()
    output = decode(output)
    error = decode(error)
    if output:
        output = output.strip()
    if error:
        error = error.strip()

    if p.returncode:
        if output or error:
            info = ": %s\n%s" % (error, output)
        else:
            info = ""

        LOG.error("%s exited with code %s%s", program, p.returncode, info)
        if fatal:
            sys.exit(p.returncode)
        return None

    return output or error or "<no output>"


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
