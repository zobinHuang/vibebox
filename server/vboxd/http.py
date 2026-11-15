import json
import secrets
import ssl
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from vboxd import VBOXD_VERSION
from vboxd.config import ServerConfig
from vboxd.errors import (
    AuthError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
    VboxdError,
)
from vboxd.playbooks import PlaybookIndex
from vboxd.skills import SkillIndex
from vboxd.store import SessionMeta, SessionQuery, SessionStore


class VboxdRequestHandler(BaseHTTPRequestHandler):
    """The HTTP surface through which clients upload agent session transcripts."""

    HANDLER_API_PREFIX = "v1"
    HANDLER_BEARER_PREFIX = "Bearer "

    server_version = f"vboxd/{VBOXD_VERSION}"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Serve a health check or a session listing."""
        self._dispatch("GET")

    def do_POST(self) -> None:
        """Serve a session registration or a session close."""
        self._dispatch("POST")

    def do_PUT(self) -> None:
        """Serve a transcript append."""
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        """Serve a change to an indexed skill or playbook."""
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        """Serve the removal of an indexed skill or playbook."""
        self._dispatch("DELETE")

    def log_message(self, format: str, *args: object) -> None:
        """Write one request line to stderr.

        Args:
            format: the printf-style template the base class supplies
            args: the template's arguments
        """
        sys.stderr.write(
            "vboxd %s %s\n"
            % (
                self.address_string(),
                format % args,
            )
        )

    def _dispatch(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            list_segments = [segment for segment in parsed.path.split("/") if segment]
            if not list_segments or list_segments[0] != self.HANDLER_API_PREFIX:
                raise NotFoundError(f"no such route: {parsed.path}")
            self._route(
                method=method,
                list_segments=list_segments[1:],
                map_query=parse_qs(parsed.query),
            )
        except VboxdError as err:
            self._respond_error(err)
        except OSError as err:
            self._respond_error(VboxdError(f"storage failure: {err}"))

    def _route(
        self,
        method: str,
        list_segments: list[str],
        map_query: dict[str, list[str]],
    ) -> None:
        if list_segments == ["healthz"] and method == "GET":
            self._respond_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "version": VBOXD_VERSION,
                },
            )
            return

        self._authenticate()

        collection = list_segments[0] if list_segments else ""
        if collection in ("sessions", "machines", "accounts"):
            self._route_sessions(method, list_segments, map_query)
        elif collection == "skills":
            self._route_skills(method, list_segments, map_query)
        elif collection == "playbooks":
            self._route_playbooks(method, list_segments, map_query)
        else:
            raise NotFoundError(self._no_such_route(method, list_segments))

    def _no_such_route(self, method: str, list_segments: list[str]) -> str:
        return (
            f"no such route: {method} /{self.HANDLER_API_PREFIX}/"
            + "/".join(list_segments)
        )

    def _route_sessions(
        self,
        method: str,
        list_segments: list[str],
        map_query: dict[str, list[str]],
    ) -> None:
        if list_segments == ["sessions"] and method == "GET":
            self._handle_query(map_query)
            return

        if list_segments == ["machines"] and method == "GET":
            self._respond_json(
                HTTPStatus.OK,
                {"machines": [s.as_payload() for s in self._store().list_machines()]},
            )
            return

        if list_segments == ["accounts"] and method == "GET":
            self._respond_json(
                HTTPStatus.OK,
                {"accounts": [s.as_payload() for s in self._store().list_accounts()]},
            )
            return

        if len(list_segments) == 2 and list_segments[0] == "sessions" and method == "GET":
            self._respond_json(
                HTTPStatus.OK,
                self._store().meta_of(list_segments[1]).as_payload(),
            )
            return

        if len(list_segments) == 3 and list_segments[0] == "sessions":
            session_id = list_segments[1]
            action = list_segments[2]
            if action == "begin" and method == "POST":
                self._handle_begin(session_id)
                return
            if action == "data" and method == "PUT":
                self._handle_data(session_id, map_query)
                return
            if action == "close" and method == "POST":
                self._handle_close(session_id)
                return
            if action == "transcript" and method == "GET":
                self._handle_transcript(session_id, map_query)
                return

        raise NotFoundError(self._no_such_route(method, list_segments))

    def _route_skills(
        self,
        method: str,
        list_segments: list[str],
        map_query: dict[str, list[str]],
    ) -> None:
        skills = self._skills()
        if list_segments == ["skills"]:
            if method == "GET":
                list_record = skills.query(
                    text=self._query_text(map_query, "q"),
                    repo=self._query_text(map_query, "repo"),
                    limit=self._index_limit(map_query, SkillIndex.INDEX_QUERY_DEFAULT_LIMIT),
                )
                self._respond_json(
                    HTTPStatus.OK,
                    {"skills": [record.as_payload() for record in list_record]},
                )
                return
            if method == "POST":
                self._respond_json(
                    HTTPStatus.OK,
                    skills.register(self._read_json_body()).as_payload(),
                )
                return

        if len(list_segments) == 2:
            skill_id = list_segments[1]
            if method == "GET":
                self._respond_json(HTTPStatus.OK, skills.get(skill_id).as_payload())
                return
            if method == "PATCH":
                self._respond_json(
                    HTTPStatus.OK,
                    skills.update(skill_id, self._read_json_body()).as_payload(),
                )
                return
            if method == "DELETE":
                self._read_body()
                skills.delete(skill_id)
                self._respond_json(HTTPStatus.OK, {"ok": True})
                return

        if len(list_segments) == 3 and method in ("GET", "POST"):
            skill_id = list_segments[1]
            action = list_segments[2]
            if action == "versions" and method == "GET":
                record = skills.get(skill_id)
                self._respond_json(
                    HTTPStatus.OK,
                    {
                        "skill_id": record.id,
                        "default_ref": record.default_ref,
                        "versions": [v.as_payload() for v in record.list_versions],
                    },
                )
                return
            if action == "refresh" and method == "POST":
                self._read_body()
                record, version = skills.resolve_version(skill_id, SkillIndex.INDEX_PIN_LATEST)
                self._respond_json(
                    HTTPStatus.OK,
                    {
                        "skill_id": record.id,
                        "default_ref": record.default_ref,
                        "version": version.as_payload(),
                    },
                )
                return

        raise NotFoundError(self._no_such_route(method, list_segments))

    def _route_playbooks(
        self,
        method: str,
        list_segments: list[str],
        map_query: dict[str, list[str]],
    ) -> None:
        playbooks = self._playbooks()
        if list_segments == ["playbooks"]:
            if method == "GET":
                list_record = playbooks.query(
                    text=self._query_text(map_query, "q"),
                    limit=self._index_limit(
                        map_query,
                        PlaybookIndex.INDEX_QUERY_DEFAULT_LIMIT,
                    ),
                )
                self._respond_json(
                    HTTPStatus.OK,
                    {"playbooks": [record.as_payload() for record in list_record]},
                )
                return
            if method == "POST":
                self._respond_json(
                    HTTPStatus.CREATED,
                    playbooks.create(self._read_json_body()).as_payload(),
                )
                return

        if len(list_segments) == 2:
            playbook_id = list_segments[1]
            if method == "GET":
                self._respond_json(HTTPStatus.OK, playbooks.get(playbook_id).as_payload())
                return
            if method == "PATCH":
                self._respond_json(
                    HTTPStatus.OK,
                    playbooks.update(playbook_id, self._read_json_body()).as_payload(),
                )
                return
            if method == "DELETE":
                self._read_body()
                playbooks.delete(playbook_id)
                self._respond_json(HTTPStatus.OK, {"ok": True})
                return

        if len(list_segments) == 3 and list_segments[2] == "resolve" and method == "GET":
            self._respond_json(
                HTTPStatus.OK,
                playbooks.resolve(list_segments[1]).as_payload(),
            )
            return

        raise NotFoundError(self._no_such_route(method, list_segments))

    def _index_limit(self, map_query: dict[str, list[str]], default: int) -> int:
        if "limit" not in map_query:
            return default
        limit = self._query_int(map_query, "limit")
        if limit < 1 or limit > SessionQuery.QUERY_MAX_LIMIT:
            raise ValidationError(f"limit out of range: {limit}")
        return limit

    def _handle_begin(self, session_id: str) -> None:
        meta = SessionMeta.from_request(
            session_id=session_id,
            map_body=self._read_json_body(),
        )
        cursor = self._store().begin(meta)
        self._respond_json(HTTPStatus.OK, cursor.as_payload())

    def _handle_data(
        self,
        session_id: str,
        map_query: dict[str, list[str]],
    ) -> None:
        offset = self._query_int(map_query, "offset")
        do_reset = self._query_flag(map_query, "reset")
        cursor = self._store().append(
            session_id=session_id,
            offset=offset,
            chunk=self._read_body(),
            do_reset=do_reset,
        )
        self._respond_json(HTTPStatus.OK, cursor.as_payload())

    def _handle_close(self, session_id: str) -> None:
        self._read_body()
        meta = self._store().close(session_id)
        self._respond_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "closed_at": meta.closed_at,
            },
        )

    def _handle_query(self, map_query: dict[str, list[str]]) -> None:
        self._respond_json(
            HTTPStatus.OK,
            self._store().query(self._build_query(map_query)).as_payload(),
        )

    def _build_query(self, map_query: dict[str, list[str]]) -> SessionQuery:
        limit = SessionQuery.QUERY_DEFAULT_LIMIT
        if "limit" in map_query:
            limit = self._query_int(map_query, "limit")
        if limit < 1 or limit > SessionQuery.QUERY_MAX_LIMIT:
            raise ValidationError(f"limit out of range: {limit}")

        agent = self._query_text(map_query, "agent")
        if agent and agent not in SessionMeta.SESSION_AGENTS:
            raise ValidationError(f"unknown agent: {agent!r}")
        status = self._query_text(map_query, "status")
        if status and status not in SessionQuery.QUERY_STATUSES:
            raise ValidationError(f"unknown status: {status!r}")
        time_field = self._query_text(map_query, "time") or "updated_at"
        if not time_field.endswith("_at"):
            time_field = f"{time_field}_at"
        if time_field not in SessionQuery.QUERY_TIME_FIELDS:
            raise ValidationError(f"unknown time field: {time_field!r}")

        since = self._query_text(map_query, "since")
        until = self._query_text(map_query, "until")
        return SessionQuery(
            agent=agent,
            account=self._query_text(map_query, "account"),
            hostname=self._query_text(map_query, "hostname"),
            project=self._query_text(map_query, "project"),
            branch=self._query_text(map_query, "branch"),
            status=status,
            since=SessionQuery.parse_timestamp(since) if since else None,
            until=SessionQuery.parse_timestamp(until) if until else None,
            time_field=time_field,
            limit=limit,
            cursor=self._query_text(map_query, "cursor"),
        )

    def _handle_transcript(
        self,
        session_id: str,
        map_query: dict[str, list[str]],
    ) -> None:
        offset = 0
        if "offset" in map_query:
            offset = self._query_int(map_query, "offset")
        limit = None
        if "limit" in map_query:
            limit = self._query_int(map_query, "limit")
            if limit < 1:
                raise ValidationError(f"limit out of range: {limit}")
        chunk = self._store().read_transcript(
            session_id=session_id,
            offset=offset,
            limit=limit,
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(chunk.data)))
        self.send_header("X-Vbox-Offset", str(chunk.offset))
        self.send_header("X-Vbox-Total", str(chunk.total))
        self.end_headers()
        self.wfile.write(chunk.data)

    def _authenticate(self) -> None:
        header = self.headers.get("Authorization", "")
        if not header.startswith(self.HANDLER_BEARER_PREFIX):
            raise AuthError("request carried no bearer token")
        offered = header[len(self.HANDLER_BEARER_PREFIX):]
        if not secrets.compare_digest(offered, self._config().token):
            raise AuthError("bearer token not accepted")

    def _read_body(self) -> bytes:
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            raise ValidationError(
                "chunked request bodies are not accepted; send Content-Length"
            )
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError as err:
            raise ValidationError(f"unusable Content-Length: {raw_length!r}") from err
        if length < 0:
            raise ValidationError(f"negative Content-Length: {length}")
        if length > self._config().max_body_bytes:
            raise PayloadTooLargeError(
                f"body of {length} bytes exceeds the {self._config().max_body_bytes}"
                " byte limit"
            )
        return self.rfile.read(length)

    def _read_json_body(self) -> dict[str, object]:
        body = self._read_body()
        if not body:
            return {}
        try:
            map_body = json.loads(body)
        except json.JSONDecodeError as err:
            raise ValidationError("request body is not valid JSON") from err
        if not isinstance(map_body, dict):
            raise ValidationError("request body is not a JSON object")
        return map_body

    def _query_int(self, map_query: dict[str, list[str]], name: str) -> int:
        list_values = map_query.get(name) or []
        if not list_values:
            raise ValidationError(f"missing query parameter: {name}")
        try:
            return int(list_values[0])
        except ValueError as err:
            raise ValidationError(
                f"query parameter {name} is not an integer: {list_values[0]!r}"
            ) from err

    def _query_text(self, map_query: dict[str, list[str]], name: str) -> str | None:
        list_values = map_query.get(name) or []
        if not list_values:
            return None
        return list_values[0].strip() or None

    def _query_flag(self, map_query: dict[str, list[str]], name: str) -> bool:
        list_values = map_query.get(name) or []
        if not list_values:
            return False
        return list_values[0] in ("1", "true", "yes")

    def _config(self) -> ServerConfig:
        return self.server.config

    def _store(self) -> SessionStore:
        return self.server.store

    def _skills(self) -> SkillIndex:
        return self.server.skills

    def _playbooks(self) -> PlaybookIndex:
        return self.server.playbooks

    def _respond_json(self, status: HTTPStatus, map_payload: dict[str, object]) -> None:
        body = json.dumps(map_payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_error(self, err: VboxdError) -> None:
        self.close_connection = True
        self._respond_json(HTTPStatus(err.ERROR_HTTP_STATUS), err.as_payload())


class VboxdServer(ThreadingHTTPServer):
    """A threaded HTTP server that carries the configuration and the session store."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        config: ServerConfig,
        store: SessionStore,
        skills: SkillIndex | None = None,
        playbooks: PlaybookIndex | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.skills = skills or SkillIndex(config.data_root)
        self.playbooks = playbooks or PlaybookIndex(config.data_root, self.skills)
        super().__init__(
            (config.host, config.port),
            VboxdRequestHandler,
        )
        if config.is_tls_enabled():
            self._wrap_tls()

    def bound_port(self) -> int:
        """Report the port the server actually bound.

        Returns:
            the listening port, which differs from the configured one when the
            configuration asked for port zero
        """
        return int(self.socket.getsockname()[1])

    def _wrap_tls(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=str(self.config.tls_cert),
            keyfile=str(self.config.tls_key),
        )
        self.socket = context.wrap_socket(
            self.socket,
            server_side=True,
        )
