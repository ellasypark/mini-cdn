import httpx
import pytest

from mini_cdn.cache import Entry, public_ttl
from mini_cdn.config import Settings


@pytest.mark.parametrize(
    "url",
    ["file:///etc", "http://", "http://origin/path", "http://user:pass@origin", "http://a?q=1"],
)
def test_invalid_origin_urls(url):
    with pytest.raises(ValueError):
        Settings(origin_url=url)


@pytest.mark.parametrize("field", ["cache_ttl", "origin_timeout", "max_asset_bytes"])
def test_configuration_requires_positive_values(field):
    with pytest.raises(ValueError):
        Settings(**{field: 0})


def test_configuration_from_environment(monkeypatch):
    monkeypatch.setenv("EDGE_ID", "custom-edge")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "7")
    assert Settings.from_env().edge_id == "custom-edge"
    assert Settings.from_env().cache_ttl == 7


def test_shared_max_age_and_existing_age_are_respected():
    headers = httpx.Headers({"Cache-Control": 'public, max-age=99, s-maxage="8"', "Age": "3"})
    assert public_ttl(headers, 30) == 5
    assert public_ttl(headers, 2) == 2
    assert public_ttl(httpx.Headers({"Cache-Control": "public, max-age=4", "Age": "5"}), 30) == 0


def test_binary_body_roundtrip():
    entry = Entry(b"\x00\xff\x80", {"content-type": "application/octet-stream"}, 1.0, 30)
    assert Entry.loads(entry.dumps()) == entry
