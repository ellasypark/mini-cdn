"""Public asset gateway with shared Redis caching and graceful cache failure."""

import logging
import re
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError

from mini_cdn.cache import Entry, cache_key, public_ttl
from mini_cdn.config import Settings

logger = logging.getLogger(__name__)
SAFE_HEADERS = ("content-type", "etag", "last-modified", "x-origin-request")


def create_app(
    settings: Settings | None = None,
    cache: Redis | None = None,
    upstream: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.cache = (
            cache
            if cache is not None
            else Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
        )
        app.state.upstream = (
            upstream
            if upstream is not None
            else httpx.AsyncClient(
                timeout=settings.origin_timeout,
                follow_redirects=False,
                trust_env=False,
                headers={"Accept-Encoding": "identity"},
            )
        )
        try:
            yield
        finally:
            if cache is None:
                await app.state.cache.aclose()
            if upstream is None:
                await app.state.upstream.aclose()

    app = FastAPI(title=f"Mini CDN {settings.edge_id}", lifespan=lifespan)

    @app.get("/healthz")
    async def health():
        # HAProxy uses liveness so a Redis outage does not remove every edge.
        return {"status": "ok", "edge": settings.edge_id}

    @app.get("/readyz")
    async def ready(request: Request):
        try:
            await request.app.state.cache.ping()
        except RedisError as exc:
            raise HTTPException(503, "Redis unavailable; origin bypass remains available") from exc
        return {"status": "ok", "edge": settings.edge_id, "redis": "ok"}

    def respond(entry: Entry, request: Request, status: int, result: str) -> Response:
        return Response(
            content=entry.body if request.method == "GET" else b"",
            status_code=status,
            headers={
                **entry.headers,
                # Keep browser caches out of the demo; Redis owns the freshness policy.
                "Cache-Control": "no-store",
                "Content-Length": str(len(entry.body)),
                "X-Cache": result,
                "X-Edge-ID": settings.edge_id,
                "X-Cache-TTL": str(max(0, entry.ttl - entry.age) if result != "BYPASS" else 0),
                "Age": str(entry.age),
            },
        )

    @app.api_route("/assets/{asset_path:path}", methods=["GET", "HEAD"])
    async def asset(asset_path: str, request: Request):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", asset_path) or any(
            part in {"", ".", ".."} for part in asset_path.split("/")
        ):
            raise HTTPException(400, "Invalid asset path")
        if "authorization" in request.headers or "cookie" in request.headers:
            raise HTTPException(400, "This gateway serves public assets without credentials")

        url = httpx.URL(f"{settings.origin_url.rstrip('/')}/assets/{asset_path}").copy_with(
            query=request.scope["query_string"] or None
        )
        key = cache_key(str(url))
        cache_client = request.app.state.cache
        directives = {
            item.strip().split("=", 1)[0].lower()
            for item in request.headers.get("cache-control", "").split(",")
        }
        bypass = bool({"no-cache", "no-store"} & directives)
        if not bypass:
            try:
                raw = await cache_client.get(key)
                if raw:
                    try:
                        entry = Entry.loads(raw)
                        if entry.age < entry.ttl:
                            return respond(entry, request, 200, "HIT")
                    except (ValueError, TypeError, KeyError):
                        logger.warning("Invalid cache entry; fetching origin")
            except RedisError:
                logger.warning("Redis read failed; bypassing cache")
                bypass = True

        try:
            # A reusable HTTPX client retains upstream cookies. Never replay those
            # cookies into a later public asset request, even after an uncached response.
            async with request.app.state.upstream.stream(
                "GET", url, headers={"Cookie": ""}
            ) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    body.extend(chunk)
                    if len(body) > settings.max_asset_bytes:
                        raise HTTPException(502, "Origin asset exceeds configured size limit")
                status = response.status_code
                ttl = public_ttl(response.headers, settings.cache_ttl) if status == 200 else 0
                headers = {
                    name: response.headers[name]
                    for name in SAFE_HEADERS
                    if name in response.headers
                }
        except httpx.TimeoutException as exc:
            raise HTTPException(504, "Origin timed out") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Origin unavailable") from exc

        # Redirects never leave the configured origin or expose an upstream Location.
        if 300 <= status < 400:
            raise HTTPException(502, "Origin redirects are not supported")
        entry = Entry(bytes(body), headers, time.time(), ttl)
        result = "BYPASS"
        if ttl > 0 and not bypass:
            try:
                await cache_client.set(key, entry.dumps(), ex=ttl)
                result = "MISS"
            except RedisError:
                logger.warning("Redis write failed; returning uncached origin response")
        return respond(entry, request, status, result)

    return app


app = create_app()
