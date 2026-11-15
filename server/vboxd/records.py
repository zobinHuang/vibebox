import fcntl
import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from vboxd.errors import NotFoundError, ValidationError


class RecordStore:
    """A directory of JSON records on the local disk, one file per record."""

    RECORD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
    RECORD_SLUG_SANITIZE_PATTERN = re.compile(r"[^a-z0-9]+")
    RECORD_LOCK_NAME = ".lock"
    RECORD_DIR_MODE = 0o700
    RECORD_FILE_MODE = 0o600

    def __init__(self, directory: Path, kind: str) -> None:
        self._directory = directory
        self._kind = kind

    @classmethod
    def slugify(cls, text: str, fallback: str) -> str:
        """Render free text as an identifier that is safe to use as a file name.

        Args:
            text: the text to render
            fallback: the identifier to use when nothing usable survives

        Returns:
            a lowercase identifier of letters, digits, dots, dashes and underscores
        """
        slug = cls.RECORD_SLUG_SANITIZE_PATTERN.sub("-", text.strip().lower())
        slug = slug.strip("-._")[:128]
        return slug if cls.RECORD_ID_PATTERN.match(slug) else fallback

    def validate_id(self, record_id: str) -> str:
        """Check that an identifier is safe to join onto the store's directory.

        Args:
            record_id: the identifier taken from a request path or body

        Returns:
            the identifier unchanged

        Raises:
            ValidationError: if it could address a file outside the store
        """
        if not self.RECORD_ID_PATTERN.match(record_id):
            raise ValidationError(f"unusable {self._kind} id: {record_id!r}")
        return record_id

    def exists(self, record_id: str) -> bool:
        """Report whether a record is stored.

        Args:
            record_id: the record to look for

        Returns:
            True when the record exists
        """
        return self._path_of(record_id).is_file()

    def read(self, record_id: str) -> dict[str, object]:
        """Read one stored record.

        Args:
            record_id: the record to read

        Returns:
            the decoded record

        Raises:
            ValidationError: if the identifier is unusable
            NotFoundError: if no such record is stored
        """
        path = self._path_of(record_id)
        try:
            with path.open(encoding="utf-8") as handle:
                map_record = json.load(handle)
        except FileNotFoundError as err:
            raise NotFoundError(f"no such {self._kind}: {record_id}") from err
        except json.JSONDecodeError as err:
            raise NotFoundError(f"malformed {self._kind}: {record_id}") from err
        if not isinstance(map_record, dict):
            raise NotFoundError(f"malformed {self._kind}: {record_id}")
        return map_record

    def write(self, record_id: str, map_record: dict[str, object]) -> None:
        """Store one record, replacing any earlier version of it.

        Args:
            record_id: the record to write
            map_record: the record body
        """
        path = self._path_of(record_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._directory.chmod(self.RECORD_DIR_MODE)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(map_record, handle, indent=2)
            handle.write("\n")
        path.chmod(self.RECORD_FILE_MODE)

    def delete(self, record_id: str) -> None:
        """Remove one stored record.

        Args:
            record_id: the record to remove

        Raises:
            ValidationError: if the identifier is unusable
            NotFoundError: if no such record is stored
        """
        path = self._path_of(record_id)
        try:
            path.unlink()
        except FileNotFoundError as err:
            raise NotFoundError(f"no such {self._kind}: {record_id}") from err

    def list_all(self) -> list[dict[str, object]]:
        """Read every stored record.

        Returns:
            the decoded records, in no particular order
        """
        if not self._directory.is_dir():
            return []
        list_record: list[dict[str, object]] = []
        for path in self._directory.iterdir():
            if path.suffix != ".json" or not path.is_file():
                continue
            try:
                with path.open(encoding="utf-8") as handle:
                    map_record = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(map_record, dict):
                list_record.append(map_record)
        return list_record

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the store's write lock for the duration of the block.

        Yields:
            nothing; the lock is released when the block ends
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        handle = (self._directory / self.RECORD_LOCK_NAME).open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            handle.close()

    def _path_of(self, record_id: str) -> Path:
        return self._directory / f"{self.validate_id(record_id)}.json"
