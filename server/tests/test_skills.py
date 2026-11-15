import tempfile
import unittest
from pathlib import Path
from vboxd.errors import NotFoundError, ValidationError
from vboxd.skills import (
    GitRefResolver,
    SkillIndex,
    SkillRecord,
    SkillSource,
    SkillVersion,
)


class FakeResolver(GitRefResolver):
    """A resolver that answers from a table instead of contacting a repository."""

    def __init__(self) -> None:
        super().__init__()
        self.map_refs = {"main": "a" * 40, "v1": "b" * 40}
        self.default = "main"
        self.calls = 0

    def default_branch(self, repo_url: str) -> str:
        return self.default

    def resolve(self, repo_url: str, ref: str) -> SkillVersion:
        self.calls += 1
        if SkillSource.SOURCE_COMMIT_PATTERN.match(ref):
            return SkillVersion(ref=ref, commit=ref, resolved_at="t")
        if ref not in self.map_refs:
            raise ValidationError(f"{repo_url} has no ref {ref!r}")
        return SkillVersion(ref=ref, commit=self.map_refs[ref], resolved_at="t")


class SkillSourceTest(unittest.TestCase):
    def test_parses_the_forms_a_repo_of_skills_is_cited_in(self) -> None:
        for spec, repo, path, ref in (
            ("owner/repo", "https://github.com/owner/repo", "", ""),
            ("owner/repo@one", "https://github.com/owner/repo", "one", ""),
            ("owner/repo@dir/one", "https://github.com/owner/repo", "dir/one", ""),
            ("owner/repo#v2", "https://github.com/owner/repo", "", "v2"),
            ("owner/repo@one#v2", "https://github.com/owner/repo", "one", "v2"),
            ("https://gitlab.com/g/s/r", "https://gitlab.com/g/s/r", "", ""),
            ("git@github.com:o/r.git", "git@github.com:o/r.git", "", ""),
            ("git@github.com:o/r.git@one", "git@github.com:o/r.git", "one", ""),
        ):
            with self.subTest(spec=spec):
                source = SkillSource.parse(spec)
                self.assertEqual(source.repo_url, repo)
                self.assertEqual(source.skill_path, path)
                self.assertEqual(source.ref, ref)

    def test_refuses_transports_that_would_run_or_read_local_things(self) -> None:
        for spec in (
            "ext::sh -c whoami",
            "file:///etc/passwd",
            "--upload-pack=evil",
            "/local/path",
            "../../etc",
            "",
            "   ",
        ):
            with self.subTest(spec=spec):
                with self.assertRaises(ValidationError):
                    SkillSource.parse(spec)

    def test_refuses_an_unusable_ref_or_skill_path(self) -> None:
        for spec in ("owner/repo#a b", "owner/repo#a;b", "owner/repo@../x", "owner/repo@a b"):
            with self.subTest(spec=spec):
                with self.assertRaises(ValidationError):
                    SkillSource.parse(spec)

    def test_identifier_ignores_the_ref_so_versions_share_one_record(self) -> None:
        first = SkillSource.parse("owner/repo@one#v1").identifier()
        second = SkillSource.parse("owner/repo@one#v2").identifier()
        self.assertEqual(first, second)
        self.assertNotEqual(first, SkillSource.parse("owner/repo@two").identifier())

    def test_identifier_is_a_safe_file_name(self) -> None:
        identifier = SkillSource.parse("https://gitlab.com/g/s/r@a/b").identifier()
        self.assertNotIn("/", identifier)
        self.assertNotIn("..", identifier)


class SkillIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.resolver = FakeResolver()
        self.index = SkillIndex(self.root, self.resolver)

    def test_registering_resolves_a_version_immediately(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        self.assertEqual(record.id, "github-com-owner-repo-one")
        self.assertEqual(record.default_ref, "main")
        self.assertEqual(record.name, "one")
        self.assertEqual(len(record.list_versions), 1)
        self.assertEqual(record.latest_version().commit, "a" * 40)

    def test_registering_the_same_skill_again_updates_one_record(self) -> None:
        self.index.register({"source": "owner/repo@one", "description": "first"})
        record = self.index.register({"source": "owner/repo@one#v1", "description": "second"})
        self.assertEqual(record.description, "second")
        self.assertEqual(record.default_ref, "v1")
        self.assertEqual(len(self.index.query()), 1)

    def test_no_file_contents_are_stored(self) -> None:
        self.index.register({"source": "owner/repo@one"})
        list_files = sorted(path.name for path in (self.root / "skills").iterdir())
        self.assertEqual(list_files, [".lock", "github-com-owner-repo-one.json"])

    def test_registering_needs_a_source(self) -> None:
        with self.assertRaises(ValidationError):
            self.index.register({"description": "nothing to go on"})

    def test_structured_registration_without_a_spec(self) -> None:
        record = self.index.register(
            {
                "repo_url": "https://github.com/owner/repo",
                "skill_path": "deep/one",
                "ref": "v1",
            }
        )
        self.assertEqual(record.skill_path, "deep/one")
        self.assertEqual(record.default_ref, "v1")

    def test_a_version_history_accumulates_as_upstream_moves(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        self.resolver.map_refs["main"] = "c" * 40
        _, version = self.index.resolve_version(record.id, "latest")
        self.assertEqual(version.commit, "c" * 40)
        self.assertEqual(len(self.index.get(record.id).list_versions), 2)

    def test_latest_is_re_resolved_but_an_unchanged_head_adds_nothing(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        before = self.resolver.calls
        self.index.resolve_version(record.id, "latest")
        self.assertEqual(self.resolver.calls, before + 1)
        self.assertEqual(len(self.index.get(record.id).list_versions), 1)

    def test_a_historical_version_is_served_without_asking_upstream(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        old_commit = record.latest_version().commit
        self.resolver.map_refs["main"] = "c" * 40
        self.index.resolve_version(record.id, "latest")
        before = self.resolver.calls
        _, version = self.index.resolve_version(record.id, old_commit)
        self.assertEqual(version.commit, old_commit)
        self.assertEqual(self.resolver.calls, before)

    def test_an_abbreviated_commit_finds_its_version(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        _, version = self.index.resolve_version(record.id, "aaaaaaa")
        self.assertEqual(version.commit, "a" * 40)

    def test_an_unknown_pin_is_rejected(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        with self.assertRaises(ValidationError):
            self.index.resolve_version(record.id, "no-such-ref")

    def test_update_can_move_the_tracked_branch(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        updated = self.index.update(record.id, {"default_ref": "v1"})
        self.assertEqual(updated.default_ref, "v1")
        self.assertEqual(updated.latest_version().commit, "b" * 40)

    def test_update_rejects_an_unusable_ref(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        with self.assertRaises(ValidationError):
            self.index.update(record.id, {"default_ref": "a b"})

    def test_delete_removes_the_entry(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        self.index.delete(record.id)
        with self.assertRaises(NotFoundError):
            self.index.get(record.id)

    def test_unknown_and_unusable_ids_are_told_apart(self) -> None:
        with self.assertRaises(NotFoundError):
            self.index.get("never-registered")
        with self.assertRaises(ValidationError):
            self.index.get("../../etc/passwd")

    def test_query_filters_on_text_and_repository(self) -> None:
        self.index.register({"source": "owner/repo@one", "description": "formats code"})
        self.index.register({"source": "other/lib@two", "description": "writes tests"})
        self.assertEqual(len(self.index.query()), 2)
        self.assertEqual([r.name for r in self.index.query(text="formats")], ["one"])
        self.assertEqual([r.name for r in self.index.query(repo="other/lib")], ["two"])
        self.assertEqual(self.index.query(text="nothing-matches"), [])

    def test_version_history_is_bounded(self) -> None:
        record = self.index.register({"source": "owner/repo@one"})
        for index in range(120):
            self.resolver.map_refs["main"] = f"{index:040x}"
            self.index.resolve_version(record.id, "latest")
        self.assertEqual(
            len(self.index.get(record.id).list_versions),
            SkillRecord.SKILL_VERSION_HISTORY_MAX,
        )


if __name__ == "__main__":
    unittest.main()
