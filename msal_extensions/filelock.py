"""A cross-process lock based on exclusive creation of a given file name"""
import os
import sys
import errno
import time
import logging


logger = logging.getLogger(__name__)


class LockError(RuntimeError):
    """It will be raised when unable to obtain a lock"""


class CrossPlatLock(object):
    """This implementation relies only on ``open(..., 'x')``"""
    def __init__(self, lockfile_path):
        self._lockpath = lockfile_path

    def __enter__(self):
        self._create_lock_file('{} {}'.format(
            os.getpid(),
            sys.argv[0],
            ).encode('utf-8'))  # pylint: disable=consider-using-f-string
        return self

    def _create_lock_file(self, content):
        timeout = 5
        check_interval = 0.25
        current_time = getattr(time, "monotonic", time.time)
        timeout_end = current_time() + timeout
        while timeout_end > current_time():
            try:
                with open(self._lockpath, 'xb') as lock_file:  # pylint: disable=unspecified-encoding
                    lock_file.write(content)
                return None  # Happy path
            except ValueError:  # This needs to be the first clause, for Python 2 to hit it
                raise LockError("Python 2 does not support atomic creation of file")
            except FileExistsError:  # Only Python 3 will reach this clause
                logger.debug(
                    "Process %d found existing lock file, will retry after %f second",
                    os.getpid(), check_interval)
                time.sleep(check_interval)
        raise LockError(
            "Unable to obtain lock, despite trying for {} second(s). "
            "You may want to manually remove the stale lock file {}".format(
                timeout,
                self._lockpath,
            ))

    def __exit__(self, *args):
        try:
            os.remove(self._lockpath)
        except OSError as ex:  # pylint: disable=invalid-name
            if ex.errno in (errno.ENOENT, errno.EACCES):
                # Probably another process has raced this one
                # and ended up clearing or locking the file for itself.
                logger.debug("Unable to remove lock file")
            else:
                raise

