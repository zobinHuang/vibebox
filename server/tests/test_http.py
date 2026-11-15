import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from vboxd.config import ServerConfig
from vboxd.http import VboxdServer
from vboxd.store import SessionStore


class HttpApiTest(unittest.TestCase):
    TEST_TOKEN = "test-token"
    TEST_BEGIN_BODY = {
        "agent": "claude",
        "cwd": "/home/dev/proj",
        "machine": {"hostname": "laptop", "os": "Linux"},
        "account": {"uuid": "acct-1", "email": "dev@example.com"},
    }

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        config = ServerConfig(
            data_root=self.root,
            host="127.0.0.1",
            port=0,
            token=self.TEST_TOKEN,
            max_body_bytes=1024,
            tls_cert=None,
            tls_key=None,
        )
        self.server = VboxdServer(
            config=config,
            store=SessionStore(self.root),
        )
        self.port = self.server.bound_port()
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        token: str | None = TEST_TOKEN,
    ) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            map_headers: dict[str, str] = {}
            if token is not None:
                map_headers["Authorization"] = f"Bearer {token}"
            if body is not None:
                map_headers["Content-Length"] = str(len(body))
            connection.request(method, path, body=body, headers=map_headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
        finally:
            connection.close()

    def begin(self, session_id: str = "s-1") -> dict[str, object]:
        status, map_body = self.request(
            "POST",
            f"/v1/sessions/{session_id}/begin",
            json.dumps(self.TEST_BEGIN_BODY).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        return map_body

    def test_healthz_needs_no_token(self) -> None:
        status, map_body = self.request("GET", "/v1/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(map_body["ok"])

    def test_missing_token_is_rejected(self) -> None:
        status, _ = self.request("GET", "/v1/sessions", token=None)
        self.assertEqual(status, 401)

    def test_wrong_token_is_rejected(self) -> None:
        status, _ = self.request("GET", "/v1/sessions", token="not-the-token")
        self.assertEqual(status, 401)

    def test_unknown_route_is_not_found(self) -> None:
        status, _ = self.request("GET", "/v2/sessions")
        self.assertEqual(status, 404)

    def test_begin_then_append_stores_the_bytes_verbatim(self) -> None:
        self.assertEqual(self.begin()["cursor"], 0)
        chunk = b'{"type":"user"}\n{"type":"assistant"}\n'
        status, map_body = self.request(
            "PUT",
            "/v1/sessions/s-1/data?offset=0",
            chunk,
        )
        self.assertEqual(status, 200)
        self.assertEqual(map_body["cursor"], len(chunk))
        list_stored = list(self.root.rglob("transcript.jsonl"))
        self.assertEqual(len(list_stored), 1)
        self.assertEqual(list_stored[0].read_bytes(), chunk)

    def test_stale_offset_conflicts_and_reports_the_server_cursor(self) -> None:
        self.begin()
        self.request("PUT", "/v1/sessions/s-1/data?offset=0", b"0123456789")
        status, map_body = self.request(
            "PUT",
            "/v1/sessions/s-1/data?offset=3",
            b"xx",
        )
        self.assertEqual(status, 409)
        self.assertEqual(map_body["cursor"], 10)

    def test_reset_replaces_a_rewritten_transcript(self) -> None:
        self.begin()
        self.request("PUT", "/v1/sessions/s-1/data?offset=0", b"old\n")
        status, _ = self.request(
            "PUT",
            "/v1/sessions/s-1/data?offset=0&reset=1",
            b"fresh\n",
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            next(self.root.rglob("transcript.jsonl")).read_bytes(),
            b"fresh\n",
        )

    def test_append_to_an_unregistered_session_is_not_found(self) -> None:
        status, _ = self.request("PUT", "/v1/sessions/ghost/data?offset=0", b"x")
        self.assertEqual(status, 404)

    def test_traversal_session_id_is_rejected(self) -> None:
        status, _ = self.request(
            "POST",
            "/v1/sessions/..%2F..%2Fetc/begin",
            json.dumps(self.TEST_BEGIN_BODY).encode("utf-8"),
        )
        self.assertIn(status, (400, 404))
        self.assertEqual(list(self.root.rglob("transcript.jsonl")), [])

    def test_unknown_agent_is_rejected(self) -> None:
        status, _ = self.request(
            "POST",
            "/v1/sessions/s-1/begin",
            json.dumps({"agent": "cursor"}).encode("utf-8"),
        )
        self.assertEqual(status, 400)

    def test_oversize_body_is_refused(self) -> None:
        self.begin()
        status, _ = self.request(
            "PUT",
            "/v1/sessions/s-1/data?offset=0",
            b"x" * 2048,
        )
        self.assertEqual(status, 413)

    def test_chunked_body_is_rejected(self) -> None:
        self.begin()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest("PUT", "/v1/sessions/s-1/data?offset=0")
            connection.putheader("Authorization", f"Bearer {self.TEST_TOKEN}")
            connection.putheader("Transfer-Encoding", "chunked")
            connection.endheaders()
            connection.send(b"5\r\nhello\r\n0\r\n\r\n")
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            connection.close()
        self.assertEqual(list(self.root.rglob("transcript.jsonl")), [])

    def test_missing_offset_is_rejected(self) -> None:
        self.begin()
        status, _ = self.request("PUT", "/v1/sessions/s-1/data", b"x")
        self.assertEqual(status, 400)

    def test_close_marks_the_session_closed(self) -> None:
        self.begin()
        status, map_body = self.request("POST", "/v1/sessions/s-1/close", b"{}")
        self.assertEqual(status, 200)
        self.assertIsNotNone(map_body["closed_at"])

    def test_machine_is_recorded_with_the_session(self) -> None:
        status, _ = self.request(
            "POST",
            "/v1/sessions/s-1/begin",
            json.dumps(
                {
                    "agent": "claude",
                    "cwd": "/srv/x",
                    "account": {"uuid": "acct-1"},
                    "machine": {
                        "hostname": "buildbox",
                        "fqdn": "buildbox.lan",
                        "os": "Linux",
                        "os_release": "6.8.0",
                        "arch": "x86_64",
                        "user": "dev",
                    },
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        map_meta = json.loads(next(self.root.rglob("meta.json")).read_text())
        self.assertEqual(map_meta["machine"]["hostname"], "buildbox")
        self.assertEqual(map_meta["machine"]["arch"], "x86_64")
        self.assertEqual(map_meta["machine"]["user"], "dev")

    def test_a_session_without_machine_info_is_still_stored(self) -> None:
        status, _ = self.request(
            "POST",
            "/v1/sessions/s-1/begin",
            json.dumps({"agent": "claude", "cwd": "/srv/x"}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        map_meta = json.loads(next(self.root.rglob("meta.json")).read_text())
        self.assertIsNone(map_meta["machine"]["hostname"])

    def test_listing_reports_the_stored_sessions(self) -> None:
        self.begin("s-1")
        self.begin("s-2")
        status, map_body = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 200)
        list_sessions = map_body["sessions"]
        self.assertEqual(len(list_sessions), 2)
        self.assertEqual(list_sessions[0]["account"]["uuid"], "acct-1")
        self.assertEqual(map_body["total_matched"], 2)
        self.assertIsNone(map_body["next_cursor"])

    def test_listing_rejects_an_out_of_range_limit(self) -> None:
        status, _ = self.request("GET", "/v1/sessions?limit=0")
        self.assertEqual(status, 400)

    def seed(self, session_id: str, **overrides: object) -> None:
        map_body = dict(self.TEST_BEGIN_BODY)
        map_body.update(overrides)
        status, _ = self.request(
            "POST",
            f"/v1/sessions/{session_id}/begin",
            json.dumps(map_body).encode("utf-8"),
        )
        self.assertEqual(status, 200)

    def ids_for(self, query: str) -> list[str]:
        status, map_body = self.request("GET", f"/v1/sessions?{query}")
        self.assertEqual(status, 200)
        return [meta["session_id"] for meta in map_body["sessions"]]

    def seed_fleet(self) -> None:
        self.seed(
            "mac-claude",
            agent="claude",
            machine={"hostname": "laptop", "fqdn": "laptop.lan", "os": "Darwin"},
            cwd="/home/dev/vibebox",
            git_branch="main",
        )
        self.seed(
            "box-claude",
            agent="claude",
            account={"uuid": "acct-2", "email": "other@example.com"},
            machine={"hostname": "buildbox", "fqdn": "buildbox.lan", "os": "Linux"},
            cwd="/srv/other",
            git_branch="wip",
        )
        self.seed(
            "box-codex",
            agent="codex",
            machine={"hostname": "buildbox", "fqdn": "buildbox.lan", "os": "Linux"},
            cwd="/srv/other",
        )

    def test_filter_by_agent(self) -> None:
        self.seed_fleet()
        self.assertEqual(self.ids_for("agent=codex"), ["box-codex"])
        self.assertEqual(sorted(self.ids_for("agent=claude")), ["box-claude", "mac-claude"])

    def test_filter_by_hostname_accepts_short_name_or_fqdn(self) -> None:
        self.seed_fleet()
        self.assertEqual(sorted(self.ids_for("hostname=buildbox")), ["box-claude", "box-codex"])
        self.assertEqual(sorted(self.ids_for("hostname=buildbox.lan")), ["box-claude", "box-codex"])
        self.assertEqual(self.ids_for("hostname=laptop"), ["mac-claude"])

    def test_filter_by_account_and_branch(self) -> None:
        self.seed_fleet()
        self.assertEqual(self.ids_for("account=acct-2"), ["box-claude"])
        self.assertEqual(self.ids_for("branch=wip"), ["box-claude"])

    def test_filter_by_project_is_a_substring_of_cwd(self) -> None:
        self.seed_fleet()
        self.assertEqual(self.ids_for("project=vibebox"), ["mac-claude"])
        self.assertEqual(sorted(self.ids_for("project=/srv")), ["box-claude", "box-codex"])

    def test_filters_combine(self) -> None:
        self.seed_fleet()
        self.assertEqual(self.ids_for("hostname=buildbox&agent=claude"), ["box-claude"])

    def test_filter_by_status(self) -> None:
        self.seed_fleet()
        self.request("POST", "/v1/sessions/box-codex/close", b"{}")
        self.assertEqual(self.ids_for("status=closed"), ["box-codex"])
        self.assertEqual(sorted(self.ids_for("status=open")), ["box-claude", "mac-claude"])

    def test_filter_by_time_window(self) -> None:
        self.seed_fleet()
        self.assertEqual(len(self.ids_for("since=1970-01-01T00:00:00Z")), 3)
        self.assertEqual(len(self.ids_for("since=0")), 3)
        self.assertEqual(len(self.ids_for("until=1970-01-01T00:00:00Z")), 0)
        self.assertEqual(len(self.ids_for("since=2099-01-01T00:00:00Z")), 0)

    def test_unusable_filters_are_rejected(self) -> None:
        for query in ("agent=cursor", "status=sleeping", "since=yesterday",
                      "time=nonsense", "cursor=!!!!", "limit=99999"):
            with self.subTest(query=query):
                status, _ = self.request("GET", f"/v1/sessions?{query}")
                self.assertEqual(status, 400)

    def test_cursor_walks_every_session_exactly_once(self) -> None:
        for index in range(7):
            self.seed(f"s-{index}")
        list_seen: list[str] = []
        query = "limit=3"
        while True:
            status, map_body = self.request("GET", f"/v1/sessions?{query}")
            self.assertEqual(status, 200)
            list_seen.extend(meta["session_id"] for meta in map_body["sessions"])
            if not map_body["next_cursor"]:
                break
            query = f"limit=3&cursor={map_body['next_cursor']}"
        self.assertEqual(sorted(list_seen), [f"s-{index}" for index in range(7)])
        self.assertEqual(len(set(list_seen)), 7)

    def test_single_session_metadata(self) -> None:
        self.seed_fleet()
        status, map_body = self.request("GET", "/v1/sessions/mac-claude")
        self.assertEqual(status, 200)
        self.assertEqual(map_body["machine"]["hostname"], "laptop")
        self.assertEqual(map_body["machine"]["os"], "Darwin")
        status, _ = self.request("GET", "/v1/sessions/nope")
        self.assertEqual(status, 404)

    def test_transcript_download_whole_and_from_an_offset(self) -> None:
        self.begin()
        chunk = b'{"a":1}\n{"b":2}\n'
        self.request("PUT", "/v1/sessions/s-1/data?offset=0", chunk)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                "GET",
                "/v1/sessions/s-1/transcript",
                headers={"Authorization": f"Bearer {self.TEST_TOKEN}"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "application/x-ndjson")
            self.assertEqual(response.getheader("X-Vbox-Total"), str(len(chunk)))
            self.assertEqual(response.read(), chunk)
        finally:
            connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                "GET",
                "/v1/sessions/s-1/transcript?offset=8",
                headers={"Authorization": f"Bearer {self.TEST_TOKEN}"},
            )
            response = connection.getresponse()
            self.assertEqual(response.read(), b'{"b":2}\n')
        finally:
            connection.close()

    def test_machines_and_accounts_summarise_the_fleet(self) -> None:
        self.seed_fleet()
        status, map_body = self.request("GET", "/v1/machines")
        self.assertEqual(status, 200)
        map_by_host = {m["hostname"]: m for m in map_body["machines"]}
        self.assertEqual(map_by_host["buildbox"]["session_count"], 2)
        self.assertEqual(map_by_host["buildbox"]["os"], "Linux")
        self.assertEqual(map_by_host["laptop"]["session_count"], 1)
        status, map_body = self.request("GET", "/v1/accounts")
        self.assertEqual(status, 200)
        set_pairs = {(a["agent"], a["uuid"]) for a in map_body["accounts"]}
        self.assertEqual(
            set_pairs,
            {("claude", "acct-1"), ("claude", "acct-2"), ("codex", "acct-1")},
        )

    def test_reads_need_a_token(self) -> None:
        self.begin()
        for path in ("/v1/sessions/s-1", "/v1/sessions/s-1/transcript",
                     "/v1/machines", "/v1/accounts"):
            with self.subTest(path=path):
                status, _ = self.request("GET", path, token=None)
                self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
