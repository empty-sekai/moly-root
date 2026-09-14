"""Atomic file replacement and process-scoped locks for persistent state."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile

PUBLIC_FILE_MODE = 0o644
PRIVATE_FILE_MODE = 0o600


def write_bytes(path, data, *, mode=None):
    """Replace bytes atomically, setting permissions before publication.

    An explicit mode is the caller's policy for both creation and replacement.
    With no mode, preserve an existing regular file's permission bits, or create
    a private file. Ownership, ACLs and special mode bits are not copied.
    """
    path = Path(path)
    if mode is None:
        try:
            existing = path.stat(follow_symlinks=False)
            mode = existing.st_mode & 0o777 if stat.S_ISREG(existing.st_mode) else PRIVATE_FILE_MODE
        except FileNotFoundError:
            mode = PRIVATE_FILE_MODE
    if type(mode) is not int or not 0 <= mode <= 0o777:
        raise ValueError("mode must contain only ordinary file permission bits (0000-0777)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), mode)
            else:
                os.chmod(temporary, mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(document):
    return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write_json(path, document, *, mode=None):
    write_bytes(path, json_bytes(document), mode=mode)


@contextmanager
def exclusive_lock(path):
    """Reject concurrent writers; the OS releases the lock even after a crash.

    The lock file stays in place: unlinking it would let another process lock
    a different inode while a waiting process still holds the old one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OSError(f"another writer holds the lock: {path}") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
