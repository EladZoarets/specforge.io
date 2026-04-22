from __future__ import annotations

import base64

import httpx


class JiraAPIError(Exception):
    """Domain exception for Jira REST API failures.

    Messages intentionally omit the Authorization header and the API token so
    that exception strings are safe to log.
    """


def _body_snippet(response: httpx.Response, *, limit: int = 200) -> str:
    """Return a short, truncated body snippet suitable for error messages."""
    try:
        text = response.text
    except Exception:  # pragma: no cover — defensive, httpx.text rarely raises
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


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
        else:
            self._client = httpx.AsyncClient()

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": self._auth_header}

    async def post_comment(self, issue_key: str, body: str) -> dict:
        """Add a plain-text comment to a Jira issue.

        Uses the simple ``{"body": body}`` request shape (Jira Cloud accepts
        both plain strings and ADF in newer API versions; we stay consistent
        with the plain string form).
        """
        url = f"{self._base_url}/rest/api/3/issue/{issue_key}/comment"
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
                f"{_body_snippet(response)}"
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
        url = f"{self._base_url}/rest/api/3/issue/{issue_key}/attachments"
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
                f"{_body_snippet(response)}"
            )
        try:
            return response.json()
        except ValueError:
            return {}
