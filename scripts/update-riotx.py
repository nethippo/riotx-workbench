"""Inspect the official stable release; --apply pins it for a subsequent Docker rebuild."""
import argparse
import hashlib
import json
import re
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com/repos/redis/riotx-dist/releases/latest"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "RIOTX-Workbench", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Download and verify official ZIP, then update build pins; does not restart containers")
    args = parser.parse_args()
    release = json.loads(fetch(API))
    version = release["tag_name"].removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or release.get("draft") or release.get("prerelease"):
        raise SystemExit("Stable semantic version required")
    name = f"riotx-{version}.zip"
    asset = next(a for a in release["assets"] if a["name"] == name)
    base = f"https://github.com/redis/riotx-dist/releases/download/v{version}/"
    if asset["browser_download_url"] != base + name:
        raise SystemExit("Unexpected official asset location")
    digest = asset.get("digest", "").removeprefix("sha256:")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        checksum_asset = next(a for a in release["assets"] if a["name"] == "checksums_sha256.txt")
        if checksum_asset["browser_download_url"] != base + "checksums_sha256.txt":
            raise SystemExit("Unexpected checksum location")
        match = re.search(r"([a-f0-9]{64})\s+\*?" + re.escape(name) + r"$", fetch(checksum_asset["browser_download_url"]).decode(), re.M)
        if not match:
            raise SystemExit("Official SHA256 not available")
        digest = match[1]
    runtime = json.loads((ROOT / "runtime.json").read_text())
    print(json.dumps({"included": runtime["version"], "latest": version, "sha256": digest, "repository": runtime["repository"]}, indent=2))
    if args.apply:
        checksum = hashlib.sha256()
        with urllib.request.urlopen(base + name, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                checksum.update(chunk)
        if checksum.hexdigest() != digest:
            raise SystemExit("Checksum verification failed; build pins unchanged")
        dockerfile = ROOT / "Dockerfile"
        text = dockerfile.read_text()
        text = re.sub(r"ARG RIOTX_VERSION=.*", f"ARG RIOTX_VERSION={version}", text)
        text = re.sub(r"ARG RIOTX_SHA256=.*", f"ARG RIOTX_SHA256={digest}", text)
        text = text.replace("COPY --from=riotx /opt/riotx-" + runtime["version"], "COPY --from=riotx /opt/riotx-" + version)
        runtime.update(version=version, sha256=digest)
        dockerfile.write_text(text)
        (ROOT / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
        print("Build pins updated. Run tests and docker compose up -d --build. Minimum supported stays at the tested baseline.")


if __name__ == "__main__":
    main()
