from __future__ import annotations


class PharosError(Exception):
    """Base exception for all Pharos Discovery errors."""

    pass


class RegistryUnavailable(PharosError):
    """Registry endpoint is unreachable or returned an error."""

    def __init__(self, url: str, status: int | None = None, detail: str | None = None):
        self.url = url
        self.status = status
        self.detail = detail
        msg = f"Registry unavailable: {url}"
        if status:
            msg += f" (HTTP {status})"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class NoServersFound(PharosError):
    """Search returned zero results."""

    def __init__(self, query: str = ""):
        self.query = query
        msg = "No servers found"
        if query:
            msg += f" for query: {query}"
        super().__init__(msg)


class ApprovalDenied(PharosError):
    """User denied the approval request."""

    def __init__(self, server_id: str, deny_reason: str | None = None):
        self.server_id = server_id
        self.deny_reason = deny_reason
        msg = f"Approval denied for {server_id}"
        if deny_reason:
            msg += f": {deny_reason}"
        super().__init__(msg)


class ScopeNotApproved(PharosError):
    """Tool call attempted outside approved scopes."""

    def __init__(self, scope: str, server_id: str):
        self.scope = scope
        self.server_id = server_id
        super().__init__(f"Scope '{scope}' not approved for server {server_id}")


class ConnectionFailed(PharosError):
    """Failed to connect to MCP server."""

    def __init__(self, server_id: str, detail: str):
        self.server_id = server_id
        self.detail = detail
        super().__init__(f"Connection failed to {server_id}: {detail}")


class DiscoveryDegraded(PharosError):
    """All registries unavailable and no cached data."""

    def __init__(self):
        super().__init__(
            "Discovery degraded: all registries unavailable and no cache"
        )


class SignatureVerificationFailed(PharosError):
    """Server card or token signature verification failed."""

    def __init__(self, detail: str = "Invalid signature"):
        self.detail = detail
        super().__init__(detail)


class HeadlessApprovalRequired(PharosError):
    """Novel server encountered in headless mode."""

    def __init__(self, server_id: str):
        self.server_id = server_id
        super().__init__(
            f"Headless mode requires pre-approved server: {server_id}"
        )


class ConsentFatigueWarning(PharosError):
    """User has approved >5 novel servers in one session."""

    def __init__(self, count: int):
        self.count = count
        super().__init__(
            f"Consent fatigue: {count} novel servers approved this session"
        )


class OAuthError(PharosError):
    """OAuth flow failed."""

    def __init__(self, error: str, detail: str | None = None):
        self.error = error
        self.detail = detail
        msg = f"OAuth error: {error}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class TransportError(PharosError):
    """MCP transport-level error."""

    def __init__(self, transport: str, detail: str):
        self.transport = transport
        self.detail = detail
        super().__init__(f"Transport error ({transport}): {detail}")
