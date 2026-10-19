"""Register an already-pushed immutable image as a Harnessbench RLI environment."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=os.getenv("RLI_API_BASE"))
    parser.add_argument(
        "--image", required=True, help="Registry image reference pinned with @sha256:..."
    )
    parser.add_argument("--name", default="harness-bench-fast")
    parser.add_argument("--version", default="0.16.0-rli.1")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()
    if not args.api_base:
        parser.error("--api-base or RLI_API_BASE is required")
    digest = args.image.rpartition("@")[2]
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(c not in "0123456789abcdef" for c in digest[7:])
    ):
        parser.error("--image must be pinned to a sha256 digest")
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")

    payload = {
        "name": args.name,
        "version": args.version,
        "type": "ENV",
        "image": args.image,
        "image_digest": digest,
        "backend_type": "SWE",
        "api_version": "v2",
        "max_concurrency": args.concurrency,
        "resource_profile": {
            "cpu": {"requests": 1000, "limits": 2000},
            "memory": {"requests": 2048, "limits": 4096},
            "ephemeral": {"requests": 10240, "limits": 10240},
        },
        "volumes": [{"path": "/workspace", "size": 2048}],
    }
    context = ssl._create_unverified_context() if args.insecure else ssl.create_default_context()
    url = args.api_base.rstrip("/") + "/v1/registry"
    entry_url = f"{url}/ENV/{urllib.parse.quote(args.name, safe='')}/{urllib.parse.quote(args.version, safe='')}"
    try:
        with urllib.request.urlopen(entry_url, context=context, timeout=60) as response:
            existing = json.load(response)["result"]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    else:
        if all(existing.get(key) == value for key, value in payload.items()):
            print(json.dumps(existing, indent=2))
            return
        raise SystemExit(
            "This RLI environment version already exists with different settings; use a new version"
        )

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, context=context, timeout=60) as response:
        print(response.read().decode())
    with urllib.request.urlopen(entry_url, context=context, timeout=60) as response:
        registered = json.load(response)["result"]
    if not all(registered.get(key) == value for key, value in payload.items()):
        raise SystemExit("RLI registry read-back differs from the requested environment")


if __name__ == "__main__":
    main()
