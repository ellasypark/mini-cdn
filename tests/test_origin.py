import httpx

from mini_cdn.origin import create_app


async def test_origin_assets_and_metadata(tmp_path):
    (tmp_path / "hello.txt").write_text("Hello!")
    app = create_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://origin"
    ) as client:
        assert (await client.get("/healthz")).status_code == 200
        first = await client.get("/assets/hello.txt")
        second = await client.get("/assets/hello.txt")
        assert first.text == "Hello!"
        assert first.headers["cache-control"] == "public, max-age=60"
        assert first.headers["etag"] == second.headers["etag"]
        assert first.headers["x-origin-request"] != second.headers["x-origin-request"]
        head = await client.head("/assets/hello.txt")
        assert head.content == b""
        assert head.headers["content-length"] == "6"
        assert (await client.get("/assets/missing.txt")).status_code == 404
        assert (await client.get("/assets/%2e%2e/secret.txt")).status_code == 404


async def test_symlinks_cannot_escape_asset_directory(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("secret")
    (root / "escape.txt").symlink_to(tmp_path / "secret.txt")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(root)), base_url="http://origin"
    ) as client:
        assert (await client.get("/assets/escape.txt")).status_code == 404
