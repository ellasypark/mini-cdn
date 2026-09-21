from contextlib import AsyncExitStack
from dataclasses import replace

import fakeredis.aioredis
import httpx
import pytest

from mini_cdn.config import Settings
from mini_cdn.edge import create_app


class OriginStub:
    def __init__(self):
        self.requests = []
        self.status = 200
        self.body = b"hello from origin"
        self.headers = {"content-type": "text/plain", "cache-control": "public, max-age=60"}
        self.error = None

    def __call__(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return httpx.Response(self.status, content=self.body, headers=self.headers)


@pytest.fixture
async def cache():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def origin():
    return OriginStub()


@pytest.fixture
async def edge_factory(cache, origin):
    async with AsyncExitStack() as stack:

        async def make(**overrides):
            upstream = await stack.enter_async_context(
                httpx.AsyncClient(transport=httpx.MockTransport(origin))
            )
            app = create_app(replace(Settings(), **overrides), cache, upstream)
            await stack.enter_async_context(app.router.lifespan_context(app))
            return await stack.enter_async_context(
                httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://edge")
            )

        yield make
