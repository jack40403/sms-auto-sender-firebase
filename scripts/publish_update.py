"""Publish a verified installer to the private Firebase Storage update feed.

Requires Google Application Default Credentials with write access to this
project's Storage bucket. The manifest is uploaded last.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
META = json.loads((ROOT / "app_meta.json").read_text(encoding="utf-8"))
BUCKET = META["storageBucket"]
VERSION = META["version"]
FILE_NAME = f'{META["installerArtifactPrefix"]}-{VERSION}.exe'
RELEASE_DIR = ROOT / "release"


def version_tuple(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"Invalid release version: {value}")
    return tuple(map(int, value.split(".")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if META["firebaseProjectId"] != "sms-auto-sender-firebase":
        raise RuntimeError("Firebase project ID does not match the dedicated SMS project.")
    installer = RELEASE_DIR / FILE_NAME
    if not installer.is_file():
        raise FileNotFoundError(installer)
    size = installer.stat().st_size
    if size <= 0 or size > 500 * 1024 * 1024:
        raise ValueError("Installer size is out of range")
    digest = hashlib.file_digest(installer.open("rb"), "sha256").hexdigest()
    manifest = {
        "version": VERSION,
        "filename": FILE_NAME,
        "size": size,
        "sha256": digest,
        "publishedAt": datetime.now(timezone.utc).isoformat(),
    }
    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return

    from google.cloud import storage

    client = storage.Client(project=META["firebaseProjectId"])
    bucket = client.bucket(BUCKET)
    remote_manifest = bucket.blob("releases/manifest.json")
    if remote_manifest.exists(client):
        previous = json.loads(remote_manifest.download_as_text())
        if version_tuple(VERSION) <= version_tuple(previous["version"]):
            raise RuntimeError("Release version must be newer than the published version")

    artifact = bucket.blob(f"releases/{FILE_NAME}")
    artifact.cache_control = "private,max-age=31536000,immutable"
    artifact.upload_from_filename(str(installer), content_type="application/octet-stream")
    artifact.reload()
    if int(artifact.size or 0) != size:
        raise RuntimeError("Remote installer size does not match; manifest was not published")

    remote_manifest.cache_control = "private,no-store"
    remote_manifest.upload_from_string(json.dumps(manifest), content_type="application/json")
    readback = json.loads(remote_manifest.download_as_text())
    if readback != manifest:
        raise RuntimeError("Published manifest readback mismatch")
    (RELEASE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Published {VERSION} to gs://{BUCKET}/releases and verified manifest readback")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Publication failed: {error}", file=sys.stderr)
        raise SystemExit(1)

