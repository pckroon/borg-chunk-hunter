#!/usr/bin/env python3
import os
import sys
from unittest.mock import MagicMock, patch

try:
    from borg import repository
    from borg.archive import Archive
    from borg.cache import LocalCache
    from borg.repository import Repository, LoggedIO
    from borg.helpers.errors import IntegrityError
except ImportError:
    print("Error: Borg python libraries not found.")
    raise


# --- 1. THE EXPLICIT SINGLETON REGISTRY ---
class ChunkRegistry:
    """
    A Singleton registry that tracks requested and missing chunks.
    Ensures that only one state container exists across the entire process.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Initialize the state only once
            cls._instance.missing = set()
            cls._instance.requested = set()
        return cls._instance

    def add_requested(self, chunk_id):
        self.requested.add(chunk_id)

    def add_missing(self, path):
        self.missing.add(path)


class GhostKey:
    """
    A transparent proxy for the Borg Key.
    It intercepts 'decrypt' to prevent IntegrityErrors,
    but forwards all other calls to the real key.
    """
    def __init__(self, real_key):
        self._real_key = real_key

    def decrypt(self, *args, **kwargs):
        # The only method we intercept
        # if args[0] == b'\x00':
        #     return b'\x00'
        # Otherwise, delegate to the real key
        try:
            return self._real_key.decrypt(*args, **kwargs)
        except IntegrityError as e:
            return b''


    def __getattr__(self, name):
        """
        Forward all other method and attribute calls
        to the original key instance.
        """
        return getattr(self._real_key, name)


orig_read = LoggedIO.read
def phantom_read(self, segment, offset, id):
    path = self.segment_filename(segment)
    registry = ChunkRegistry()
    registry.add_requested(path)
    try:
        return orig_read(self, segment, offset, id)
    except FileNotFoundError as e:
        registry.add_missing(path)
        mock_chunk = MagicMock()
        mock_chunk.data = b''
        mock_chunk.size = 0
        return mock_chunk

LoggedIO.read = phantom_read


def make_wrapper(cls, method_name):
    original = getattr(cls, method_name)
    def phantom(self, name):
        if name == 'key':
            return GhostKey(original(self, name))
        else:
            return original(self, name)
    setattr(cls, method_name, phantom)

make_wrapper(Repository, '__getattribute__')
make_wrapper(Archive, '__getattribute__')
make_wrapper(LocalCache, '__getattribute__')


# Hijack sys.exit to prevent the CLI from killing the script
class BorgExitException(Exception):
    def __init__(self, code):
        self.code = code


def patched_exit(status=0):
    raise BorgExitException(status)


sys.exit = patched_exit


# --- 4. THE EXECUTION ---
def main():
    import borg.archiver
    # argv = [sys.argv[0]] + sys.argv[1:]
    # sys.argv = argv

    print(f"[*] Intercepting Borg command: {' '.join(sys.argv[1:])}")
    print("-" * 60)

    try:
        borg.archiver.main()
    except BorgExitException as e:
        exit_code = e.code
    except Exception as e:
        print(f"\n[!] The Borg CLI crashed unexpectedly: {e}")
        exit_code = 1

    # --- 5. THE REVEAL ---
    # Retrieve the singleton instance to read the results
    registry = ChunkRegistry()

    print("\n" + "=" * 60)
    print("BORG OMNISCIENCE: RESULTS")
    print("=" * 60)
    print(f"Command Status: {'Success' if exit_code == 0 else 'Error/Exit'}")
    print(f"Total chunks requested: {len(registry.requested)}")
    print(f"Total chunks missing:   {len(registry.missing)}")
    print("-" * 60)

    if registry.missing:
        print("SHOPPING LIST (Rehydrate these in Azure):")
        for path in sorted(list(registry.missing)):
            print(path)
    else:
        print("Everything required for this command is already local!")
    print("=" * 60)


if __name__ == "__main__":
    main()