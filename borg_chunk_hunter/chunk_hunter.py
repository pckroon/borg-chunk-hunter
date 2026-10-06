import os
import sys
from unittest.mock import MagicMock

try:
    from borg import repository
    from borg.repository import Repository
    import borg.archiver
except ImportError:
    print("Error: Borg python libraries not found.")
    sys.exit(1)

# --- 1. THE DATA HIJACK ---
original_get = Repository.get


class ChunkTracker:
    def __init__(self):
        self.missing = set()
        self.requested = set()

    def track_get(self, self_repo, chunk_id):
        self.requested.add(chunk_id)
        path = self_repo.get_chunk_path(chunk_id)

        # if os.path.exists(path):
        try:
            return original_get(self_repo, chunk_id)
        except:
            raise
        # else:
        #     self.missing.add(path)
        #     # We return a Mock that mimics a Borg chunk's basic properties.
        #     # This allows the CLI to continue without crashing.
        #     mock_chunk = MagicMock()
        #     mock_chunk.data = b'\x00'  # Provide dummy data to prevent crashes on .decode()
        #     mock_chunk.size = 0
        #     return mock_chunk


tracker = ChunkTracker()
Repository.get = tracker.track_get


# --- 2. THE EXIT HIJACK ---
# Borg's main() calls sys.exit(). We replace it with a custom exception
# so we can catch the exit and still print our results.
class BorgExitException(Exception):
    def __init__(self, code):
        self.code = code


def patched_exit(status=0):
    raise BorgExitException(status)


# Save original exit and overwrite it
original_exit = sys.exit
sys.exit = patched_exit


# --- 3. THE EXECUTION ---
def main():
    # We pass the same arguments the user provided to the script
    # but we shift them so 'borg-omniscience.py' is replaced by 'borg'
    # in the eyes of the archiver.
    argv = [sys.argv[0]] + sys.argv[1:]

    print(f"[*] Intercepting Borg command: {' '.join(sys.argv[1:])}")
    print("[*] Running in 'Ghost Mode'... (intercepting missing chunks)")
    print("-" * 60)

    try:
        # Invoke the actual Borg CLI entrypoint
        borg.archiver.main(argv)
    except BorgExitException as e:
        # This is where the program 'exits' normally
        exit_code = e.code
    except Exception as e:
        print(f"\n[!] The Borg CLI crashed unexpectedly: {e}")
        exit_code = 1

    # --- 4. THE REVEAL ---
    print("\n" + "=" * 60)
    print("BORG OMNISCIENCE: RESULTS")
    print("=" * 60)
    print(f"Command Status: {'Success' if exit_code == 0 else 'Error/Exit'}")
    print(f"Total chunks requested: {len(tracker.requested)}")
    print(f"Total chunks missing:   {len(tracker.missing)}")
    print("-" * 60)

    if tracker.missing:
        print("SHOPPING LIST:")
        for path in sorted(list(tracker.missing)):
            print(path)
    else:
        print("Everything required for this command is already local!")
    print("=" * 60)


if __name__ == "__main__":
    main()