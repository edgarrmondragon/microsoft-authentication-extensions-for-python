"""Generic functions and types for working with a TokenCache that is not platform specific."""
import os
import time
import logging

import msal

try:
    from .cache_lock import CrossPlatLock  # It needs portalocker
except ImportError:
    from .filelock import CrossPlatLock
from .persistence import _mkdir_p, PersistenceNotFound


logger = logging.getLogger(__name__)

class PersistedTokenCache(msal.SerializableTokenCache):
    """A token cache backed by a persistence layer, coordinated by a file lock,
    to sustain a certain level of multi-process concurrency for a desktop app.

    The scenario is that multiple instances of same desktop app
    (or even multiple different apps)
    create their own ``PersistedTokenCache`` instances,
    which are all backed by the same token cache file on disk
    (known as a persistence). The goal is to have Single Sign On (SSO).

    Each instance of ``PersistedTokenCache`` holds a snapshot of the token cache
    in memory.
    Each :func:`~find` call will
    automatically reload token cache from the persistence when necessary,
    so that it will have fresh data.
    Each :func:`~modify` call will
    automatically reload token cache from the persistence when necessary,
    so that new writes will be appended on top of latest token cache data,
    and then the new data will be immediately flushed back to the persistence.

    Note: :func:`~deserialize` and :func:`~serialize` remain the same
    as their counterparts in the parent class ``msal.SerializableTokenCache``.
    In other words, they do not have the "reload from persistence if necessary"
    nor the "flush back to persistence" behavior.
    """

    def __init__(self, persistence, lock_location=None):
        super(PersistedTokenCache, self).__init__()
        self._lock_location = (
            os.path.expanduser(lock_location) if lock_location
            else persistence.get_location() + ".lockfile")
        _mkdir_p(os.path.dirname(self._lock_location))
        self._persistence = persistence
        self._last_sync = 0  # _last_sync is a Unixtime
        self.is_encrypted = persistence.is_encrypted

    def _reload_if_necessary(self):
        # type: () -> None
        """Reload cache from persistence layer, if necessary"""
        try:
            if self._last_sync < self._persistence.time_last_modified():
                self.deserialize(self._persistence.load())
                self._last_sync = time.time()
        except PersistenceNotFound:
            # From cache's perspective, a nonexistent persistence is a NO-OP.
            pass
        # However, existing data unable to be decrypted will still be bubbled up.

    def modify(self, credential_type, old_entry, new_key_value_pairs=None):
        with CrossPlatLock(self._lock_location):
            self._reload_if_necessary()
            super(PersistedTokenCache, self).modify(
                credential_type,
                old_entry,
                new_key_value_pairs=new_key_value_pairs)
            self._persistence.save(self.serialize())
            self._last_sync = time.time()

    def find(self, credential_type, **kwargs):  # pylint: disable=arguments-differ
        # Use optimistic locking rather than CrossPlatLock(self._lock_location)
        retry = 3
        for attempt in range(1, retry + 1):
            try:
                self._reload_if_necessary()
            except Exception:  # pylint: disable=broad-except
                # Presumably other processes are writing the file, causing dirty read
                if attempt < retry:
                    logger.debug("Unable to load token cache file in No. %d attempt", attempt)
                    time.sleep(0.5)
                else:
                    raise  # End of retry. Re-raise the exception as-is.
            else:  # If reload encountered no error, the data is considered intact
                return super(PersistedTokenCache, self).find(credential_type, **kwargs)
        return []  # Not really reachable here. Just to keep pylint happy.

