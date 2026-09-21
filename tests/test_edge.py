import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from mini_cdn.cache import cache_key
from mini_cdn.edge import create_app


async def test_shared_cache_across_three_edges(edge_factory, origin, cache):
    edges = [await edge_factory(edge_id=f"edge-{i}") for i in range(1, 4)]
    for index, edge in enumerate(edges):
        response = await edge.get("/assets/hello.txt")
        assert response.status_code == 200
        assert response.content == origin.body
        assert response.headers["x-cache"] == ("MISS" if index == 0 else "HIT")
        assert response.headers["x-edge-id"] == f"edge-{index + 1}"
        assert response.headers["cache-control"] == "no-store"
    assert len(origin.requests) == 1
    key = cache_key("http://origin:8000/assets/hello.txt")
    assert 0 < await cache.ttl(key) <= 30


async def test_expiration_fetches_origin_again(edge_factory, origin):
    edge = await edge_factory(cache_ttl=1)
    assert (await edge.get("/assets/a.txt")).headers["x-cache"] == "MISS"
    assert (await edge.get("/assets/a.txt")).headers["x-cache"] == "HIT"
    await asyncio.sleep(1.05)
    origin.body = b"updated content"
    response = await edge.get("/assets/a.txt")
    assert response.headers["x-cache"] == "MISS"
    assert response.content == b"updated content"
    assert len(origin.requests) == 2


async def test_query_strings_and_origins_have_separate_keys(edge_factory, origin):
    edge = await edge_factory()
    for query in ("v=1", "v=2", "v=1&v=2", "v=2&v=1"):
        response = await edge.get(f"/assets/a.txt?{query}")
        assert response.headers["x-cache"] == "MISS"
        assert origin.requests[-1].url.query == query.encode()
    other = await edge_factory(origin_url="http://another-origin:8000")
    assert (await other.get("/assets/a.txt?v=1")).headers["x-cache"] == "MISS"
    assert len(origin.requests) == 5


async def test_head_has_get_headers_and_no_body(edge_factory, origin):
    edge = await edge_factory()
    response = await edge.head("/assets/a.txt")
    assert response.content == b""
    assert response.headers["content-length"] == str(len(origin.body))
    assert response.headers["x-cache"] == "MISS"
    assert (await edge.get("/assets/a.txt")).content == origin.body
    assert (await edge.head("/assets/a.txt")).headers["x-cache"] == "HIT"
    assert len(origin.requests) == 1


@pytest.mark.parametrize("status", [404, 500])
async def test_non_success_is_not_cached(edge_factory, origin, status):
    origin.status = status
    edge = await edge_factory()
    for _ in range(2):
        response = await edge.get("/assets/a.txt")
        assert response.status_code == status
        assert response.headers["x-cache"] == "BYPASS"
    assert len(origin.requests) == 2


@pytest.mark.parametrize(
    "headers",
    [
        {"cache-control": "private, max-age=60"},
        {"cache-control": "public, no-store, max-age=60"},
        {"cache-control": "public, no-cache, max-age=60"},
        {"cache-control": "public, max-age=0"},
        {"cache-control": "public, max-age=bad"},
        {"cache-control": "public, max-age=60", "vary": "Accept-Language"},
        {"cache-control": "public, max-age=60", "set-cookie": "session=private"},
        {},
    ],
)
async def test_uncacheable_origin_responses(edge_factory, origin, cache, headers):
    origin.headers = headers
    edge = await edge_factory()
    response = await edge.get("/assets/a.txt")
    assert response.status_code == 200
    assert response.headers["x-cache"] == "BYPASS"
    assert "set-cookie" not in response.headers
    assert await cache.dbsize() == 0


@pytest.mark.parametrize("directive", ["no-cache", "no-store"])
async def test_client_bypass_skips_existing_entry_and_does_not_replace_it(
    edge_factory, origin, directive
):
    edge = await edge_factory()
    original = (await edge.get("/assets/a.txt")).content
    origin.body = b"new"
    response = await edge.get("/assets/a.txt", headers={"Cache-Control": directive})
    assert response.content == b"new"
    assert response.headers["x-cache"] == "BYPASS"
    assert (await edge.get("/assets/a.txt")).content == original


@pytest.mark.parametrize("operation", ["get", "set"])
async def test_redis_failure_serves_origin(edge_factory, cache, origin, monkeypatch, operation):
    monkeypatch.setattr(cache, operation, AsyncMock(side_effect=RedisConnectionError("offline")))
    response = await (await edge_factory()).get("/assets/a.txt")
    assert response.status_code == 200
    assert response.content == origin.body
    assert response.headers["x-cache"] == "BYPASS"
    assert response.headers["x-cache-ttl"] == "0"


async def test_health_and_readiness(edge_factory, cache, monkeypatch):
    edge = await edge_factory()
    assert (await edge.get("/healthz")).status_code == 200
    assert (await edge.get("/readyz")).json()["redis"] == "ok"
    monkeypatch.setattr(cache, "ping", AsyncMock(side_effect=RedisConnectionError("offline")))
    assert (await edge.get("/readyz")).status_code == 503
    assert (await edge.get("/healthz")).status_code == 200


@pytest.mark.parametrize(
    ("error", "expected"),
    [(httpx.ConnectError("offline"), 502), (httpx.ReadTimeout("slow"), 504)],
)
async def test_origin_failure_still_serves_warm_cache(edge_factory, origin, error, expected):
    edge = await edge_factory()
    await edge.get("/assets/warm.txt")
    origin.error = error
    assert (await edge.get("/assets/warm.txt")).headers["x-cache"] == "HIT"
    assert (await edge.get("/assets/cold.txt")).status_code == expected


async def test_oversized_asset_and_redirect_are_rejected(edge_factory, origin, cache):
    edge = await edge_factory(max_asset_bytes=2)
    assert (await edge.get("/assets/a.txt")).status_code == 502
    origin.body = b""
    origin.status = 302
    origin.headers = {"location": "http://untrusted.example"}
    assert (await edge.get("/assets/a.txt")).status_code == 502
    assert await cache.dbsize() == 0


@pytest.mark.parametrize("path", ["%2e%2e/secret.txt", "//evil.example/a", "folder//a", "a%5Cb"])
async def test_unsafe_paths_never_reach_origin(edge_factory, origin, path):
    edge = await edge_factory()
    assert (await edge.get(f"/assets/{path}")).status_code == 400
    assert origin.requests == []


@pytest.mark.parametrize("headers", [{"Authorization": "Bearer demo"}, {"Cookie": "demo=1"}])
async def test_credentials_are_rejected(edge_factory, origin, headers):
    edge = await edge_factory()
    assert (await edge.get("/assets/a.txt", headers=headers)).status_code == 400
    assert origin.requests == []


async def test_corrupt_cache_entry_is_replaced(edge_factory, cache, origin):
    await cache.set(cache_key("http://origin:8000/assets/a.txt"), "not-json", ex=30)
    edge = await edge_factory()
    assert (await edge.get("/assets/a.txt")).headers["x-cache"] == "MISS"
    assert (await edge.get("/assets/a.txt")).headers["x-cache"] == "HIT"
    assert len(origin.requests) == 1


async def test_owned_clients_lifecycle():
    app = create_app()
    async with app.router.lifespan_context(app):
        client = app.state.upstream
        assert not client.is_closed
    assert client.is_closed


async def test_upstream_cookies_are_never_replayed(edge_factory, origin):
    origin.headers["set-cookie"] = "private_session=example; Path=/"
    edge = await edge_factory()
    await edge.get("/assets/a.txt")
    del origin.headers["set-cookie"]
    await edge.get("/assets/b.txt")
    assert len(origin.requests) == 2
    assert not origin.requests[1].headers.get("cookie")
