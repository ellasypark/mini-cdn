"""Redis stores the body and metadata together with an atomic expiration."""

import base64
import hashlib
import json
import time
from dataclasses import dataclass

from httpx import Headers


def cache_key(url: str) -> str:
    # Include the upstream identity and full query string, but bound key length.
    return "mini-cdn:v1:" + hashlib.sha256(url.encode()).hexdigest()


def public_ttl(headers: Headers, limit: int) -> int:
    """Conservative public-asset policy, deliberately not a general HTTP cache."""
    directives = {}
    for item in headers.get("cache-control", "").lower().split(","):
        name, _, value = item.strip().partition("=")
        directives[name] = value.strip(' "')
    if (
        "public" not in directives
        or {"private", "no-store", "no-cache"} & directives.keys()
        or "set-cookie" in headers
        or "vary" in headers
    ):
        return 0
    try:
        lifetime = int(directives.get("s-maxage", directives.get("max-age", "0")))
        age = int(headers.get("age", "0"))
    except ValueError:
        return 0
    return max(0, min(limit, lifetime - max(0, age)))


@dataclass
class Entry:
    body: bytes
    headers: dict[str, str]
    stored_at: float
    ttl: int

    def dumps(self) -> str:
        return json.dumps(
            {
                "body": base64.b64encode(self.body).decode(),
                "headers": self.headers,
                "stored_at": self.stored_at,
                "ttl": self.ttl,
            }
        )

    @classmethod
    def loads(cls, value: str):
        data = json.loads(value)
        return cls(
            body=base64.b64decode(data["body"], validate=True),
            headers=dict(data["headers"]),
            stored_at=float(data["stored_at"]),
            ttl=int(data["ttl"]),
        )

    @property
    def age(self) -> int:
        return max(0, int(time.time() - self.stored_at))
