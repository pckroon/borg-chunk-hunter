#!/usr/bin/env python3
import argparse
import contextlib
import functools
import sys
import inspect

from borg.repository import LoggedIO
from borg.helpers.errors import IntegrityError
from borg.helpers.manifest import Manifest
import borg.archiver


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
        try:
            return self._real_key.decrypt(*args, **kwargs)
        except (IntegrityError, IndexError) as e:
            return b''

    def __getattr__(self, name):
        """
        Forward all other method and attribute calls
        to the original key instance.
        """
        return getattr(self._real_key, name)


def phantom_read(self, segment, offset, id, read_data=True, original=None):
    path = self.segment_filename(segment)
    registry = ChunkRegistry()
    registry.add_requested(path)
    try:
        return original(self, segment, offset, id, read_data=read_data)
    except FileNotFoundError as e:
        registry.add_missing(path)
        if read_data:
            return b''
        else:
            return 0


def make_wrapper(cls, method_name, new_function):
    original = getattr(cls, method_name)
    if inspect.isclass(cls):
        partial = functools.partialmethod
    else:
        partial = functools.partial

    if 'original' in inspect.signature(new_function).parameters:
        kwargs = {'original': original}
    else:
        kwargs = {}
    phantom = partial(new_function, **kwargs)
    functools.update_wrapper(phantom, original)
    setattr(cls, method_name, phantom)


# Hijack sys.exit to prevent the CLI from killing the script
class BorgExitException(Exception):
    def __init__(self, code):
        self.code = code


def patched_exit(status=0):
    raise BorgExitException(status)


def phantom_manifest_load(*args, original=None, **kwargs):
    manifest, key = original(*args, **kwargs)
    if key:
        key = GhostKey(key)
    return manifest, key


def install_wrappers():
    make_wrapper(Manifest, 'load', phantom_manifest_load)
    make_wrapper(LoggedIO, 'read', phantom_read)
    make_wrapper(sys, 'exit', patched_exit)


def build_cli():
    parser = argparse.ArgumentParser(description="Intercept Borg requests to identify missing chunks.")
    parser.add_argument("--borg-out", type=str, help="File to capture Borg stdout")
    parser.add_argument("--borg-err", type=str, help="File to capture Borg stderr")
    verb_group = parser.add_mutually_exclusive_group()
    verb_group.add_argument('--quiet', '-q', action='store_true', help="Limit output to missing chunks")
    verb_group.add_argument('--verbose', '-v', action='store_true', help="Also output successfully retrieved chunks")
    return parser


def run_borg(argv, stdout=None, stderr=None):
    old_sysargv = sys.argv.copy()
    sys.argv = argv
    redirect_stack = contextlib.ExitStack()

    if stdout:
        out_f = redirect_stack.enter_context(open(stdout, 'w'))
        redirect_stack.enter_context(contextlib.redirect_stdout(out_f))
    if stderr:
        err_f = redirect_stack.enter_context(open(stderr, 'w'))
        redirect_stack.enter_context(contextlib.redirect_stderr(err_f))
    try:
        with redirect_stack:
            borg.archiver.main()
        exit_code = 0
    except BorgExitException as e:
        exit_code = e.code
    except Exception as e:
        exit_code = 1

    sys.argv = old_sysargv
    return exit_code


def main():
    parser = build_cli()
    args, borg_args = parser.parse_known_args()

    install_wrappers()

    exit_code = run_borg([sys.argv[0]] + borg_args, args.borg_out, args.borg_err)

    registry = ChunkRegistry()

    if not args.quiet:
        print(f"[*] Intercepted Borg command: {' '.join(sys.argv[1:])}")
        print("-" * 60)
        print(f"Command Status: {'Success' if exit_code == 0 else 'Error/Exit'}")
        print(f"Total chunks requested: {len(registry.requested)}")
        print(f"Total chunks missing:   {len(registry.missing)}")

    if registry.missing:
        if args.verbose:
            print("-" * 60)
            print("RETRIEVED CHUNKS")
            print("----------------")
            for path in sorted(list(registry.requested - registry.missing)):
                print(path)
        if not args.quiet:
            print("-" * 60)
            print("MISSING CHUNKS")
            print("--------------")
        for path in sorted(list(registry.missing)):
            print(path)
    else:
        if not args.quiet and not exit_code:
            print("-" * 60)
            print("Everything required for this command is already local!")


if __name__ == "__main__":
    main()
