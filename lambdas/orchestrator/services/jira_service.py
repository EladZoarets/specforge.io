from __future__ import annotations

import base64
from types import TracebackType
from urllib.parse import quote

import httpx


class JiraAPIError(Exception):
    """Domain exception for Jira REST API failures.

    Messages intentionally omit the Authorization header and the API token so
    that exception strings are safe to log.
    """


def _body_snippet(
    response: httpx.Response, *, limit: int = 200, auth_header: str | None = None
) -> str:
    """Return a short, truncated body snippet suitable for error messages.

    Defense-in-depth: Jira error bodies sometimes echo request metadata. If
    the snippet contains an ``Authorization`` marker (header name or the
    literal auth header value), replace the entire snippet to guarantee the
    token never lands in exception text.
    """
    try:
        text = response.text
    except Exception:  # pragma: no cover — defensive, httpx.text rarely raises
        return ""
    if len(text) <= limit:
        snippet = text
    else:
        snippet = text[:limit] + "..."
    if "Authorization" in snippet or (auth_header is not None and auth_header in snippet):
        return "<redacted body>"
    return snippet


class JiraService:
    """Thin async wrapper around the Jira Cloud REST API.

    Constructor accepts Jira credentials and an optional injected
    ``httpx.AsyncClient`` (tests always inject). The Basic auth header is
    encoded once at construction time so the raw token is never recomputed
    per request and never passed through format strings that might leak it.
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._auth_header = f"Basic {encoded}"
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient()
            self._owns_client = True

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": self._auth_header}

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` only if we own it.

        When a client is injected by the caller, lifecycle stays with the
        caller; we never close someone else's client.
        """
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> JiraService:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def post_comment(self, issue_key: str, body: str) -> dict:
        """Add a plain-text comment to a Jira issue.

        Uses the simple ``{"body": body}`` request shape (Jira Cloud accepts
        both plain strings and ADF in newer API versions; we stay consistent
        with the plain string form).
        """
        encoded_key = quote(issue_key, safe="")
        url = f"{self._base_url}/rest/api/3/issue/{encoded_key}/comment"
        headers = {**self._auth_headers(), "Content-Type": "application/json"}
        try:
            response = await self._client.post(url, json={"body": body}, headers=headers)
        except httpx.HTTPError as exc:
            raise JiraAPIError(
                f"Jira request failed: POST {url}: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise JiraAPIError(
                f"Jira API error: POST {url} returned {response.status_code}: "
                f"{_body_snippet(response, auth_header=self._auth_header)}"
            )
        try:
            return response.json()
        except ValueError:
            return {}

    async def attach_file(
        self, issue_key: str, content_bytes: bytes, filename: str
    ) -> dict:
        """Upload an attachment to a Jira issue.

        Jira requires the ``X-Atlassian-Token: no-check`` header to bypass
        XSRF protection on multipart uploads.
        """
        encoded_key = quote(issue_key, safe="")
        url = f"{self._base_url}/rest/api/3/issue/{encoded_key}/attachments"
        headers = {
            **self._auth_headers(),
            "X-Atlassian-Token": "no-check",
        }
        files = {"file": (filename, content_bytes)}
        try:
            response = await self._client.post(url, files=files, headers=headers)
        except httpx.HTTPError as exc:
            raise JiraAPIError(
                f"Jira request failed: POST {url}: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise JiraAPIError(
                f"Jira API error: POST {url} returned {response.status_code}: "
                f"{_body_snippet(response, auth_header=self._auth_header)}"
            )
        try:
            return response.json()
        except ValueError:
            return {}
