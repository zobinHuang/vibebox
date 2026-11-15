import tempfile
import unittest
from pathlib import Path
from tests.test_skills import FakeResolver
from vboxd.errors import ConflictError, NotFoundError, ValidationError
from vboxd.playbooks import PlaybookIndex, PlaybookRecord, SkillPin
from vboxd.skills import SkillIndex


class PlaybookIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.resolver = FakeResolver()
        self.skills = SkillIndex(self.root, self.resolver)
        self.playbooks = PlaybookIndex(self.root, self.skills)
        self.one = self.skills.register({"source": "owner/repo@one", "description": "formats"})
        self.two = self.skills.register({"source": "other/lib@two", "description": "tests"})

    def make(self, **overrides: object) -> PlaybookRecord:
        map_body: dict[str, object] = {
            "name": "Backend review",
            "description": "what to load when reviewing the backend",
            "prompt": "Prefer the loaded skills over improvising.",
            "skills": [self.one.id, {"skill_id": self.two.id, "pin": "v1"}],
        }
        map_body.update(overrides)
        return self.playbooks.create(map_body)

    def test_create_slugs_the_name_into_an_id(self) -> None:
        record = self.make()
        self.assertEqual(record.id, "backend-review")
        self.assertEqual(record.name, "Backend review")
        self.assertEqual(len(record.list_pins), 2)

    def test_a_bare_string_skill_tracks_the_latest(self) -> None:
        record = self.make(skills=[self.one.id])
        self.assertEqual(record.list_pins[0].pin, SkillPin.PIN_LATEST)

    def test_create_needs_a_name(self) -> None:
        for name in ("", "   "):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    self.playbooks.create({"name": name})

    def test_create_refuses_to_overwrite(self) -> None:
        self.make()
        with self.assertRaises(ConflictError):
            self.make()

    def test_create_rejects_an_unusable_id_or_pin(self) -> None:
        with self.assertRaises(ValidationError):
            self.playbooks.create({"name": "x", "id": "../../etc/passwd"})
        with self.assertRaises(ValidationError):
            self.playbooks.create(
                {"name": "x", "skills": [{"skill_id": self.one.id, "pin": "a b"}]}
            )

    def test_create_rejects_a_skills_value_that_is_not_a_list(self) -> None:
        with self.assertRaises(ValidationError):
            self.playbooks.create({"name": "x", "skills": {"skill_id": self.one.id}})

    def test_duplicate_skills_collapse_to_one(self) -> None:
        record = self.make(skills=[self.one.id, self.one.id])
        self.assertEqual(len(record.list_pins), 1)

    def test_an_over_long_prompt_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            self.make(prompt="x" * (PlaybookRecord.PLAYBOOK_PROMPT_MAX_CHARS + 1))

    def test_too_many_skills_are_refused(self) -> None:
        with self.assertRaises(ValidationError):
            self.make(skills=[f"s-{i}" for i in range(PlaybookRecord.PLAYBOOK_SKILLS_MAX + 1)])

    def test_update_leaves_fields_it_was_not_given(self) -> None:
        record = self.make()
        updated = self.playbooks.update(record.id, {"prompt": "new instructions"})
        self.assertEqual(updated.prompt, "new instructions")
        self.assertEqual(updated.name, record.name)
        self.assertEqual(len(updated.list_pins), 2)

    def test_update_can_replace_the_skill_list(self) -> None:
        record = self.make()
        updated = self.playbooks.update(record.id, {"skills": [self.two.id]})
        self.assertEqual([pin.skill_id for pin in updated.list_pins], [self.two.id])

    def test_delete_then_get_is_not_found(self) -> None:
        record = self.make()
        self.playbooks.delete(record.id)
        with self.assertRaises(NotFoundError):
            self.playbooks.get(record.id)

    def test_query_filters_on_text(self) -> None:
        self.make()
        self.make(name="Frontend work", skills=[])
        self.assertEqual(len(self.playbooks.query()), 2)
        self.assertEqual([r.id for r in self.playbooks.query(text="frontend")], ["frontend-work"])

    def test_resolve_pins_every_skill_to_a_commit(self) -> None:
        record = self.make()
        resolved = self.playbooks.resolve(record.id)
        map_by_id = {skill.skill_id: skill for skill in resolved.list_skills}
        self.assertEqual(map_by_id[self.one.id].commit, "a" * 40)
        self.assertEqual(map_by_id[self.two.id].commit, "b" * 40)
        self.assertEqual(map_by_id[self.two.id].pin, "v1")
        self.assertEqual(resolved.list_unresolved, [])
        self.assertEqual(resolved.playbook.prompt, record.prompt)

    def test_resolve_carries_what_a_client_needs_to_fetch(self) -> None:
        resolved = self.playbooks.resolve(self.make().id)
        map_payload = resolved.as_payload()["skills"][0]
        for key in ("repo_url", "skill_path", "commit", "name", "description"):
            self.assertIn(key, map_payload)

    def test_a_ref_pin_follows_the_ref_because_a_ref_moves(self) -> None:
        record = self.make()
        self.resolver.map_refs["main"] = "c" * 40
        self.resolver.map_refs["v1"] = "d" * 40
        resolved = self.playbooks.resolve(record.id)
        map_by_id = {skill.skill_id: skill for skill in resolved.list_skills}
        self.assertEqual(map_by_id[self.one.id].commit, "c" * 40)
        self.assertEqual(map_by_id[self.two.id].commit, "d" * 40)

    def test_a_commit_pin_never_moves_and_never_asks_upstream(self) -> None:
        record = self.playbooks.create(
            {"name": "pinned", "skills": [{"skill_id": self.one.id, "pin": "a" * 40}]}
        )
        self.resolver.map_refs["main"] = "c" * 40
        before = self.resolver.calls
        resolved = self.playbooks.resolve(record.id)
        self.assertEqual(resolved.list_skills[0].commit, "a" * 40)
        self.assertEqual(self.resolver.calls, before)

    def test_resolve_can_serve_a_historical_commit(self) -> None:
        record = self.playbooks.create(
            {"name": "pinned", "skills": [{"skill_id": self.one.id, "pin": "a" * 40}]}
        )
        self.resolver.map_refs["main"] = "c" * 40
        resolved = self.playbooks.resolve(record.id)
        self.assertEqual(resolved.list_skills[0].commit, "a" * 40)

    def test_a_removed_skill_is_reported_not_fatal(self) -> None:
        record = self.make()
        self.skills.delete(self.one.id)
        resolved = self.playbooks.resolve(record.id)
        self.assertEqual([s.skill_id for s in resolved.list_skills], [self.two.id])
        self.assertEqual(len(resolved.list_unresolved), 1)
        self.assertEqual(resolved.list_unresolved[0]["skill_id"], self.one.id)

    def test_an_unreachable_repository_is_reported_not_fatal(self) -> None:
        record = self.playbooks.create(
            {"name": "broken", "skills": [{"skill_id": self.one.id, "pin": "gone-ref"}]}
        )
        resolved = self.playbooks.resolve(record.id)
        self.assertEqual(resolved.list_skills, [])
        self.assertIn("gone-ref", resolved.list_unresolved[0]["reason"])

    def test_no_skill_files_are_stored(self) -> None:
        self.make()
        list_files = sorted(path.name for path in (self.root / "playbooks").iterdir())
        self.assertEqual(list_files, [".lock", "backend-review.json"])


if __name__ == "__main__":
    unittest.main()
