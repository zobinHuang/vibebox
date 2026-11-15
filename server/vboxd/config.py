import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from vboxd.errors import ConfigError


@dataclass
class ServerConfig:
    """Everything the session server needs in order to run."""

    data_root: Path
    host: str
    port: int
    token: str
    max_body_bytes: int
    tls_cert: Path | None
    tls_key: Path | None

    CONFIG_DEFAULT_HOST = "0.0.0.0"
    CONFIG_DEFAULT_PORT = 8787
    CONFIG_DEFAULT_MAX_BODY_BYTES = 64 * 1024 * 1024
    CONFIG_TOKEN_BYTES = 32
    CONFIG_FILE_MODE = 0o600

    @staticmethod
    def generate_token() -> str:
        """Mint a fresh access token for a server install.

        Returns:
            a URL-safe random token
        """
        return secrets.token_urlsafe(ServerConfig.CONFIG_TOKEN_BYTES)

    @classmethod
    def from_file(cls, path: Path) -> "ServerConfig":
        """Load a server configuration from its JSON file.

        Args:
            path: the configuration file to read

        Returns:
            the parsed configuration

        Raises:
            ConfigError: if the file is missing, malformed or lacks a token
        """
        try:
            with path.open(encoding="utf-8") as handle:
                map_raw = json.load(handle)
        except FileNotFoundError as err:
            raise ConfigError(f"no server configuration at {path}") from err
        except json.JSONDecodeError as err:
            raise ConfigError(f"malformed server configuration at {path}") from err

        token = map_raw.get("token", "")
        if not token:
            raise ConfigError(f"server configuration at {path} carries no token")
        data_root = map_raw.get("data_root", "")
        if not data_root:
            raise ConfigError(f"server configuration at {path} carries no data_root")

        tls_cert = map_raw.get("tls_cert") or None
        tls_key = map_raw.get("tls_key") or None
        return cls(
            data_root=Path(data_root).expanduser(),
            host=map_raw.get("host", cls.CONFIG_DEFAULT_HOST),
            port=int(map_raw.get("port", cls.CONFIG_DEFAULT_PORT)),
            token=token,
            max_body_bytes=int(
                map_raw.get("max_body_bytes", cls.CONFIG_DEFAULT_MAX_BODY_BYTES)
            ),
            tls_cert=Path(tls_cert).expanduser() if tls_cert else None,
            tls_key=Path(tls_key).expanduser() if tls_key else None,
        )

    def write_to(self, path: Path) -> None:
        """Persist this configuration, readable only by its owner.

        Args:
            path: the configuration file to write
        """
        map_raw: dict[str, object] = {
            "data_root": str(self.data_root),
            "host": self.host,
            "port": self.port,
            "token": self.token,
            "max_body_bytes": self.max_body_bytes,
            "tls_cert": str(self.tls_cert) if self.tls_cert else None,
            "tls_key": str(self.tls_key) if self.tls_key else None,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(map_raw, handle, indent=2)
            handle.write("\n")
        path.chmod(self.CONFIG_FILE_MODE)

    def is_tls_enabled(self) -> bool:
        """Report whether the server should wrap its socket in TLS.

        Returns:
            True when both a certificate and a key are configured
        """
        return self.tls_cert is not None and self.tls_key is not None
