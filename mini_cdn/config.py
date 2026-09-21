"""Configuration shared by the three edge processes."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    edge_id: str = "edge-local"
    origin_url: str = "http://origin:8000"
    redis_url: str = "redis://redis:6379/0"
    cache_ttl: int = 30
    origin_timeout: float = 5.0
    max_asset_bytes: int = 2 * 1024 * 1024

    def __post_init__(self):
        url = urlsplit(self.origin_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError("ORIGIN_URL must be an HTTP(S) origin without a path or credentials")
        if self.cache_ttl < 1 or self.origin_timeout <= 0 or self.max_asset_bytes < 1:
            raise ValueError("TTL, timeout, and maximum asset size must be positive")

    @classmethod
    def from_env(cls):
        return cls(
            edge_id=os.getenv("EDGE_ID", "edge-local"),
            origin_url=os.getenv("ORIGIN_URL", "http://origin:8000"),
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
            cache_ttl=int(os.getenv("CACHE_TTL_SECONDS", "30")),
            origin_timeout=float(os.getenv("ORIGIN_TIMEOUT_SECONDS", "5")),
            max_asset_bytes=int(os.getenv("MAX_ASSET_BYTES", str(2 * 1024 * 1024))),
        )
