"""Exercise the real Compose stack without third-party Python dependencies."""

import argparse
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--edges", nargs="+", default=["edge-1", "edge-2", "edge-3"])
    parser.add_argument("--check-expiry", action="store_true")
    args = parser.parse_args()
    url = f"{args.url}/assets/hello.txt?smoke={uuid4().hex}"
    seen = set()
    origin_request = None
    expected_body = None
    ttl = 0

    for index in range(9):
        with urlopen(url, timeout=10) as response:
            body = response.read()
            headers = response.headers
            expected = "MISS" if index == 0 else "HIT"
            assert headers["X-Cache"] == expected, dict(headers)
            assert response.status == 200
            assert b"Hello from mini-cdn!" in body
            if index == 0:
                origin_request = headers["X-Origin-Request"]
                expected_body = body
                ttl = int(headers["X-Cache-TTL"])
            assert body == expected_body
            assert headers["X-Origin-Request"] == origin_request, "Cache should avoid origin calls"
            seen.add(headers["X-Edge-ID"])
            print(f"{index + 1}: {headers['X-Edge-ID']} {headers['X-Cache']}")
    assert seen == set(args.edges), f"Expected {args.edges}, saw {sorted(seen)}"

    with urlopen(Request(url, method="HEAD"), timeout=10) as response:
        assert response.read() == b""
        assert int(response.headers["Content-Length"]) == len(expected_body)
        assert response.headers["X-Cache"] == "HIT"

    try:
        urlopen(f"{args.url}/assets/missing-{uuid4().hex}.txt", timeout=10)
    except HTTPError as error:
        assert error.code == 404
        assert error.headers["X-Cache"] == "BYPASS"
        error.close()
    else:
        raise AssertionError("Missing files must return 404")

    if args.check_expiry:
        print(f"Waiting {ttl + 1}s to verify Redis expiration...")
        time.sleep(ttl + 1)
        with urlopen(url, timeout=10) as response:
            assert response.headers["X-Cache"] == "MISS"
            assert response.headers["X-Origin-Request"] != origin_request
    print("PASS: load balancing, shared cache, HEAD, 404" + (", TTL" if args.check_expiry else ""))


if __name__ == "__main__":
    main()
