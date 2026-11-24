import errno
import json
import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def report_lock(path):
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.with_name(destination.name + ".lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            raise ValueError("Отчёт уже формируется другим процессом.") from None
        yield destination


def write_report(output_path, report):
    temporary_path = None
    directory = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("linux"):
            directory = os.open(output_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output_path.parent, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(report, temporary_file, ensure_ascii=False, indent=2, allow_nan=False)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.replace(output_path)
        if directory is not None:
            try:
                os.fsync(directory)
            except OSError:
                logging.getLogger(__name__).warning(
                    "Отчёт опубликован, но синхронизация каталога не подтверждена."
                )
    finally:
        if directory is not None:
            os.close(directory)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
