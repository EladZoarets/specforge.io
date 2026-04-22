from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from services.jira_service import JiraAPIError, JiraService

BASE_URL = "https://test.atlassian.net"
EMAIL = "e@x.com"
TOKEN = "super-secret-token"
EXPECTED_BASIC = "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()


@pytest.fixture
def service():
    """Yield a JiraService wired to a fresh httpx.AsyncClient."""

    async def _factory():
        client = httpx.AsyncClient()
        return JiraService(BASE_URL, EMAIL, TOKEN, client=client), client

    return _factory


@pytest.mark.asyncio
async def test_post_comment_hits_correct_url_and_sends_basic_auth():
    url = f"{BASE_URL}/rest/api/3/issue/ABC-1/comment"
    with respx.mock(assert_all_called=True) as router:
        route = router.post(url).mock(
            return_value=httpx.Response(201, json={"id": "10001"})
        )
        async with httpx.AsyncClient() as client:
            svc = JiraService(BASE_URL, EMAIL, TOKEN, client=client)
            result = await svc.post_comment("ABC-1", "hello world")
        assert result == {"id": "10001"}
        assert route.called
        request = route.calls.last.request
        assert request.url == url
        assert request.headers["Authorization"] == EXPECTED_BASIC
        # Body is the simple {"body": ...} shape.
        assert request.headers["content-type"].startswith("application/json")
        assert json.loads(request.content) == {"body": "hello world"}


@pytest.mark.asyncio
async def test_post_comment_401_raises_jira_api_error_without_token():
    url = f"{BASE_URL}/rest/api/3/issue/ABC-1/comment"
    with respx.mock() as router:
        router.post(url).mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        async with httpx.AsyncClient() as client:
            svc = JiraService(BASE_URL, EMAIL, TOKEN, client=client)
            with pytest.raises(JiraAPIError) as excinfo:
                await svc.post_comment("ABC-1", "hi")
    message = str(excinfo.value)
    assert "401" in message
    # Secrets must never leak into the exception message.
    assert TOKEN not in message
    assert EXPECTED_BASIC not in message
    assert "Authorization" not in message


@pytest.mark.asyncio
async def test_post_comment_500_raises_jira_api_error_without_token():
    url = f"{BASE_URL}/rest/api/3/issue/XYZ-9/comment"
    with respx.mock() as router:
        router.post(url).mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        async with httpx.AsyncClient() as client:
            svc = JiraService(BASE_URL, EMAIL, TOKEN, client=client)
            with pytest.raises(JiraAPIError) as excinfo:
                await svc.post_comment("XYZ-9", "boom")
    message = str(excinfo.value)
    assert "500" in message
    assert TOKEN not in message
    assert EXPECTED_BASIC not in message


@pytest.mark.asyncio
async def test_attach_file_hits_correct_url_with_xsrf_header_and_basic_auth():
    url = f"{BASE_URL}/rest/api/3/issue/ABC-2/attachments"
    with respx.mock(assert_all_called=True) as router:
        route = router.post(url).mock(
            return_value=httpx.Response(200, json=[{"id": "20001"}])
        )
        async with httpx.AsyncClient() as client:
            svc = JiraService(BASE_URL, EMAIL, TOKEN, client=client)
            result = await svc.attach_file("ABC-2", b"file-bytes", "spec.md")
        assert result == [{"id": "20001"}]
        assert route.called
        request = route.calls.last.request
        assert request.url == url
        assert request.headers["Authorization"] == EXPECTED_BASIC
        assert request.headers["X-Atlassian-Token"] == "no-check"
        # Multipart body contains the filename and the bytes payload.
        body = request.content
        assert b"spec.md" in body
        assert b"file-bytes" in body


@pytest.mark.asyncio
async def test_post_comment_does_not_include_xsrf_header():
    url = f"{BASE_URL}/rest/api/3/issue/ABC-1/comment"
    with respx.mock() as router:
        route = router.post(url).mock(
            return_value=httpx.Response(201, json={"id": "1"})
        )
        async with httpx.AsyncClient() as client:
            svc = JiraService(BASE_URL, EMAIL, TOKEN, client=client)
            await svc.post_comment("ABC-1", "hi")
        request = route.calls.last.request
        # The XSRF-bypass header is only needed for attachments; don't set it
        # for regular JSON endpoints.
        assert "X-Atlassian-Token" not in request.headers


@pytest.mark.asyncio
async def test_attach_file_non_2xx_raises_jira_api_error_without_token():
    url = f"{BASE_URL}/rest/api/3/issue/ABC-2/attachments"
    with respx.mock() as router:
        router.post(url).mock(
            return_value=httpx.Response(403, text="Forbidden")
        )
        async with httpx.AsyncClient() as client:
            svc = JiraService(BASE_URL, EMAIL, TOKEN, client=client)
            with pytest.raises(JiraAPIError) as excinfo:
                await svc.attach_file("ABC-2", b"data", "spec.md")
    message = str(excinfo.value)
    assert "403" in message
    assert TOKEN not in message
    assert EXPECTED_BASIC not in message
    assert "Authorization" not in message


@pytest.mark.asyncio
async def test_base_url_trailing_slash_is_normalized():
    # Construct with a trailing slash; request URL should still be well-formed
    # (no double slash before /rest).
    url = f"{BASE_URL}/rest/api/3/issue/ABC-1/comment"
    with respx.mock() as router:
        route = router.post(url).mock(
            return_value=httpx.Response(201, json={"id": "1"})
        )
        async with httpx.AsyncClient() as client:
            svc = JiraService(BASE_URL + "/", EMAIL, TOKEN, client=client)
            await svc.post_comment("ABC-1", "hi")
        assert route.called
