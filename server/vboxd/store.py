import base64
import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from vboxd.errors import CursorConflictError, NotFoundError, ValidationError


@dataclass
class AgentAccount:
    """The coding agent's logged-in account, as reported by the client."""

    uuid: str
    email: str | None = None
    label: str | None = None

    ACCOUNT_ANONYMOUS_UUID = "anonymous"
    ACCOUNT_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9_-]")
    ACCOUNT_DIR_MAX_CHARS = 64

    @classmethod
    def from_request(cls, map_body: dict[str, object]) -> "AgentAccount":
        """Read the account block out of a client request body.

        Args:
            map_body: the decoded request body

        Returns:
            the reported account, anonymous when the client knows of none
        """
        map_account = map_body.get("account")
        if not isinstance(map_account, dict):
            return cls(uuid=cls.ACCOUNT_ANONYMOUS_UUID)
        uuid = str(map_account.get("uuid") or "").strip()
        email = map_account.get("email")
        label = map_account.get("label")
        return cls(
            uuid=uuid or cls.ACCOUNT_ANONYMOUS_UUID,
            email=str(email) if email else None,
            label=str(label) if label else None,
        )

    def directory_name(self) -> str:
        """Render the account as a directory name that is safe to join onto a path.

        Returns:
            the sanitized account identifier, never empty and never a path segment
            that could escape its parent
        """
        name = self.ACCOUNT_SANITIZE_PATTERN.sub("_", self.uuid)
        name = name[: self.ACCOUNT_DIR_MAX_CHARS].strip("_-")
        return name or self.ACCOUNT_ANONYMOUS_UUID


@dataclass
class MachineInfo:
    """The machine a session was recorded on."""

    hostname: str | None = None
    fqdn: str | None = None
    os: str | None = None
    os_release: str | None = None
    arch: str | None = None
    user: str | None = None

    MACHINE_FIELDS = ("hostname", "fqdn", "os", "os_release", "arch", "user")

    @classmethod
    def from_mapping(cls, map_raw: object) -> "MachineInfo":
        """Read a machine block out of a request body or a stored meta.json.

        Args:
            map_raw: the decoded machine mapping, or anything else when absent

        Returns:
            the machine as reported, with every unknown field left as None
        """
        if not isinstance(map_raw, dict):
            return cls()
        map_clean: dict[str, str | None] = {}
        for name in cls.MACHINE_FIELDS:
            value = map_raw.get(name)
            text = str(value).strip() if value is not None else ""
            map_clean[name] = text or None
        return cls(**map_clean)

    def as_payload(self) -> dict[str, object]:
        """Render the machine as the JSON stored beside the transcript.

        Returns:
            a mapping of every machine field
        """
        return {name: getattr(self, name) for name in self.MACHINE_FIELDS}


@dataclass
class SessionMeta:
    """Everything known about one stored agent session."""

    agent: str
    session_id: str
    account: AgentAccount
    cwd: str | None = None
    machine: MachineInfo = field(default_factory=MachineInfo)
    git_branch: str | None = None
    cli_version: str | None = None
    transcript_path: str | None = None
    opened_at: str = ""
    updated_at: str = ""
    closed_at: str | None = None

    SESSION_AGENTS = ("claude", "codex")
    SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    SESSION_PROJECT_HASH_CHARS = 8
    SESSION_PROJECT_NAME_MAX_CHARS = 48
    SESSION_PROJECT_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9_-]")
    SESSION_PROJECT_UNKNOWN = "unknown"

    @staticmethod
    def now_timestamp() -> str:
        """Produce the timestamp format used throughout the stored metadata.

        Returns:
            the current time as an ISO 8601 string in UTC
        """
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @classmethod
    def validate_session_id(cls, session_id: str) -> str:
        """Check that a session identifier is safe to use as a directory name.

        Args:
            session_id: the identifier taken from the request path

        Returns:
            the identifier unchanged

        Raises:
            ValidationError: if the identifier could escape the data root
        """
        if not cls.SESSION_ID_PATTERN.match(session_id):
            raise ValidationError(f"unusable session id: {session_id!r}")
        return session_id

    @classmethod
    def from_request(
        cls,
        session_id: str,
        map_body: dict[str, object],
    ) -> "SessionMeta":
        """Build session metadata from a client's begin request.

        Args:
            session_id: the identifier taken from the request path
            map_body: the decoded request body

        Returns:
            the metadata to record for this session

        Raises:
            ValidationError: if the session id or agent name is unusable
        """
        cls.validate_session_id(session_id)
        agent = str(map_body.get("agent") or "").strip().lower()
        if agent not in cls.SESSION_AGENTS:
            raise ValidationError(f"unknown agent: {agent!r}")
        timestamp = cls.now_timestamp()
        return cls(
            agent=agent,
            session_id=session_id,
            account=AgentAccount.from_request(map_body),
            cwd=cls._optional_text(map_body.get("cwd")),
            machine=MachineInfo.from_mapping(map_body.get("machine")),
            git_branch=cls._optional_text(map_body.get("git_branch")),
            cli_version=cls._optional_text(map_body.get("cli_version")),
            transcript_path=cls._optional_text(map_body.get("transcript_path")),
            opened_at=timestamp,
            updated_at=timestamp,
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def project_directory_name(self) -> str:
        """Render the session's working directory as one stable directory name.

        The basename keeps the name readable while the digest of the full path
        keeps two same-named checkouts apart.

        Returns:
            a sanitized directory name derived from the session's working directory
        """
        if not self.cwd:
            return self.SESSION_PROJECT_UNKNOWN
        digest = hashlib.sha256(self.cwd.encode("utf-8")).hexdigest()
        basename = os.path.basename(self.cwd.rstrip("/")) or self.SESSION_PROJECT_UNKNOWN
        basename = self.SESSION_PROJECT_SANITIZE_PATTERN.sub("_", basename)
        basename = basename[: self.SESSION_PROJECT_NAME_MAX_CHARS].strip("_-")
        basename = basename or self.SESSION_PROJECT_UNKNOWN
        return f"{basename}-{digest[: self.SESSION_PROJECT_HASH_CHARS]}"

    def relative_directory(self) -> Path:
        """Render where this session's files live, relative to the data root.

        Returns:
            the per-session directory, split by agent, account and project
        """
        return Path(
            self.agent,
            self.account.directory_name(),
            self.project_directory_name(),
            self.session_id,
        )

    def as_payload(self) -> dict[str, object]:
        """Render the metadata as the JSON stored beside the transcript.

        Returns:
            a mapping of every recorded field
        """
        return {
            "agent": self.agent,
            "session_id": self.session_id,
            "account": {
                "uuid": self.account.uuid,
                "email": self.account.email,
                "label": self.account.label,
            },
            "cwd": self.cwd,
            "machine": self.machine.as_payload(),
            "git_branch": self.git_branch,
            "cli_version": self.cli_version,
            "transcript_path": self.transcript_path,
            "opened_at": self.opened_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
        }

    @classmethod
    def from_payload(cls, map_payload: dict[str, object]) -> "SessionMeta":
        """Rebuild metadata from a stored meta.json body.

        Args:
            map_payload: the decoded file contents

        Returns:
            the metadata it describes
        """
        map_account = map_payload.get("account")
        if not isinstance(map_account, dict):
            map_account = {}
        return cls(
            agent=str(map_payload.get("agent") or ""),
            session_id=str(map_payload.get("session_id") or ""),
            account=AgentAccount(
                uuid=str(map_account.get("uuid") or AgentAccount.ACCOUNT_ANONYMOUS_UUID),
                email=cls._optional_text(map_account.get("email")),
                label=cls._optional_text(map_account.get("label")),
            ),
            cwd=cls._optional_text(map_payload.get("cwd")),
            machine=MachineInfo.from_mapping(map_payload.get("machine")),
            git_branch=cls._optional_text(map_payload.get("git_branch")),
            cli_version=cls._optional_text(map_payload.get("cli_version")),
            transcript_path=cls._optional_text(map_payload.get("transcript_path")),
            opened_at=str(map_payload.get("opened_at") or ""),
            updated_at=str(map_payload.get("updated_at") or ""),
            closed_at=cls._optional_text(map_payload.get("closed_at")),
        )


@dataclass
class SessionQuery:
    """A filter over the stored sessions, as a reading client asks for them."""

    agent: str | None = None
    account: str | None = None
    hostname: str | None = None
    project: str | None = None
    branch: str | None = None
    status: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    time_field: str = "updated_at"
    limit: int = 100
    cursor: str | None = None

    QUERY_TIME_FIELDS = ("updated_at", "opened_at", "closed_at")
    QUERY_STATUSES = ("open", "closed")
    QUERY_DEFAULT_LIMIT = 100
    QUERY_MAX_LIMIT = 1000

    @staticmethod
    def parse_timestamp(text: str) -> datetime:
        """Read a point in time written either as ISO 8601 or as epoch seconds.

        Args:
            text: the value a client supplied

        Returns:
            the instant it names, in UTC

        Raises:
            ValidationError: if it names no instant
        """
        candidate = text.strip()
        if candidate.isdigit():
            return datetime.fromtimestamp(int(candidate), tz=timezone.utc)
        # fromisoformat only learned to read a trailing Z in 3.11.
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            moment = datetime.fromisoformat(candidate)
        except ValueError as err:
            raise ValidationError(f"unusable timestamp: {text!r}") from err
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    @classmethod
    def encode_cursor(cls, sort_key: str, session_id: str) -> str:
        """Render the position of one result as an opaque continuation token.

        Args:
            sort_key: the timestamp the results were ordered by
            session_id: the session at that position

        Returns:
            a token to hand back as `cursor` for the following page
        """
        raw = f"{sort_key}|{session_id}".encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode_cursor(cls, token: str) -> tuple[str, str]:
        """Read back a continuation token.

        Args:
            token: the token from a previous page

        Returns:
            the sort key and session id it names

        Raises:
            ValidationError: if the token was not produced by this server
        """
        padding = "=" * (-len(token) % 4)
        try:
            raw = base64.urlsafe_b64decode(token + padding).decode("utf-8")
            sort_key, session_id = raw.split("|", 1)
        except (ValueError, UnicodeDecodeError) as err:
            raise ValidationError(f"unusable cursor: {token!r}") from err
        return sort_key, session_id

    def sort_key_of(self, meta: "SessionMeta") -> str:
        """Read the timestamp this query orders and filters by.

        Args:
            meta: the session to read

        Returns:
            the chosen timestamp, empty when the session has none
        """
        return getattr(meta, self.time_field) or ""

    def matches(self, meta: "SessionMeta") -> bool:
        """Report whether one stored session satisfies this query.

        Args:
            meta: the session to test

        Returns:
            True when every supplied filter accepts it
        """
        if self.agent and meta.agent != self.agent:
            return False
        if self.account and meta.account.uuid != self.account:
            return False
        if self.hostname and self.hostname not in (
            meta.machine.hostname or "",
            meta.machine.fqdn or "",
        ):
            return False
        if self.project and self.project.lower() not in (meta.cwd or "").lower():
            return False
        if self.branch and meta.git_branch != self.branch:
            return False
        if self.status == "open" and meta.closed_at is not None:
            return False
        if self.status == "closed" and meta.closed_at is None:
            return False
        return self._matches_window(self.sort_key_of(meta))

    def _matches_window(self, sort_key: str) -> bool:
        if self.since is None and self.until is None:
            return True
        if not sort_key:
            return False
        moment = self.parse_timestamp(sort_key)
        if self.since is not None and moment < self.since:
            return False
        if self.until is not None and moment > self.until:
            return False
        return True


@dataclass
class SessionPage:
    """One page of query results, newest first."""

    list_sessions: list["SessionMeta"]
    next_cursor: str | None
    total_matched: int

    def as_payload(self) -> dict[str, object]:
        """Render the page as the JSON body returned to the client.

        Returns:
            a mapping of the results, the continuation token and the match count
        """
        return {
            "sessions": [meta.as_payload() for meta in self.list_sessions],
            "next_cursor": self.next_cursor,
            "total_matched": self.total_matched,
        }


@dataclass
class MachineSummary:
    """How much one machine has stored, for a reading client's filters."""

    hostname: str | None
    fqdn: str | None
    os: str | None
    arch: str | None
    session_count: int
    last_seen: str

    def as_payload(self) -> dict[str, object]:
        """Render the summary as JSON.

        Returns:
            a mapping of the machine and its session count
        """
        return {
            "hostname": self.hostname,
            "fqdn": self.fqdn,
            "os": self.os,
            "arch": self.arch,
            "session_count": self.session_count,
            "last_seen": self.last_seen,
        }


@dataclass
class AccountSummary:
    """How much one agent account has stored, for a reading client's filters."""

    agent: str
    uuid: str
    email: str | None
    label: str | None
    session_count: int
    last_seen: str

    def as_payload(self) -> dict[str, object]:
        """Render the summary as JSON.

        Returns:
            a mapping of the account and its session count
        """
        return {
            "agent": self.agent,
            "uuid": self.uuid,
            "email": self.email,
            "label": self.label,
            "session_count": self.session_count,
            "last_seen": self.last_seen,
        }


@dataclass
class TranscriptChunk:
    """A slice of one stored transcript, with the whole file's length."""

    data: bytes
    offset: int
    total: int


@dataclass
class SessionCursor:
    """How much of one session's transcript the server already holds."""

    cursor: int
    prefix_sha256: str

    def as_payload(self) -> dict[str, object]:
        """Render the cursor as the JSON body returned to the client.

        Returns:
            a mapping of the stored length and the digest of its leading bytes
        """
        return {
            "cursor": self.cursor,
            "prefix_sha256": self.prefix_sha256,
        }


class SessionStore:
    """An append-only store of agent session transcripts on the local disk."""

    STORE_TRANSCRIPT_NAME = "transcript.jsonl"
    STORE_META_NAME = "meta.json"
    STORE_LOCK_NAME = ".lock"
    STORE_INDEX_DIR = ".index"
    STORE_PREFIX_SAMPLE_BYTES = 4096
    STORE_DIR_MODE = 0o700
    STORE_FILE_MODE = 0o600

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root

    def begin(self, meta: SessionMeta) -> SessionCursor:
        """Register a session and report how much of it is already stored.

        Creating a session that already exists refreshes its metadata rather than
        failing, so a client may call this before every upload without tracking
        whether the server has seen the session before.

        Args:
            meta: the metadata reported by the client

        Returns:
            the stored length and leading-byte digest the client should resume from
        """
        directory = self._data_root / meta.relative_directory()
        directory.mkdir(parents=True, exist_ok=True)
        self._harden(directory)

        path_meta = directory / self.STORE_META_NAME
        meta_merged = meta
        if path_meta.exists():
            meta_stored = self._read_meta(path_meta)
            meta_merged.opened_at = meta_stored.opened_at or meta.opened_at
        self._write_meta(path_meta, meta_merged)
        self._write_index(meta.session_id, meta.relative_directory())
        return self.cursor_of(meta.session_id)

    def cursor_of(self, session_id: str) -> SessionCursor:
        """Report how much of a session's transcript is stored.

        Args:
            session_id: the session to inspect

        Returns:
            the stored length and the digest of its leading bytes

        Raises:
            ValidationError: if the session id is unusable
            NotFoundError: if the session was never registered
        """
        path_transcript = self._resolve(session_id) / self.STORE_TRANSCRIPT_NAME
        if not path_transcript.exists():
            return SessionCursor(cursor=0, prefix_sha256="")
        return SessionCursor(
            cursor=path_transcript.stat().st_size,
            prefix_sha256=self._prefix_digest(path_transcript),
        )

    def append(
        self,
        session_id: str,
        offset: int,
        chunk: bytes,
        do_reset: bool,
    ) -> SessionCursor:
        """Append freshly written transcript bytes to a stored session.

        Args:
            session_id: the session to extend
            offset: the stored length the client believes the server holds
            chunk: the bytes that follow that offset in the client's transcript
            do_reset: whether to rotate the stored transcript away and start over,
                which a client asks for when its own transcript was rewritten
                rather than appended to

        Returns:
            the stored length and leading-byte digest after the write

        Raises:
            ValidationError: if the session id is unusable or the offset is negative
            NotFoundError: if the session was never registered
            CursorConflictError: if the offset disagrees with the stored transcript
        """
        if offset < 0:
            raise ValidationError(f"negative offset: {offset}")
        directory = self._resolve(session_id)
        path_transcript = directory / self.STORE_TRANSCRIPT_NAME
        with self._locked(directory):
            if do_reset and path_transcript.exists():
                self._rotate(path_transcript)
            stored = path_transcript.stat().st_size if path_transcript.exists() else 0
            if offset != stored:
                raise CursorConflictError(
                    f"offset {offset} does not match stored length {stored}",
                    cursor=stored,
                    prefix_sha256=(
                        self._prefix_digest(path_transcript) if stored else ""
                    ),
                )
            with path_transcript.open("ab") as handle:
                handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            path_transcript.chmod(self.STORE_FILE_MODE)
            self._touch_meta(directory)
            return SessionCursor(
                cursor=path_transcript.stat().st_size,
                prefix_sha256=self._prefix_digest(path_transcript),
            )

    def close(self, session_id: str) -> SessionMeta:
        """Mark a session as finished.

        Args:
            session_id: the session to close

        Returns:
            the metadata as stored after closing

        Raises:
            ValidationError: if the session id is unusable
            NotFoundError: if the session was never registered
        """
        directory = self._resolve(session_id)
        path_meta = directory / self.STORE_META_NAME
        with self._locked(directory):
            meta = self._read_meta(path_meta)
            meta.closed_at = SessionMeta.now_timestamp()
            meta.updated_at = meta.closed_at
            self._write_meta(path_meta, meta)
        return meta

    def query(self, query: SessionQuery) -> SessionPage:
        """Select stored sessions, newest first, one page at a time.

        Args:
            query: the filters, ordering field and page position to apply

        Returns:
            the matching page and the token for the page after it
        """
        list_matched = [meta for meta in self._load_all() if query.matches(meta)]
        list_matched.sort(
            key=lambda meta: (query.sort_key_of(meta), meta.session_id),
            reverse=True,
        )
        if query.cursor:
            after_key, after_id = SessionQuery.decode_cursor(query.cursor)
            list_matched = [
                meta
                for meta in list_matched
                if (query.sort_key_of(meta), meta.session_id) < (after_key, after_id)
            ]
        list_page = list_matched[: query.limit]
        next_cursor = None
        if len(list_matched) > len(list_page) and list_page:
            last = list_page[-1]
            next_cursor = SessionQuery.encode_cursor(
                query.sort_key_of(last),
                last.session_id,
            )
        return SessionPage(
            list_sessions=list_page,
            next_cursor=next_cursor,
            total_matched=len(list_matched),
        )

    def meta_of(self, session_id: str) -> SessionMeta:
        """Read one stored session's metadata.

        Args:
            session_id: the session to read

        Returns:
            its metadata

        Raises:
            ValidationError: if the session id is unusable
            NotFoundError: if the session was never registered
        """
        return self._read_meta(self._resolve(session_id) / self.STORE_META_NAME)

    def read_transcript(
        self,
        session_id: str,
        offset: int,
        limit: int | None,
    ) -> TranscriptChunk:
        """Read a stored transcript, or the part of it that follows an offset.

        Args:
            session_id: the session to read
            offset: the byte to start at, which a client polling for new
                output sets to the length it already holds
            limit: the greatest number of bytes to return, or None for the rest

        Returns:
            the bytes read, where they started and the whole file's length

        Raises:
            ValidationError: if the session id or the offset is unusable
            NotFoundError: if the session was never registered
        """
        if offset < 0:
            raise ValidationError(f"negative offset: {offset}")
        path_transcript = self._resolve(session_id) / self.STORE_TRANSCRIPT_NAME
        if not path_transcript.exists():
            return TranscriptChunk(data=b"", offset=offset, total=0)
        total = path_transcript.stat().st_size
        if offset >= total:
            return TranscriptChunk(data=b"", offset=offset, total=total)
        with path_transcript.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(limit) if limit is not None else handle.read()
        return TranscriptChunk(data=data, offset=offset, total=total)

    def list_machines(self) -> list[MachineSummary]:
        """Summarise the machines that have stored sessions here.

        Returns:
            one summary per machine, busiest first
        """
        map_counts: dict[str, list[SessionMeta]] = {}
        for meta in self._load_all():
            map_counts.setdefault(meta.machine.hostname or "", []).append(meta)
        list_summary = [
            MachineSummary(
                hostname=list_meta[0].machine.hostname,
                fqdn=list_meta[0].machine.fqdn,
                os=list_meta[0].machine.os,
                arch=list_meta[0].machine.arch,
                session_count=len(list_meta),
                last_seen=max(meta.updated_at for meta in list_meta),
            )
            for list_meta in map_counts.values()
        ]
        list_summary.sort(
            key=lambda summary: (summary.session_count, summary.last_seen),
            reverse=True,
        )
        return list_summary

    def list_accounts(self) -> list[AccountSummary]:
        """Summarise the agent accounts that have stored sessions here.

        Returns:
            one summary per agent and account pair, busiest first
        """
        map_counts: dict[tuple[str, str], list[SessionMeta]] = {}
        for meta in self._load_all():
            map_counts.setdefault((meta.agent, meta.account.uuid), []).append(meta)
        list_summary = [
            AccountSummary(
                agent=agent,
                uuid=uuid,
                email=list_meta[0].account.email,
                label=list_meta[0].account.label,
                session_count=len(list_meta),
                last_seen=max(meta.updated_at for meta in list_meta),
            )
            for (agent, uuid), list_meta in map_counts.items()
        ]
        list_summary.sort(
            key=lambda summary: (summary.session_count, summary.last_seen),
            reverse=True,
        )
        return list_summary

    def _load_all(self) -> list[SessionMeta]:
        path_index = self._data_root / self.STORE_INDEX_DIR
        if not path_index.is_dir():
            return []
        list_meta: list[SessionMeta] = []
        for entry in path_index.iterdir():
            if not entry.is_file():
                continue
            path_meta = (
                self._data_root
                / entry.read_text(encoding="utf-8").strip()
                / self.STORE_META_NAME
            )
            if not path_meta.exists():
                continue
            list_meta.append(self._read_meta(path_meta))
        return list_meta

    def _resolve(self, session_id: str) -> Path:
        SessionMeta.validate_session_id(session_id)
        path_index = self._data_root / self.STORE_INDEX_DIR / session_id
        if not path_index.is_file():
            raise NotFoundError(f"unknown session: {session_id}")
        relative = Path(path_index.read_text(encoding="utf-8").strip())
        directory = self._data_root / relative
        if not directory.is_dir():
            raise NotFoundError(f"unknown session: {session_id}")
        return directory

    def _write_index(self, session_id: str, relative: Path) -> None:
        path_index = self._data_root / self.STORE_INDEX_DIR
        path_index.mkdir(parents=True, exist_ok=True)
        self._harden(path_index)
        path_entry = path_index / session_id
        path_entry.write_text(f"{relative}\n", encoding="utf-8")
        path_entry.chmod(self.STORE_FILE_MODE)

    def _read_meta(self, path_meta: Path) -> SessionMeta:
        try:
            with path_meta.open(encoding="utf-8") as handle:
                return SessionMeta.from_payload(json.load(handle))
        except FileNotFoundError as err:
            raise NotFoundError(f"no metadata at {path_meta}") from err
        except json.JSONDecodeError as err:
            raise NotFoundError(f"malformed metadata at {path_meta}") from err

    def _write_meta(self, path_meta: Path, meta: SessionMeta) -> None:
        meta.updated_at = SessionMeta.now_timestamp()
        with path_meta.open("w", encoding="utf-8") as handle:
            json.dump(meta.as_payload(), handle, indent=2)
            handle.write("\n")
        path_meta.chmod(self.STORE_FILE_MODE)

    def _touch_meta(self, directory: Path) -> None:
        path_meta = directory / self.STORE_META_NAME
        if path_meta.exists():
            self._write_meta(path_meta, self._read_meta(path_meta))

    def _rotate(self, path_transcript: Path) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path_transcript.rename(path_transcript.with_suffix(f".jsonl.{stamp}"))

    def _prefix_digest(self, path_transcript: Path) -> str:
        with path_transcript.open("rb") as handle:
            return hashlib.sha256(handle.read(self.STORE_PREFIX_SAMPLE_BYTES)).hexdigest()

    def _harden(self, directory: Path) -> None:
        directory.chmod(self.STORE_DIR_MODE)

    @contextmanager
    def _locked(self, directory: Path) -> Iterator[None]:
        path_lock = directory / self.STORE_LOCK_NAME
        handle = path_lock.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            handle.close()
