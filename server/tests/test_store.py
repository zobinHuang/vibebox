import json
import tempfile
import unittest
from pathlib import Path
from vboxd.errors import CursorConflictError, NotFoundError, ValidationError
from vboxd.store import AgentAccount, SessionMeta, SessionQuery, SessionStore


def make_meta(
    agent: str = "claude",
    session_id: str = "s-1",
    account_uuid: str = "acct-1",
    cwd: str = "/home/dev/proj",
) -> SessionMeta:
    return SessionMeta.from_request(
        session_id=session_id,
        map_body={
            "agent": agent,
            "cwd": cwd,
            "account": {"uuid": account_uuid, "email": "dev@example.com"},
        },
    )


class SessionMetaTest(unittest.TestCase):
    def test_rejects_traversal_in_session_id(self) -> None:
        for session_id in ("../etc", ".hidden", "a/b", "", "."):
            with self.subTest(session_id=session_id):
                with self.assertRaises(ValidationError):
                    SessionMeta.validate_session_id(session_id)

    def test_rejects_unknown_agent(self) -> None:
        with self.assertRaises(ValidationError):
            SessionMeta.from_request("s-1", {"agent": "cursor"})

    def test_account_falls_back_to_anonymous(self) -> None:
        meta = SessionMeta.from_request("s-1", {"agent": "codex"})
        self.assertEqual(
            meta.account.directory_name(),
            AgentAccount.ACCOUNT_ANONYMOUS_UUID,
        )

    def test_account_directory_name_cannot_escape(self) -> None:
        account = AgentAccount(uuid="../../root")
        self.assertNotIn("/", account.directory_name())
        self.assertNotIn("..", account.directory_name())

    def test_project_directory_keeps_basename_and_separates_same_names(self) -> None:
        one = make_meta(cwd="/a/vibebox").project_directory_name()
        two = make_meta(cwd="/b/vibebox").project_directory_name()
        self.assertTrue(one.startswith("vibebox-"))
        self.assertTrue(two.startswith("vibebox-"))
        self.assertNotEqual(one, two)

    def test_layout_splits_by_agent_account_and_project(self) -> None:
        meta = make_meta(agent="codex", account_uuid="acct-9", session_id="s-7")
        list_parts = list(meta.relative_directory().parts)
        self.assertEqual(list_parts[0], "codex")
        self.assertEqual(list_parts[1], "acct-9")
        self.assertTrue(list_parts[2].startswith("proj-"))
        self.assertEqual(list_parts[3], "s-7")


class SessionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = SessionStore(self.root)

    def test_begin_reports_an_empty_cursor(self) -> None:
        cursor = self.store.begin(make_meta())
        self.assertEqual(cursor.cursor, 0)
        self.assertEqual(cursor.prefix_sha256, "")

    def test_begin_is_idempotent_and_keeps_the_original_open_time(self) -> None:
        self.store.begin(make_meta())
        self.store.append("s-1", 0, b'{"a":1}\n', do_reset=False)
        path_meta = (
            self.root / make_meta().relative_directory() / SessionStore.STORE_META_NAME
        )
        opened_at = json.loads(path_meta.read_text())["opened_at"]
        cursor = self.store.begin(make_meta())
        self.assertEqual(cursor.cursor, 8)
        self.assertEqual(json.loads(path_meta.read_text())["opened_at"], opened_at)

    def test_append_accumulates_exactly_the_uploaded_bytes(self) -> None:
        self.store.begin(make_meta())
        self.store.append("s-1", 0, b'{"a":1}\n', do_reset=False)
        self.store.append("s-1", 8, b'{"b":2}\n', do_reset=False)
        path_transcript = (
            self.root
            / make_meta().relative_directory()
            / SessionStore.STORE_TRANSCRIPT_NAME
        )
        self.assertEqual(path_transcript.read_bytes(), b'{"a":1}\n{"b":2}\n')
        self.assertEqual(self.store.cursor_of("s-1").cursor, 16)

    def test_append_rejects_a_stale_offset_and_reports_the_real_one(self) -> None:
        self.store.begin(make_meta())
        self.store.append("s-1", 0, b"0123456789", do_reset=False)
        with self.assertRaises(CursorConflictError) as caught:
            self.store.append("s-1", 4, b"xx", do_reset=False)
        self.assertEqual(caught.exception.cursor, 10)
        self.assertEqual(caught.exception.as_payload()["cursor"], 10)

    def test_append_rejects_a_negative_offset(self) -> None:
        self.store.begin(make_meta())
        with self.assertRaises(ValidationError):
            self.store.append("s-1", -1, b"x", do_reset=False)

    def test_reset_rotates_the_old_transcript_aside(self) -> None:
        self.store.begin(make_meta())
        self.store.append("s-1", 0, b"old-content\n", do_reset=False)
        self.store.append("s-1", 0, b"new\n", do_reset=True)
        directory = self.root / make_meta().relative_directory()
        self.assertEqual(
            (directory / SessionStore.STORE_TRANSCRIPT_NAME).read_bytes(),
            b"new\n",
        )
        list_rotated = sorted(directory.glob("transcript.jsonl.*"))
        self.assertEqual(len(list_rotated), 1)
        self.assertEqual(list_rotated[0].read_bytes(), b"old-content\n")

    def test_unknown_session_is_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            self.store.cursor_of("never-seen")

    def test_close_records_a_closing_time(self) -> None:
        self.store.begin(make_meta())
        meta = self.store.close("s-1")
        self.assertIsNotNone(meta.closed_at)

    def test_two_accounts_of_one_agent_stay_apart(self) -> None:
        self.store.begin(make_meta(session_id="s-a", account_uuid="acct-a"))
        self.store.begin(make_meta(session_id="s-b", account_uuid="acct-b"))
        set_account_dirs = {path.name for path in (self.root / "claude").iterdir()}
        self.assertEqual(set_account_dirs, {"acct-a", "acct-b"})

    def restamp(self, session_id: str, updated_at: str) -> None:
        """Force one stored session's update time, so ordering tests never race
        the clock: several writes can land inside the same millisecond."""
        path_meta = (
            self.root
            / make_meta(session_id=session_id).relative_directory()
            / SessionStore.STORE_META_NAME
        )
        map_payload = json.loads(path_meta.read_text())
        map_payload["updated_at"] = updated_at
        path_meta.write_text(json.dumps(map_payload))

    def test_query_is_newest_first(self) -> None:
        self.store.begin(make_meta(session_id="s-a"))
        self.store.begin(make_meta(session_id="s-b"))
        self.restamp("s-a", "2026-11-16T10:00:00.000+00:00")
        self.restamp("s-b", "2026-11-16T09:00:00.000+00:00")
        list_ids = [m.session_id for m in self.store.query(SessionQuery()).list_sessions]
        self.assertEqual(list_ids, ["s-a", "s-b"])

    def test_equal_timestamps_break_on_session_id(self) -> None:
        for session_id in ("s-a", "s-b", "s-c"):
            self.store.begin(make_meta(session_id=session_id))
            self.restamp(session_id, "2026-11-16T10:00:00.000+00:00")
        list_ids = [m.session_id for m in self.store.query(SessionQuery()).list_sessions]
        self.assertEqual(list_ids, ["s-c", "s-b", "s-a"])

    def test_paging_through_equal_timestamps_loses_nothing(self) -> None:
        for index in range(6):
            self.store.begin(make_meta(session_id=f"s-{index}"))
            self.restamp(f"s-{index}", "2026-11-16T10:00:00.000+00:00")
        list_seen: list[str] = []
        cursor = None
        while True:
            page = self.store.query(SessionQuery(limit=2, cursor=cursor))
            list_seen.extend(meta.session_id for meta in page.list_sessions)
            cursor = page.next_cursor
            if not cursor:
                break
        self.assertEqual(sorted(list_seen), [f"s-{index}" for index in range(6)])
        self.assertEqual(len(set(list_seen)), 6)

    def test_stored_files_are_owner_only(self) -> None:
        self.store.begin(make_meta())
        self.store.append("s-1", 0, b"x\n", do_reset=False)
        directory = self.root / make_meta().relative_directory()
        self.assertEqual(
            (directory / SessionStore.STORE_TRANSCRIPT_NAME).stat().st_mode & 0o777,
            0o600,
        )
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
