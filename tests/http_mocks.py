from unittest.mock import AsyncMock, MagicMock


class _StreamCtx:
    def __init__(self, fake_get, url, kwargs):
        self._fake_get = fake_get
        self._url = url
        self._kwargs = kwargs

    async def __aenter__(self):
        resp = await self._fake_get(self._url, **self._kwargs)
        content = getattr(resp, "content", b"") or b""

        async def aiter_bytes():
            yield content

        resp.aiter_bytes = aiter_bytes
        return resp

    async def __aexit__(self, *exc):
        return None


def make_http_client(fake_get):
    """Mock httpx client whose get() and stream() both route to the same fake response function."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)
    client.stream = lambda method, url, **kwargs: _StreamCtx(fake_get, url, kwargs)
    return client
