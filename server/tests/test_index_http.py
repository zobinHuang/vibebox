import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from tests.test_skills import FakeResolver
from vboxd.config import ServerConfig
from vboxd.http import VboxdServer
from vboxd.playbooks import PlaybookIndex
from vboxd.skills import SkillIndex
from vboxd.store import SessionStore


class IndexApiTest(unittest.TestCase):
    TEST_TOKEN = "test-token"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.resolver = FakeResolver()
        skills = SkillIndex(self.root, self.resolver)
        config = ServerConfig(
            data_root=self.root,
            host="127.0.0.1",
            port=0,
            token=self.TEST_TOKEN,
            max_body_bytes=1024 * 64,
            tls_cert=None,
            tls_key=None,
        )
        self.server = VboxdServer(
            config=config,
            store=SessionStore(self.root),
            skills=skills,
            playbooks=PlaybookIndex(self.root, skills),
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
        map_body: object = None,
        token: str | None = TEST_TOKEN,
    ) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            body = None if map_body is None else json.dumps(map_body).encode("utf-8")
            map_headers: dict[str, str] = {}
            if token is not None:
                map_headers["Authorization"] = f"Bearer {token}"
            if body is not None:
                map_headers["Content-Length"] = str(len(body))
                map_headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=map_headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
        finally:
            connection.close()

    def add_skill(self, spec: str, **extra: object) -> dict[str, object]:
        map_body: dict[str, object] = {"source": spec}
        map_body.update(extra)
        status, map_record = self.request("POST", "/v1/skills", map_body)
        self.assertEqual(status, 200, map_record)
        return map_record

    def add_playbook(self, **overrides: object) -> dict[str, object]:
        map_body: dict[str, object] = {"name": "Backend review", "prompt": "use them"}
        map_body.update(overrides)
        status, map_record = self.request("POST", "/v1/playbooks", map_body)
        self.assertEqual(status, 201, map_record)
        return map_record

    # ── skills ────────────────────────────────────────────────────────
    def test_skill_crud_round_trip(self) -> None:
        created = self.add_skill("owner/repo@one", description="formats code")
        self.assertEqual(created["id"], "github-com-owner-repo-one")
        self.assertEqual(created["versions"][0]["commit"], "a" * 40)

        status, map_read = self.request("GET", f"/v1/skills/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(map_read["description"], "formats code")

        status, map_patched = self.request(
            "PATCH",
            f"/v1/skills/{created['id']}",
            {"description": "formats code, properly"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(map_patched["description"], "formats code, properly")

        status, _ = self.request("DELETE", f"/v1/skills/{created['id']}")
        self.assertEqual(status, 200)
        status, _ = self.request("GET", f"/v1/skills/{created['id']}")
        self.assertEqual(status, 404)

    def test_skill_listing_filters(self) -> None:
        self.add_skill("owner/repo@one", description="formats code")
        self.add_skill("other/lib@two", description="writes tests")
        status, map_body = self.request("GET", "/v1/skills")
        self.assertEqual(status, 200)
        self.assertEqual(len(map_body["skills"]), 2)
        _, map_body = self.request("GET", "/v1/skills?q=formats")
        self.assertEqual([s["name"] for s in map_body["skills"]], ["one"])
        _, map_body = self.request("GET", "/v1/skills?repo=other/lib")
        self.assertEqual([s["name"] for s in map_body["skills"]], ["two"])

    def test_skill_versions_and_refresh(self) -> None:
        created = self.add_skill("owner/repo@one")
        self.resolver.map_refs["main"] = "c" * 40
        status, map_body = self.request("POST", f"/v1/skills/{created['id']}/refresh")
        self.assertEqual(status, 200)
        self.assertEqual(map_body["version"]["commit"], "c" * 40)
        status, map_body = self.request("GET", f"/v1/skills/{created['id']}/versions")
        self.assertEqual(status, 200)
        self.assertEqual([v["commit"] for v in map_body["versions"]], ["a" * 40, "c" * 40])

    def test_skill_registration_rejects_a_dangerous_source(self) -> None:
        for spec in ("ext::sh -c id", "file:///etc/passwd", "--upload-pack=x", "owner/repo@../x"):
            with self.subTest(spec=spec):
                status, _ = self.request("POST", "/v1/skills", {"source": spec})
                self.assertEqual(status, 400)

    def test_skill_registration_needs_a_source(self) -> None:
        status, _ = self.request("POST", "/v1/skills", {"description": "nothing"})
        self.assertEqual(status, 400)

    def test_unusable_skill_id_is_rejected(self) -> None:
        status, _ = self.request("GET", "/v1/skills/..%2F..%2Fetc%2Fpasswd")
        self.assertIn(status, (400, 404))

    # ── playbooks ─────────────────────────────────────────────────────
    def test_playbook_crud_round_trip(self) -> None:
        one = self.add_skill("owner/repo@one")
        created = self.add_playbook(skills=[one["id"]])
        self.assertEqual(created["id"], "backend-review")
        self.assertEqual(created["skills"], [{"skill_id": one["id"], "pin": "latest"}])

        status, map_read = self.request("GET", "/v1/playbooks/backend-review")
        self.assertEqual(status, 200)
        self.assertEqual(map_read["prompt"], "use them")

        status, map_patched = self.request(
            "PATCH",
            "/v1/playbooks/backend-review",
            {"prompt": "prefer them over improvising"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(map_patched["prompt"], "prefer them over improvising")
        self.assertEqual(len(map_patched["skills"]), 1)

        status, _ = self.request("DELETE", "/v1/playbooks/backend-review")
        self.assertEqual(status, 200)
        status, _ = self.request("GET", "/v1/playbooks/backend-review")
        self.assertEqual(status, 404)

    def test_creating_the_same_playbook_twice_conflicts(self) -> None:
        self.add_playbook()
        status, map_body = self.request(
            "POST",
            "/v1/playbooks",
            {"name": "Backend review"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(map_body["error"], "ConflictError")

    def test_playbook_needs_a_name(self) -> None:
        status, _ = self.request("POST", "/v1/playbooks", {"prompt": "x"})
        self.assertEqual(status, 400)

    def test_playbook_listing_filters(self) -> None:
        self.add_playbook()
        self.add_playbook(name="Frontend work")
        status, map_body = self.request("GET", "/v1/playbooks")
        self.assertEqual(status, 200)
        self.assertEqual(len(map_body["playbooks"]), 2)
        _, map_body = self.request("GET", "/v1/playbooks?q=frontend")
        self.assertEqual([p["id"] for p in map_body["playbooks"]], ["frontend-work"])

    def test_resolve_gives_a_client_everything_it_needs(self) -> None:
        one = self.add_skill("owner/repo@one", description="formats code")
        two = self.add_skill("other/lib@two")
        self.add_playbook(
            skills=[one["id"], {"skill_id": two["id"], "pin": "v1"}],
        )
        status, map_body = self.request("GET", "/v1/playbooks/backend-review/resolve")
        self.assertEqual(status, 200)
        self.assertEqual(map_body["prompt"], "use them")
        self.assertEqual(map_body["unresolved"], [])
        map_by_id = {s["skill_id"]: s for s in map_body["skills"]}
        self.assertEqual(map_by_id[one["id"]]["commit"], "a" * 40)
        self.assertEqual(map_by_id[one["id"]]["repo_url"], "https://github.com/owner/repo")
        self.assertEqual(map_by_id[one["id"]]["skill_path"], "one")
        self.assertEqual(map_by_id[one["id"]]["description"], "formats code")
        self.assertEqual(map_by_id[two["id"]]["commit"], "b" * 40)

    def test_resolve_picks_up_upstream_movement(self) -> None:
        one = self.add_skill("owner/repo@one")
        self.add_playbook(skills=[one["id"]])
        self.resolver.map_refs["main"] = "c" * 40
        _, map_body = self.request("GET", "/v1/playbooks/backend-review/resolve")
        self.assertEqual(map_body["skills"][0]["commit"], "c" * 40)

    def test_resolve_reports_a_skill_it_could_not_pin(self) -> None:
        one = self.add_skill("owner/repo@one")
        self.add_playbook(skills=[one["id"]])
        self.request("DELETE", f"/v1/skills/{one['id']}")
        status, map_body = self.request("GET", "/v1/playbooks/backend-review/resolve")
        self.assertEqual(status, 200)
        self.assertEqual(map_body["skills"], [])
        self.assertEqual(map_body["unresolved"][0]["skill_id"], one["id"])

    # ── shared guarantees ─────────────────────────────────────────────
    def test_every_index_route_needs_a_token(self) -> None:
        for method, path in (
            ("GET", "/v1/skills"),
            ("POST", "/v1/skills"),
            ("GET", "/v1/playbooks"),
            ("POST", "/v1/playbooks"),
            ("GET", "/v1/playbooks/x/resolve"),
        ):
            with self.subTest(path=path):
                status, _ = self.request(method, path, token=None)
                self.assertEqual(status, 401)

    def test_unknown_index_routes_are_not_found(self) -> None:
        for method, path in (
            ("GET", "/v1/skills/x/nonsense"),
            ("PUT", "/v1/skills"),
            ("DELETE", "/v1/playbooks"),
            ("GET", "/v1/widgets"),
        ):
            with self.subTest(path=path):
                status, _ = self.request(method, path)
                self.assertEqual(status, 404)

    def test_no_skill_files_are_ever_stored(self) -> None:
        one = self.add_skill("owner/repo@one")
        self.add_playbook(skills=[one["id"]])
        self.request("GET", "/v1/playbooks/backend-review/resolve")
        set_names = {
            path.name
            for path in self.root.rglob("*")
            if path.is_file() and path.name != ".lock"
        }
        self.assertEqual(
            set_names,
            {"github-com-owner-repo-one.json", "backend-review.json"},
        )
        self.assertEqual(list(self.root.rglob("SKILL.md")), [])


if __name__ == "__main__":
    unittest.main()
