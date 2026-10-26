"""Authenticated Windows installer updates served by the Firebase gateway."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

import sms_licensing


GATEWAY = f"{sms_licensing.FUNCTIONS_BASE_URL}/updateGateway/releases"
MAX_INSTALLER_BYTES = 500 * 1024 * 1024


def _get(name):
    request = urllib.request.Request(f"{GATEWAY}/{name}", headers=sms_licensing.auth_headers())
    return urllib.request.urlopen(request, timeout=30)


def _version(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value or ""):
        raise ValueError("更新版本格式不正確。")
    return tuple(map(int, value.split(".")))


def check_update():
    sms_licensing.validate()
    if not sms_licensing.PACKAGED:
        return None
    with _get("manifest.json") as response:
        manifest = json.load(response)
    version = manifest.get("version")
    filename = manifest.get("filename")
    size = manifest.get("size")
    digest = manifest.get("sha256")
    if not isinstance(version, str) or not re.fullmatch(r"SMSAutoSender-Setup-\d+\.\d+\.\d+\.exe", filename or ""):
        raise ValueError("更新資訊內容不正確。")
    if filename != f"SMSAutoSender-Setup-{version}.exe" or not isinstance(size, int) or size <= 0 or size > MAX_INSTALLER_BYTES:
        raise ValueError("更新檔名稱或大小不正確。")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("更新檔雜湊格式不正確。")
    return manifest if _version(version) > _version(sms_licensing.APP_VERSION) else None


def download_update(manifest):
    filename = manifest["filename"]
    directory = sms_licensing.STATE_DIR / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="download-", suffix=".tmp", dir=directory)
    sha = hashlib.sha256()
    received = 0
    try:
        with os.fdopen(descriptor, "wb") as output, _get(filename) as response:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > manifest["size"]:
                    raise ValueError("更新檔超過宣告大小。")
                sha.update(chunk)
                output.write(chunk)
        if received != manifest["size"] or sha.hexdigest() != manifest["sha256"]:
            raise ValueError("更新檔驗證失敗，請重新下載。")
        destination = directory / filename
        os.replace(temporary, destination)
        return destination
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def launch_installer(path):
    installer = Path(path).resolve()
    if installer.parent != (sms_licensing.STATE_DIR / "updates").resolve():
        raise ValueError("更新檔路徑不正確。")
    subprocess.Popen([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])

