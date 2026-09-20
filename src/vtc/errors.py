"""CLI error types. Messages must never include secrets."""


class VtcError(Exception):
    exit_code = 1
    status = "error"

    def __init__(self, message: str, *, status: str | None = None) -> None:
        super().__init__(message)
        if status is not None:
            self.status = status


class UsageError(VtcError):
    exit_code = 3
    status = "usage_error"


class MissingSession(VtcError):
    exit_code = 2
    status = "unverified"


class LoginFailed(VtcError):
    exit_code = 4
    status = "auth_failed"


class SecretInputError(VtcError):
    exit_code = 4
    status = "secret_input_blocked"


class NotImplementedYet(VtcError):
    exit_code = 1
    status = "not_implemented"


class WriteBlocked(VtcError):
    exit_code = 4
    status = "write_blocked"
