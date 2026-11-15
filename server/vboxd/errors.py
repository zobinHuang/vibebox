class VboxdError(Exception):
    """Base class for every failure the session server reports to a client."""

    ERROR_HTTP_STATUS = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def as_payload(self) -> dict[str, object]:
        """Render the failure as the JSON body returned to the client.

        Returns:
            a mapping naming the failure kind and its message
        """
        return {
            "error": type(self).__name__,
            "message": self.message,
        }


class ConfigError(VboxdError):
    """The server configuration is absent, unreadable or incomplete."""

    ERROR_HTTP_STATUS = 500


class AuthError(VboxdError):
    """The request carried no bearer token, or one that does not match."""

    ERROR_HTTP_STATUS = 401


class ValidationError(VboxdError):
    """A request field is missing or would not be safe to store."""

    ERROR_HTTP_STATUS = 400


class NotFoundError(VboxdError):
    """The addressed route or session is unknown to this server."""

    ERROR_HTTP_STATUS = 404


class PayloadTooLargeError(VboxdError):
    """The request body is larger than the configured per-request limit."""

    ERROR_HTTP_STATUS = 413


class CursorConflictError(VboxdError):
    """The client's write offset disagrees with the stored transcript."""

    ERROR_HTTP_STATUS = 409

    def __init__(
        self,
        message: str,
        cursor: int,
        prefix_sha256: str,
    ) -> None:
        super().__init__(message)
        self.cursor = cursor
        self.prefix_sha256 = prefix_sha256

    def as_payload(self) -> dict[str, object]:
        """Render the conflict, including the offset the client should resume at.

        Returns:
            the base failure mapping plus the server's own cursor and prefix digest
        """
        map_payload = super().as_payload()
        map_payload["cursor"] = self.cursor
        map_payload["prefix_sha256"] = self.prefix_sha256
        return map_payload
