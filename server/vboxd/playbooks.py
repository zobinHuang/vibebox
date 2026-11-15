from dataclasses import dataclass, field
from pathlib import Path
from vboxd.errors import ConflictError, NotFoundError, ValidationError
from vboxd.records import RecordStore
from vboxd.skills import GitRefResolver, SkillIndex, SkillRecord, SkillSource, SkillVersion


@dataclass
class SkillPin:
    """One skill a playbook loads, and which version of it."""

    skill_id: str
    pin: str = "latest"

    PIN_LATEST = "latest"

    @classmethod
    def from_payload(cls, map_payload: object) -> "SkillPin":
        """Read one pinned skill out of a request body or a stored record.

        A bare string is read as a skill id tracking the latest version, which
        is the common case and keeps a playbook readable when written by hand.

        Args:
            map_payload: the decoded entry, either a string or a mapping

        Returns:
            the pinned skill

        Raises:
            ValidationError: if the entry names no skill, or an unusable version
        """
        if isinstance(map_payload, str):
            return cls(skill_id=map_payload.strip(), pin=cls.PIN_LATEST)
        if not isinstance(map_payload, dict):
            raise ValidationError("a playbook skill is neither an id nor an object")
        skill_id = str(map_payload.get("skill_id") or "").strip()
        if not skill_id:
            raise ValidationError("a playbook skill carries no skill_id")
        pin = str(map_payload.get("pin") or cls.PIN_LATEST).strip() or cls.PIN_LATEST
        if pin != cls.PIN_LATEST and not (
            SkillSource.SOURCE_REF_PATTERN.match(pin)
            or SkillSource.SOURCE_COMMIT_PATTERN.match(pin)
        ):
            raise ValidationError(f"unusable pin: {pin!r}")
        return cls(skill_id=skill_id, pin=pin)

    def as_payload(self) -> dict[str, object]:
        """Render the pinned skill as JSON.

        Returns:
            a mapping of the skill id and the version pinned
        """
        return {
            "skill_id": self.skill_id,
            "pin": self.pin,
        }


@dataclass
class PlaybookRecord:
    """A named set of skills, with the standing instructions that go with them."""

    id: str
    name: str
    description: str = ""
    prompt: str = ""
    list_pins: list[SkillPin] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    PLAYBOOK_NAME_MAX_CHARS = 120
    PLAYBOOK_PROMPT_MAX_CHARS = 20000
    PLAYBOOK_SKILLS_MAX = 100

    @classmethod
    def validate_name(cls, name: str) -> str:
        """Check that a playbook name is present and of a sane length.

        Args:
            name: the name as supplied

        Returns:
            the trimmed name

        Raises:
            ValidationError: if it is empty or too long
        """
        trimmed = name.strip()
        if not trimmed:
            raise ValidationError("a playbook needs a name")
        if len(trimmed) > cls.PLAYBOOK_NAME_MAX_CHARS:
            raise ValidationError(
                f"playbook name is longer than {cls.PLAYBOOK_NAME_MAX_CHARS} characters"
            )
        return trimmed

    @classmethod
    def validate_prompt(cls, prompt: str) -> str:
        """Check that a playbook's standing instructions fit the stored limit.

        Args:
            prompt: the instructions as supplied

        Returns:
            the instructions unchanged

        Raises:
            ValidationError: if they are too long to inject into a session
        """
        if len(prompt) > cls.PLAYBOOK_PROMPT_MAX_CHARS:
            raise ValidationError(
                f"playbook prompt is longer than {cls.PLAYBOOK_PROMPT_MAX_CHARS} characters"
            )
        return prompt

    @classmethod
    def pins_from_payload(cls, list_raw: object) -> list[SkillPin]:
        """Read a playbook's skill list out of a request body.

        Args:
            list_raw: the decoded `skills` value

        Returns:
            the pinned skills, in the order given, with duplicates dropped

        Raises:
            ValidationError: if the value is not a list, holds an unusable
                entry, or names more skills than a playbook may carry
        """
        if list_raw is None:
            return []
        if not isinstance(list_raw, list):
            raise ValidationError("a playbook's skills must be a list")
        if len(list_raw) > cls.PLAYBOOK_SKILLS_MAX:
            raise ValidationError(
                f"a playbook carries at most {cls.PLAYBOOK_SKILLS_MAX} skills"
            )
        list_pins: list[SkillPin] = []
        set_seen: set[str] = set()
        for item in list_raw:
            pin = SkillPin.from_payload(item)
            if pin.skill_id in set_seen:
                continue
            set_seen.add(pin.skill_id)
            list_pins.append(pin)
        return list_pins

    def as_payload(self) -> dict[str, object]:
        """Render the playbook as the JSON stored and served for it.

        Returns:
            a mapping of every recorded field
        """
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "skills": [pin.as_payload() for pin in self.list_pins],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, map_payload: dict[str, object]) -> "PlaybookRecord":
        """Rebuild a playbook from its stored JSON.

        Args:
            map_payload: the decoded record

        Returns:
            the playbook it describes
        """
        return cls(
            id=str(map_payload.get("id") or ""),
            name=str(map_payload.get("name") or ""),
            description=str(map_payload.get("description") or ""),
            prompt=str(map_payload.get("prompt") or ""),
            list_pins=cls.pins_from_payload(map_payload.get("skills")),
            created_at=str(map_payload.get("created_at") or ""),
            updated_at=str(map_payload.get("updated_at") or ""),
        )


@dataclass
class ResolvedSkill:
    """One skill of a playbook, pinned to a commit and ready for a client to fetch."""

    skill_id: str
    name: str
    description: str
    repo_url: str
    skill_path: str
    ref: str
    commit: str
    pin: str

    @classmethod
    def of(
        cls,
        record: SkillRecord,
        version: SkillVersion,
        pin: str,
    ) -> "ResolvedSkill":
        """Pair an indexed skill with the version a pin resolved to.

        Args:
            record: the indexed skill
            version: the version its pin resolved to
            pin: the pin as the playbook wrote it

        Returns:
            everything a client needs in order to fetch this skill
        """
        return cls(
            skill_id=record.id,
            name=record.name,
            description=record.description,
            repo_url=record.repo_url,
            skill_path=record.skill_path,
            ref=version.ref,
            commit=version.commit,
            pin=pin,
        )

    def as_payload(self) -> dict[str, object]:
        """Render the resolved skill as JSON.

        Returns:
            a mapping of the repository, the path, the commit and the metadata
        """
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "repo_url": self.repo_url,
            "skill_path": self.skill_path,
            "ref": self.ref,
            "commit": self.commit,
            "pin": self.pin,
        }


@dataclass
class ResolvedPlaybook:
    """A playbook with every skill pinned to a concrete commit."""

    playbook: PlaybookRecord
    list_skills: list[ResolvedSkill]
    list_unresolved: list[dict[str, object]]
    resolved_at: str

    def as_payload(self) -> dict[str, object]:
        """Render the resolution as the JSON a client installs from.

        Returns:
            the playbook, its pinned skills, and whatever could not be resolved
        """
        return {
            "id": self.playbook.id,
            "name": self.playbook.name,
            "description": self.playbook.description,
            "prompt": self.playbook.prompt,
            "skills": [skill.as_payload() for skill in self.list_skills],
            "unresolved": self.list_unresolved,
            "resolved_at": self.resolved_at,
        }


class PlaybookIndex:
    """The index of playbooks this server holds."""

    INDEX_DIR_NAME = "playbooks"
    INDEX_KIND = "playbook"
    INDEX_QUERY_DEFAULT_LIMIT = 200

    def __init__(self, data_root: Path, skills: SkillIndex) -> None:
        self._store = RecordStore(data_root / self.INDEX_DIR_NAME, self.INDEX_KIND)
        self._skills = skills

    def create(self, map_body: dict[str, object]) -> PlaybookRecord:
        """Add a playbook to the index.

        Args:
            map_body: the request body, carrying a name and optionally a
                description, a prompt, an explicit id and a list of skills

        Returns:
            the stored playbook

        Raises:
            ValidationError: if a field is missing or unusable
            ConflictError: if a playbook of that id already exists
        """
        name = PlaybookRecord.validate_name(str(map_body.get("name") or ""))
        record_id = str(map_body.get("id") or "").strip() or RecordStore.slugify(
            name,
            fallback="playbook",
        )
        self._store.validate_id(record_id)
        timestamp = GitRefResolver.now_timestamp()
        with self._store.locked():
            if self._store.exists(record_id):
                raise ConflictError(f"playbook {record_id} already exists")
            record = PlaybookRecord(
                id=record_id,
                name=name,
                description=str(map_body.get("description") or "").strip(),
                prompt=PlaybookRecord.validate_prompt(str(map_body.get("prompt") or "")),
                list_pins=PlaybookRecord.pins_from_payload(map_body.get("skills")),
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._store.write(record_id, record.as_payload())
        return record

    def get(self, record_id: str) -> PlaybookRecord:
        """Read one playbook.

        Args:
            record_id: the playbook to read

        Returns:
            its record

        Raises:
            ValidationError: if the identifier is unusable
            NotFoundError: if no such playbook is stored
        """
        return PlaybookRecord.from_payload(self._store.read(record_id))

    def update(self, record_id: str, map_body: dict[str, object]) -> PlaybookRecord:
        """Change a playbook's name, description, instructions or skill list.

        Args:
            record_id: the playbook to change
            map_body: the fields to replace; a field left out is left alone

        Returns:
            the stored playbook

        Raises:
            ValidationError: if the identifier or a field is unusable
            NotFoundError: if no such playbook is stored
        """
        with self._store.locked():
            record = PlaybookRecord.from_payload(self._store.read(record_id))
            if "name" in map_body:
                record.name = PlaybookRecord.validate_name(str(map_body.get("name") or ""))
            if "description" in map_body:
                record.description = str(map_body.get("description") or "").strip()
            if "prompt" in map_body:
                record.prompt = PlaybookRecord.validate_prompt(
                    str(map_body.get("prompt") or "")
                )
            if "skills" in map_body:
                record.list_pins = PlaybookRecord.pins_from_payload(map_body.get("skills"))
            record.updated_at = GitRefResolver.now_timestamp()
            self._store.write(record_id, record.as_payload())
        return record

    def delete(self, record_id: str) -> None:
        """Remove a playbook.

        Args:
            record_id: the playbook to remove

        Raises:
            ValidationError: if the identifier is unusable
            NotFoundError: if no such playbook is stored
        """
        with self._store.locked():
            self._store.delete(record_id)

    def query(
        self,
        text: str | None = None,
        limit: int = INDEX_QUERY_DEFAULT_LIMIT,
    ) -> list[PlaybookRecord]:
        """Select playbooks, most recently updated first.

        Args:
            text: case-insensitive substring matched against name and description
            limit: the greatest number of playbooks to return

        Returns:
            the matching playbooks
        """
        list_record = [
            PlaybookRecord.from_payload(map_record)
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
        list_record.sort(
            key=lambda record: (record.updated_at, record.id),
            reverse=True,
        )
        return list_record[:limit]

    def resolve(self, record_id: str) -> ResolvedPlaybook:
        """Pin every skill of a playbook to a commit, asking upstream as needed.

        This is what a client installs from: a pin of `latest` is re-resolved
        here and now, so installing picks up what upstream has at this moment,
        while a pinned ref or commit stays where it was put. A skill that has
        since been removed from the index, or whose repository cannot be reached,
        is reported rather than failing the whole playbook.

        Args:
            record_id: the playbook to resolve

        Returns:
            the playbook, its pinned skills, and whatever could not be resolved

        Raises:
            ValidationError: if the identifier is unusable
            NotFoundError: if no such playbook is stored
        """
        record = self.get(record_id)
        list_skills: list[ResolvedSkill] = []
        list_unresolved: list[dict[str, object]] = []
        for pin in record.list_pins:
            try:
                skill, version = self._skills.resolve_version(pin.skill_id, pin.pin)
            except (NotFoundError, ValidationError) as err:
                list_unresolved.append(
                    {
                        "skill_id": pin.skill_id,
                        "pin": pin.pin,
                        "reason": err.message,
                    }
                )
                continue
            list_skills.append(ResolvedSkill.of(skill, version, pin.pin))
        return ResolvedPlaybook(
            playbook=record,
            list_skills=list_skills,
            list_unresolved=list_unresolved,
            resolved_at=GitRefResolver.now_timestamp(),
        )
