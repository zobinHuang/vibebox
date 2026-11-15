import argparse
import json
import sys
from pathlib import Path
from vboxd import VBOXD_VERSION
from vboxd.config import ServerConfig
from vboxd.errors import ConfigError, VboxdError
from vboxd.http import VboxdServer
from vboxd.store import SessionStore


class ServerCli:
    """The command line around the vibebox session server."""

    CLI_DEFAULT_CONFIG = "~/.config/vboxd/config.json"
    CLI_DEFAULT_DATA_ROOT = "~/.local/share/vboxd/data"

    def __init__(self, path_config: Path) -> None:
        self._path_config = path_config

    def run_init(
        self,
        data_root: Path,
        host: str,
        port: int,
        tls_cert: Path | None,
        tls_key: Path | None,
    ) -> int:
        """Create or refresh the server configuration, keeping any existing token.

        Args:
            data_root: where session transcripts are stored
            host: the address to listen on
            port: the port to listen on
            tls_cert: the certificate chain to serve, or None for plain HTTP
            tls_key: the private key matching the certificate

        Returns:
            the process exit status
        """
        token = ServerConfig.generate_token()
        if self._path_config.exists():
            token = ServerConfig.from_file(self._path_config).token
        config = ServerConfig(
            data_root=data_root,
            host=host,
            port=port,
            token=token,
            max_body_bytes=ServerConfig.CONFIG_DEFAULT_MAX_BODY_BYTES,
            tls_cert=tls_cert,
            tls_key=tls_key,
        )
        config.data_root.mkdir(parents=True, exist_ok=True)
        config.write_to(self._path_config)
        sys.stdout.write(f"{self._path_config}\n")
        return 0

    def run_serve(self) -> int:
        """Serve requests until interrupted.

        Returns:
            the process exit status
        """
        config = ServerConfig.from_file(self._path_config)
        server = VboxdServer(
            config=config,
            store=SessionStore(config.data_root),
        )
        scheme = "https" if config.is_tls_enabled() else "http"
        sys.stderr.write(
            f"vboxd {VBOXD_VERSION} serving {config.data_root} on "
            f"{scheme}://{config.host}:{server.bound_port()}\n"
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("vboxd stopping\n")
        finally:
            server.server_close()
        return 0

    def run_token(self) -> int:
        """Print the access token clients must present.

        Returns:
            the process exit status
        """
        sys.stdout.write(f"{ServerConfig.from_file(self._path_config).token}\n")
        return 0

    def run_list(self, limit: int) -> int:
        """Print the stored sessions as JSON, most recently written first.

        Args:
            limit: the greatest number of sessions to print

        Returns:
            the process exit status
        """
        config = ServerConfig.from_file(self._path_config)
        list_meta = SessionStore(config.data_root).list_sessions(limit)
        json.dump(
            [meta.as_payload() for meta in list_meta],
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0


def main(list_argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vboxd")
    parser.add_argument("--config", default=ServerCli.CLI_DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_init = subparsers.add_parser("init")
    parser_init.add_argument("--data-root", default=ServerCli.CLI_DEFAULT_DATA_ROOT)
    parser_init.add_argument("--host", default=ServerConfig.CONFIG_DEFAULT_HOST)
    parser_init.add_argument("--port", type=int, default=ServerConfig.CONFIG_DEFAULT_PORT)
    parser_init.add_argument("--tls-cert", default=None)
    parser_init.add_argument("--tls-key", default=None)

    subparsers.add_parser("serve")
    subparsers.add_parser("token")

    parser_list = subparsers.add_parser("ls")
    parser_list.add_argument(
        "--limit",
        type=int,
        default=SessionStore.STORE_LIST_DEFAULT_LIMIT,
    )

    args = parser.parse_args(list_argv)
    cli = ServerCli(Path(args.config).expanduser())
    try:
        if args.command == "init":
            return cli.run_init(
                data_root=Path(args.data_root).expanduser(),
                host=args.host,
                port=args.port,
                tls_cert=Path(args.tls_cert).expanduser() if args.tls_cert else None,
                tls_key=Path(args.tls_key).expanduser() if args.tls_key else None,
            )
        if args.command == "serve":
            return cli.run_serve()
        if args.command == "token":
            return cli.run_token()
        return cli.run_list(args.limit)
    except ConfigError as err:
        sys.stderr.write(f"vboxd: {err.message}\n")
        return 2
    except VboxdError as err:
        sys.stderr.write(f"vboxd: {err.message}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
