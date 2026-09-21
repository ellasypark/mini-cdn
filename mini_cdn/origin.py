"""An origin that serves only files inside the bundled assets directory."""

import hashlib
import mimetypes
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response


def create_app(asset_dir: Path | None = None) -> FastAPI:
    root = (asset_dir or Path(__file__).parent / "assets").resolve()
    app = FastAPI(title="Mini CDN origin")

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "service": "origin"}

    @app.api_route("/assets/{asset_path:path}", methods=["GET", "HEAD"])
    async def asset(asset_path: str, request: Request):
        path = (root / asset_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "Asset not found")
        body = path.read_bytes()
        headers = {
            "Cache-Control": "public, max-age=60",
            "ETag": '"' + hashlib.sha256(body).hexdigest() + '"',
            "X-Origin-Request": str(uuid4()),
            "Content-Length": str(len(body)),
        }
        return Response(
            content=body if request.method == "GET" else b"",
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            headers=headers,
        )

    return app


app = create_app()
