from glob import glob
import itertools
from pathlib import Path
import sys
import time

from pypiserver.backend import listdir
from pypiserver.cache import CacheFileManager

# The paths should be absolute.
PACKAGE_ROOTS = None
PKG_INDEX_FILE = "pkg_index_cache.pickle"


def get_listdir_cache(cache_manager):
    print("getting listdir_cache ...")
    start = time.monotonic()
    for root in PACKAGE_ROOTS:
        print(f"- processing {root} ...")
        cache_manager.force_update_listdir_cache(root, listdir)
        # for subdir in glob(f"{root}/*/"):
        #     print(f"- processing {subdir} ...")
        #     cache_manager.force_update_listdir_cache(subdir, listdir)
    end = time.monotonic()
    print("time elapsed for getting listdir_cache:", end - start, "(s).")


def serialize_listdir(cache_manager):
    print("serializing listdir_cache ...")
    start = time.monotonic()
    cache_manager.serialize_listdir_cache()
    end = time.monotonic()
    print("time elapsed for serializing listdir_cache:", end - start, "(s).")


def main():
    cache_manager = CacheFileManager()
    get_listdir_cache(cache_manager)
    cache_manager.pkg_index_file = PKG_INDEX_FILE
    serialize_listdir(cache_manager)
    print("done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("at least one package root directory should be specified.")
        exit(1)
    PACKAGE_ROOTS = [Path(r).resolve() for r in sys.argv[1:]]
    main()
