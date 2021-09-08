#
# The cache implementation is only used when the watchdog package
# is installed
#

from os.path import dirname
from pathlib import Path
import pickle
import time
import typing as t
import threading

try:
    from watchdog.observers import Observer

    ENABLE_CACHING = True

except ImportError:

    Observer = None

    ENABLE_CACHING = False

if t.TYPE_CHECKING:
    from pypiserver.core import PkgFile


class CacheManager:
    """
    A naive cache implementation for listdir and digest_file

    The listdir_cache is just a giant list of PkgFile objects, and
    for simplicity it is invalidated anytime a modification occurs
    within the directory it represents. If we were smarter about
    the way that the listdir data structure were created/stored,
    then we could do more granular invalidation. In practice, this
    is good enough for now.

    The digest_cache exists on a per-file basis, because computing
    hashes on large files can get expensive, and it's very easy to
    invalidate specific filenames.
    """

    def __init__(self):
        if not ENABLE_CACHING:
            raise RuntimeError(
                "Please install the extra cache requirements by running 'pip "
                "install pypiserver[cache]' to use the CachingFileBackend"
            )

        # Cache for listdir output
        self.listdir_cache = {}

        # Cache for hashes: two-level dictionary
        # -> key: hash_algo, value: dict
        #    -> key: file path, value: hash
        # We assume that the hash_algo value will never be erased
        self.digest_cache = {}

        self.observer = Observer()
        self.observer.start()

        # Directories being watched
        self.watched = set()

        self.watch_lock = threading.Lock()
        self.digest_lock = threading.Lock()
        self.listdir_lock = threading.Lock()

    def listdir(
        self,
        root: t.Union[Path, str],
        impl_fn: t.Callable[[Path], t.Iterable["PkgFile"]],
    ) -> t.Iterable["PkgFile"]:
        root = str(root)
        with self.listdir_lock:
            try:
                return self.listdir_cache[root]
            except KeyError:
                # check to see if we're watching
                with self.watch_lock:
                    if root not in self.watched:
                        self._watch(root)

                v = list(impl_fn(Path(root)))
                self.listdir_cache[root] = v
                return v

    def digest_file(
        self, fpath: str, hash_algo: str, impl_fn: t.Callable[[str, str], str]
    ) -> str:
        with self.digest_lock:
            try:
                cache = self.digest_cache[hash_algo]
            except KeyError:
                cache = self.digest_cache.setdefault(hash_algo, {})

            try:
                return cache[fpath]
            except KeyError:
                root = dirname(fpath)
                with self.watch_lock:
                    if root not in self.watched:
                        self._watch(root)

            # TODO: move this outside of the lock... but there's not a good
            #       way to do this without a race condition if the file
            #       gets modified
            v = impl_fn(fpath, hash_algo)
            cache[fpath] = v
            return v

    def _watch(self, root: str):
        self.watched.add(root)
        self.observer.schedule(_EventHandler(self, root), root, recursive=True)

    def invalidate_root_cache(self, root: t.Union[Path, str]):
        with self.listdir_lock:
            self.listdir_cache.pop(str(root), None)



class CacheFileManager(CacheManager):
    """
    Serialize/deserialize package index cache file for faster server loading.
    """
    pkg_index_file: Path = None
    using_index_file: bool = False

    def __init__(self, pkg_index_file: Path = None):
        super().__init__()

        if not pkg_index_file or not pkg_index_file.exists():
            return

        # Load the listdir_cache from package index file if exists.
        try:
            print("loading cache index file ...")
            start = time.monotonic()
            with open(pkg_index_file, 'rb') as f:
                self.listdir_cache = pickle.load(f)
            end = time.monotonic()
            print("time elapsed for loading:", end - start, "(s).")
            self.pkg_index_file = pkg_index_file
            self.using_index_file = True
        except (IOError, Exception):
            pass

    def listdir(
        self,
        root: t.Union[Path, str],
        impl_fn: t.Callable[[Path], t.Iterable["PkgFile"]],
    ) -> t.Iterable["PkgFile"]:
        """
        If index cache file is loaded, use it exclusively without live updating.
        """
        if not self.using_index_file:
            # No file index cache found. Fallback to the default live cache.
            return super().listdir(root, impl_fn)
        return self.listdir_cache.get(root, {})

    def force_update_listdir_cache(
        self,
        root: t.Union[Path, str],
        impl_fn: t.Callable[[Path], t.Iterable["PkgFile"]],
    ) -> t.Iterable["PkgFile"]:
        """
        Force update listdir_cache package index for given list of root directory.
        """
        with self.listdir_lock:
            v = list(impl_fn(Path(root)))
            self.listdir_cache[root] = v
            return v

    def serialize_listdir_cache(self):
        """
        Serialize the current listdir_cache package index to a file for the next use.
        """
        with open(self.pkg_index_file, 'wb') as f:
            pickle.dump(self.listdir_cache, f)


class _EventHandler:
    def __init__(self, cache: CacheManager, root: str):
        self.cache = cache
        self.root = root

    def dispatch(self, event):
        """Called by watchdog observer"""
        cache = self.cache

        # Don't care about directory events
        if event.is_directory:
            return

        # Lazy: just invalidate the whole cache
        cache.invalidate_root_cache(self.root)

        # Digests are more expensive: invalidate specific paths
        paths = []

        if event.event_type == "moved":
            paths.append(event.src_path)
            paths.append(event.dest_path)
        else:
            paths.append(event.src_path)

        with cache.digest_lock:
            for _, subcache in cache.digest_cache.items():
                for path in paths:
                    subcache.pop(path, None)
