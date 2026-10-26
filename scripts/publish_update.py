"""Publish a verified installer to the private Firebase Storage update feed."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
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


def firebase_cli_credentials():
    """Use the already signed-in Firebase CLI without persisting an access token."""
    from google.oauth2.credentials import Credentials

    node = os.environ.get("SMS_NODE_EXE") or shutil.which("node")
    appdata = os.environ.get("APPDATA", "")
    firebase_tools = Path(appdata) / "npm" / "node_modules" / "firebase-tools"
    if not node or not (firebase_tools / "lib" / "apiv2.js").is_file():
        raise RuntimeError("Firebase CLI global npm installation was not found")
    script = (
        "const path=process.argv[1];"
        "const auth=require(path+'/lib/auth');"
        "const api=require(path+'/lib/apiv2');"
        "const account=auth.getGlobalDefaultAccount();"
        "const refresh=account?.tokens?.refresh_token;"
        "if(!refresh)process.exit(2);"
        "api.setRefreshToken(refresh);"
        "api.getAccessToken().then(token=>process.stdout.write(token)).catch(()=>process.exit(3));"
    )
    result = subprocess.run(
        [node, "-e", script, str(firebase_tools)],
        capture_output=True,
        check=False,
    )
    token = result.stdout.decode("ascii", errors="ignore").strip()
    if result.returncode or len(token) < 20:
        raise RuntimeError("Firebase CLI login could not provide an access token")
    return Credentials(token=token)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--auth", choices=("adc", "firebase-cli"), default="adc")
    args = parser.parse_args()
    if META["firebaseProjectId"] != "sms-auto-sender-firebase":
        raise RuntimeError("Firebase project ID does not match the dedicated SMS project.")
    installer = RELEASE_DIR / FILE_NAME
    if not installer.is_file():
        raise FileNotFoundError(installer)
    size = installer.stat().st_size
    if size <= 0 or size > 500 * 1024 * 1024:
        raise ValueError("Installer size is out of range")
    with installer.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
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

    credentials = firebase_cli_credentials() if args.auth == "firebase-cli" else None
    client = storage.Client(project=META["firebaseProjectId"], credentials=credentials)
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
