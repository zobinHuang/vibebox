import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from vboxd.errors import NotFoundError, ValidationError
from vboxd.records import RecordStore


@dataclass
class SkillSource:
    """Where one skill lives: a git repository, a path inside it, and a ref."""

    repo_url: str
    skill_path: str = ""
    ref: str = ""

    SOURCE_DEFAULT_HOST = "github.com"
    SOURCE_ALLOWED_SCHEMES = ("https", "http", "ssh", "git")
    SOURCE_SHORTHAND_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    SOURCE_SCP_PATTERN = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[A-Za-z0-9._/-]+$")
    SOURCE_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")
    SOURCE_REF_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,128}$")
    SOURCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")

    @classmethod
    def validate_skill_path(cls, skill_path: str) -> str:
        """Check that a path inside a repository is safe for a client to check out.

        The client turns this into a sparse-checkout pattern and a local
        directory, so a `..` segment here would let it write outside the tree it
        meant to.

        Args:
            skill_path: the path inside the repository

        Returns:
            the path unchanged

        Raises:
            ValidationError: if the path could escape the repository or the
                client's own skill directory
        """
        if not cls.SOURCE_PATH_PATTERN.match(skill_path):
            raise ValidationError(f"unusable skill path: {skill_path!r}")
        for segment in skill_path.split("/"):
            if segment in ("", ".", ".."):
                raise ValidationError(f"unusable skill path: {skill_path!r}")
        return skill_path

    @classmethod
    def parse(cls, spec: str) -> "SkillSource":
        """Read a skill source written the way a repository of skills is cited.

        Accepts `owner/repo`, `owner/repo@skill`, `owner/repo@dir/skill`, a full
        https, ssh or scp-style git URL, and a `#ref` suffix on any of them.

        Args:
            spec: the source as a person would write it

        Returns:
            the repository, the path inside it and the ref it names

        Raises:
            ValidationError: if the spec names no repository this server will fetch
        """
        text = spec.strip()
        if not text:
            raise ValidationError("skill source is empty")

        ref = ""
        if "#" in text:
            text, _, ref = text.partition("#")
            ref = ref.strip()
            if ref and not cls.SOURCE_REF_PATTERN.match(ref):
                raise ValidationError(f"unusable ref: {ref!r}")

        skill_path = ""
        if "@" in text:
            head, _, tail = text.rpartition("@")
            # A scp-style URL carries an @ of its own, before the host; only an @
            # whose remainder is a plain path separates the skill.
            if head and ":" not in tail and "//" not in tail:
                text, skill_path = head, tail.strip("/")
                if skill_path:
                    cls.validate_skill_path(skill_path)

        return cls(
            repo_url=cls._normalize_repo_url(text.strip()),
            skill_path=skill_path,
            ref=ref,
        )

    @classmethod
    def _normalize_repo_url(cls, text: str) -> str:
        if cls.SOURCE_SHORTHAND_PATTERN.match(text):
            return f"https://{cls.SOURCE_DEFAULT_HOST}/{text}"
        if cls.SOURCE_SCP_PATTERN.match(text):
            return text
        # Anything handed to git must be a remote this server is willing to
        # contact. A leading dash would be read as an option, and the ext::
        # and file:: transports run local commands or read local paths.
        if text.startswith("-"):
            raise ValidationError(f"unusable repository: {text!r}")
        parts = urlsplit(text)
        if parts.scheme not in cls.SOURCE_ALLOWED_SCHEMES or not parts.netloc:
            raise ValidationError(
                f"unusable repository: {text!r}"
                " (expected owner/repo, or an https, ssh or scp-style git URL)"
            )
        return text

    def host(self) -> str:
        """Read the host the repository is served from.

        Returns:
            the host name, or an empty string if the URL carries none
        """
        if self.SOURCE_SCP_PATTERN.match(self.repo_url):
            return self.repo_url.split("@", 1)[1].split(":", 1)[0]
        return urlsplit(self.repo_url).netloc

    def repo_slug(self) -> str:
        """Read the owner-and-name part of the repository path.

        Returns:
            the repository path without a host, scheme or .git suffix
        """
        if self.SOURCE_SCP_PATTERN.match(self.repo_url):
            tail = self.repo_url.split(":", 1)[1]
        else:
            tail = urlsplit(self.repo_url).path
        return tail.strip("/").removesuffix(".git")

    def identifier(self) -> str:
        """Render a stable identifier for the skill this source names.

        The identifier is derived from the repository and the path inside it, and
        never from the ref, so re-registering the same skill at another version
        updates one record rather than creating a second.

        Returns:
            the store identifier for this skill
        """
        parts = [self.host(), self.repo_slug()]
        if self.skill_path:
            parts.append(self.skill_path)
        return RecordStore.slugify("-".join(parts), fallback="skill")

    def as_payload(self) -> dict[str, object]:
        """Render the source as JSON.

        Returns:
            a mapping of the repository, the path inside it and the ref
        """
        return {
            "repo_url": self.repo_url,
            "skill_path": self.skill_path,
            "ref": self.ref,
        }


@dataclass
class SkillVersion:
    """One version of a skill: a ref and the commit it pointed at when resolved."""

    ref: str
    commit: str
    resolved_at: str

    def as_payload(self) -> dict[str, object]:
        """Render the version as JSON.

        Returns:
            a mapping of the ref, its commit and when the two were tied together
        """
        return {
            "ref": self.ref,
            "commit": self.commit,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_payload(cls, map_payload: dict[str, object]) -> "SkillVersion":
        """Rebuild a version from a stored record.

        Args:
            map_payload: the decoded version mapping

        Returns:
            the version it describes
        """
        return cls(
            ref=str(map_payload.get("ref") or ""),
            commit=str(map_payload.get("commit") or ""),
            resolved_at=str(map_payload.get("resolved_at") or ""),
        )


class GitRefResolver:
    """Turns a repository ref into a commit without cloning anything."""

    RESOLVER_TIMEOUT_SECONDS = 30
    RESOLVER_HEAD_PREFIX = "ref: "

    def __init__(self, timeout_seconds: int = RESOLVER_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    def resolve(self, repo_url: str, ref: str) -> SkillVersion:
        """Resolve a ref, or a commit written out in full, to a concrete commit.

        Args:
            repo_url: the repository to ask
            ref: a branch, a tag, a commit, or an empty string for the
                repository's own default branch

        Returns:
            the ref and the commit it currently points at

        Raises:
            ValidationError: if the repository cannot be reached or names no
                such ref
        """
        if SkillSource.SOURCE_COMMIT_PATTERN.match(ref):
            return SkillVersion(
                ref=ref,
                commit=ref,
                resolved_at=self.now_timestamp(),
            )
        if not ref:
            ref = self.default_branch(repo_url)
        map_refs = self._list_remote(repo_url, ref)
        for candidate in (
            f"refs/heads/{ref}",
            f"refs/tags/{ref}",
            f"refs/tags/{ref}^{{}}",
            ref,
        ):
            if candidate in map_refs:
                return SkillVersion(
                    ref=ref,
                    commit=map_refs[candidate],
                    resolved_at=self.now_timestamp(),
                )
        raise ValidationError(f"{repo_url} has no ref {ref!r}")

    def default_branch(self, repo_url: str) -> str:
        """Read the branch a repository hands out by default.

        Args:
            repo_url: the repository to ask

        Returns:
            the default branch name

        Raises:
            ValidationError: if the repository cannot be reached
        """
        for line in self._run(["ls-remote", "--symref", repo_url, "HEAD"]):
            if line.startswith(self.RESOLVER_HEAD_PREFIX):
                target = line[len(self.RESOLVER_HEAD_PREFIX):].split()[0]
                return target.removeprefix("refs/heads/")
        raise ValidationError(f"{repo_url} reports no default branch")

    def list_refs(self, repo_url: str) -> dict[str, str]:
        """List every branch and tag a repository publishes.

        Args:
            repo_url: the repository to ask

        Returns:
            a mapping of full ref name to commit

        Raises:
            ValidationError: if the repository cannot be reached
        """
        return self._list_remote(repo_url, None)

    @staticmethod
    def now_timestamp() -> str:
        """Produce the timestamp format used throughout the stored records.

        Returns:
            the current time as an ISO 8601 string in UTC
        """
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _list_remote(self, repo_url: str, ref: str | None) -> dict[str, str]:
        list_command = ["ls-remote", "--heads", "--tags", repo_url]
        if ref:
            list_command.append(ref)
        map_refs: dict[str, str] = {}
        for line in self._run(list_command):
            list_fields = line.split("\t")
            if len(list_fields) == 2:
                map_refs[list_fields[1]] = list_fields[0]
        return map_refs

    def _run(self, list_arguments: list[str]) -> list[str]:
        try:
            completed = subprocess.run(
                ["git", *list_arguments],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                # git must never stop to ask for credentials: a private
                # repository has to fail rather than hang the request.
                env={
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ASKPASS": "",
                    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                    "HOME": str(Path.home()),
                },
                check=False,
            )
        except FileNotFoundError as err:
            raise ValidationError("git is not installed on this server") from err
        except subprocess.TimeoutExpired as err:
            raise ValidationError(
                f"git timed out after {self._timeout_seconds}s talking to the repository"
            ) from err
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            raise ValidationError(
                f"git could not read the repository: {detail[-1] if detail else 'unknown error'}"
            )
        return [line for line in completed.stdout.splitlines() if line.strip()]


@dataclass
class SkillRecord:
    """One entry in the skill index: where a skill lives, and its known versions."""

    id: str
    source: str
    repo_url: str
    skill_path: str = ""
    name: str = ""
    description: str = ""
    default_ref: str = ""
    list_versions: list[SkillVersion] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    SKILL_VERSION_HISTORY_MAX = 100

    def latest_version(self) -> SkillVersion | None:
        """Read the most recently resolved version of this skill.

        Returns:
            the newest recorded version, or None if none has been resolved yet
        """
        if not self.list_versions:
            return None
        return self.list_versions[-1]

    def version_for(self, commit: str) -> SkillVersion | None:
        """Find a recorded version by its commit.

        Only a commit is matched here, never a ref. A ref is a pointer that
        moves, so resolving one against whatever this index happens to have
        recorded would make the same pin mean different things on different
        servers; a ref always goes back to the repository instead.

        Args:
            commit: a commit, written in full or abbreviated

        Returns:
            the matching version, newest first, or None if none matches
        """
        for version in reversed(self.list_versions):
            if version.commit == commit or version.commit.startswith(commit):
                return version
        return None

    def record_version(self, version: SkillVersion) -> bool:
        """Add a freshly resolved version, unless it is the one already on top.

        Args:
            version: the version just resolved

        Returns:
            True when the version was new, False when it repeated the newest one
        """
        latest = self.latest_version()
        if latest is not None and latest.commit == version.commit and latest.ref == version.ref:
            return False
        self.list_versions.append(version)
        del self.list_versions[: -self.SKILL_VERSION_HISTORY_MAX]
        return True

    def as_payload(self) -> dict[str, object]:
        """Render the record as the JSON stored and served for this skill.

        Returns:
            a mapping of every recorded field
        """
        return {
            "id": self.id,
            "source": self.source,
            "repo_url": self.repo_url,
            "skill_path": self.skill_path,
            "name": self.name,
            "description": self.description,
            "default_ref": self.default_ref,
            "versions": [version.as_payload() for version in self.list_versions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, map_payload: dict[str, object]) -> "SkillRecord":
        """Rebuild a record from its stored JSON.

        Args:
            map_payload: the decoded record

        Returns:
            the record it describes
        """
        list_raw = map_payload.get("versions")
        list_versions = [
            SkillVersion.from_payload(item)
            for item in (list_raw if isinstance(list_raw, list) else [])
            if isinstance(item, dict)
        ]
        return cls(
            id=str(map_payload.get("id") or ""),
            source=str(map_payload.get("source") or ""),
            repo_url=str(map_payload.get("repo_url") or ""),
            skill_path=str(map_payload.get("skill_path") or ""),
            name=str(map_payload.get("name") or ""),
            description=str(map_payload.get("description") or ""),
            default_ref=str(map_payload.get("default_ref") or ""),
            list_versions=list_versions,
            created_at=str(map_payload.get("created_at") or ""),
            updated_at=str(map_payload.get("updated_at") or ""),
        )


class SkillIndex:
    """The index of skills this server knows about; it holds no skill files."""

    INDEX_DIR_NAME = "skills"
    INDEX_KIND = "skill"
    INDEX_PIN_LATEST = "latest"
    INDEX_QUERY_DEFAULT_LIMIT = 200

    def __init__(self, data_root: Path, resolver: GitRefResolver | None = None) -> None:
        self._store = RecordStore(data_root / self.INDEX_DIR_NAME, self.INDEX_KIND)
        self._resolver = resolver or GitRefResolver()

    def register(self, map_body: dict[str, object]) -> SkillRecord:
        """Add a skill to the index, or refresh the entry that already names it.

        The ref is resolved to a commit as part of registering, so a record is
        never stored without at least one usable version.

        Args:
            map_body: the request body, carrying either a `source` spec or an
                explicit `repo_url` with an optional `skill_path` and `ref`

        Returns:
            the stored record

        Raises:
            ValidationError: if the source names no repository, or its ref
                cannot be resolved
        """
        source = self._source_from(map_body)
        record_id = source.identifier()
        timestamp = GitRefResolver.now_timestamp()
        with self._store.locked():
            if self._store.exists(record_id):
                record = SkillRecord.from_payload(self._store.read(record_id))
                record.source = str(map_body.get("source") or record.source)
                record.repo_url = source.repo_url
                record.skill_path = source.skill_path
            else:
                record = SkillRecord(
                    id=record_id,
                    source=str(map_body.get("source") or source.repo_url),
                    repo_url=source.repo_url,
                    skill_path=source.skill_path,
                    created_at=timestamp,
                )
            if source.ref:
                record.default_ref = source.ref
            if not record.default_ref:
                record.default_ref = self._resolver.default_branch(source.repo_url)
            for name in ("name", "description"):
                value = map_body.get(name)
                if value:
                    setattr(record, name, str(value).strip())
            if not record.name:
                record.name = source.skill_path.rsplit("/", 1)[-1] or source.repo_slug()
            record.record_version(
                self._resolver.resolve(source.repo_url, record.default_ref)
            )
            record.updated_at = GitRefResolver.now_timestamp()
            self._store.write(record_id, record.as_payload())
        return record

    def get(self, record_id: str) -> SkillRecord:
        """Read one indexed skill.

        Args:
            record_id: the skill to read

        Returns:
            its record

        Raises:
            ValidationError: if the identifier is unusable
            NotFoundError: if no such skill is indexed
        """
        return SkillRecord.from_payload(self._store.read(record_id))

    def update(self, record_id: str, map_body: dict[str, object]) -> SkillRecord:
        """Change an indexed skill's description, name or tracked branch.

        Args:
            record_id: the skill to change
            map_body: the fields to replace

        Returns:
            the stored record

        Raises:
            ValidationError: if the identifier or a field is unusable
            NotFoundError: if no such skill is indexed
        """
        with self._store.locked():
            record = SkillRecord.from_payload(self._store.read(record_id))
            for name in ("name", "description"):
                if name in map_body:
                    setattr(record, name, str(map_body.get(name) or "").strip())
            if "default_ref" in map_body:
                ref = str(map_body.get("default_ref") or "").strip()
                if ref and not SkillSource.SOURCE_REF_PATTERN.match(ref):
                    raise ValidationError(f"unusable ref: {ref!r}")
                record.default_ref = ref or self._resolver.default_branch(record.repo_url)
                record.record_version(
                    self._resolver.resolve(record.repo_url, record.default_ref)
                )
            record.updated_at = GitRefResolver.now_timestamp()
            self._store.write(record_id, record.as_payload())
        return record

    def delete(self, record_id: str) -> None:
        """Remove a skill from the index.

        Args:
            record_id: the skill to remove

        Raises:
            ValidationError: if the identifier is unusable
            NotFoundError: if no such skill is indexed
        """
        with self._store.locked():
            self._store.delete(record_id)

    def query(
        self,
        text: str | None = None,
        repo: str | None = None,
        limit: int = INDEX_QUERY_DEFAULT_LIMIT,
    ) -> list[SkillRecord]:
        """Select indexed skills, most recently updated first.

        Args:
            text: case-insensitive substring matched against name and description
            repo: case-insensitive substring matched against the repository URL
            limit: the greatest number of records to return

        Returns:
            the matching records
        """
        list_record = [
            SkillRecord.from_payload(map_record)
            for map_record in self._store.list_all()
        ]
        if text:
            needle = text.lower()
            list_record = [
                record
                for record in list_record
                if needle in record.name.lower()
                or needle in record.description.lower()
                or needle in record.id
            ]
        if repo:
            needle = repo.lower()
            list_record = [
                record for record in list_record if needle in record.repo_url.lower()
            ]
        list_record.sort(
            key=lambda record: (record.updated_at, record.id),
            reverse=True,
        )
        return list_record[:limit]

    def resolve_version(self, record_id: str, pin: str) -> tuple[SkillRecord, SkillVersion]:
        """Decide which commit of a skill a pin names, asking the repository if needed.

        `latest` re-resolves the tracked branch, so installing always picks up
        what upstream has now. A commit is immutable and is answered from the
        recorded versions when they hold it. A ref is a pointer that moves, so it
        is always re-resolved against the repository.

        Args:
            record_id: the skill to resolve
            pin: `latest`, a ref, or a commit

        Returns:
            the record and the version the pin names

        Raises:
            ValidationError: if the pin names no resolvable version
            NotFoundError: if no such skill is indexed
        """
        record = self.get(record_id)
        if pin in ("", self.INDEX_PIN_LATEST):
            version = self._resolver.resolve(record.repo_url, record.default_ref)
            if record.record_version(version):
                with self._store.locked():
                    stored = SkillRecord.from_payload(self._store.read(record_id))
                    if stored.record_version(version):
                        stored.updated_at = GitRefResolver.now_timestamp()
                        self._store.write(record_id, stored.as_payload())
            return record, version
        if SkillSource.SOURCE_COMMIT_PATTERN.match(pin):
            recorded = record.version_for(pin)
            if recorded is not None:
                return record, recorded
        return record, self._resolver.resolve(record.repo_url, pin)

    def _source_from(self, map_body: dict[str, object]) -> SkillSource:
        spec = map_body.get("source")
        if spec:
            source = SkillSource.parse(str(spec))
        else:
            repo_url = str(map_body.get("repo_url") or "").strip()
            if not repo_url:
                raise ValidationError("a skill needs a source or a repo_url")
            source = SkillSource.parse(repo_url)
            skill_path = str(map_body.get("skill_path") or "").strip("/")
            if skill_path:
                source.skill_path = SkillSource.validate_skill_path(skill_path)
        ref = str(map_body.get("ref") or "").strip()
        if ref:
            if not SkillSource.SOURCE_REF_PATTERN.match(ref):
                raise ValidationError(f"unusable ref: {ref!r}")
            source.ref = ref
        return source
