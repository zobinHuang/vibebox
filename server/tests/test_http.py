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
        "host": "laptop",
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

    def test_missing_offset_is_rejected(self) -> None:
        self.begin()
        status, _ = self.request("PUT", "/v1/sessions/s-1/data", b"x")
        self.assertEqual(status, 400)

    def test_close_marks_the_session_closed(self) -> None:
        self.begin()
        status, map_body = self.request("POST", "/v1/sessions/s-1/close", b"{}")
        self.assertEqual(status, 200)
        self.assertIsNotNone(map_body["closed_at"])

    def test_listing_reports_the_stored_sessions(self) -> None:
        self.begin("s-1")
        self.begin("s-2")
        status, map_body = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 200)
        list_sessions = map_body["sessions"]
        self.assertEqual(len(list_sessions), 2)
        self.assertEqual(list_sessions[0]["account"]["uuid"], "acct-1")

    def test_listing_rejects_an_out_of_range_limit(self) -> None:
        status, _ = self.request("GET", "/v1/sessions?limit=0")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
