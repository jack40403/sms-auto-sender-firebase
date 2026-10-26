"""Offline checks for licensing and installer integrity. Never sends SMS."""

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sms_licensing
import sms_updater


class LicenseAndUpdateTests(unittest.TestCase):
    def test_packaged_app_rejects_missing_license(self):
        with patch.object(sms_licensing, "PACKAGED", True), patch.object(
            sms_licensing, "validate_activation_status", return_value={
                "activated": False, "message": "授權已停用",
            }
        ):
            with self.assertRaisesRegex(sms_licensing.LicenseError, "授權已停用"):
                sms_licensing.validate()

    def test_update_manifest_must_name_this_apps_installer(self):
        manifest = {
            "version": "1.0.1", "filename": "Other-Setup-1.0.1.exe",
            "size": 3, "sha256": "a" * 64,
        }
        with patch.object(sms_licensing, "PACKAGED", True), patch.object(
            sms_licensing, "validate", return_value={"success": True}
        ), patch.object(sms_updater, "_get", return_value=io.BytesIO(json.dumps(manifest).encode())):
            with self.assertRaisesRegex(ValueError, "更新資訊"):
                sms_updater.check_update()

    def test_download_rejects_tampered_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {
                "filename": "SMSAutoSender-Setup-1.0.1.exe",
                "size": 3,
                "sha256": hashlib.sha256(b"good").hexdigest(),
            }
            with patch.object(sms_licensing, "STATE_DIR", Path(directory)), patch.object(
                sms_updater, "_get", return_value=io.BytesIO(b"bad")
            ):
                with self.assertRaisesRegex(ValueError, "驗證失敗"):
                    sms_updater.download_update(manifest)
            self.assertFalse(list(Path(directory).rglob("*.exe")))


if __name__ == "__main__":
    unittest.main()

