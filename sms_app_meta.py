"""Shared product identity for licensing, updates, and packaging."""

from __future__ import annotations

import json
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
META_FILE = os.path.join(PROJECT_DIR, "app_meta.json")

_DEFAULTS = {
    "name": "sms-auto-sender-firebase",
    "productName": "簡訊自動發送系統",
    "version": "1.0.0",
    "firebaseProjectId": "sms-auto-sender-firebase",
    "functionsRegion": "us-central1",
    "storageBucket": "sms-auto-sender-firebase.firebasestorage.app",
    "installerArtifactPrefix": "SMSAutoSender-Setup",
    "licenseStoreName": "sms-auto-sender-firebase-license",
}


def load_app_meta():
    data = dict(_DEFAULTS)
    if os.path.exists(META_FILE):
        with open(META_FILE, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data.update(loaded)
    return data


APP_META = load_app_meta()
APP_NAME = str(APP_META["name"])
PRODUCT_NAME = str(APP_META["productName"])
APP_VERSION = str(APP_META["version"])
FIREBASE_PROJECT_ID = str(APP_META["firebaseProjectId"])
FUNCTIONS_REGION = str(APP_META["functionsRegion"])
STORAGE_BUCKET = str(APP_META["storageBucket"])
INSTALLER_ARTIFACT_PREFIX = str(APP_META["installerArtifactPrefix"])
LICENSE_STORE_NAME = str(APP_META["licenseStoreName"])
DEFAULT_FUNCTIONS_BASE_URL = f"https://{FUNCTIONS_REGION}-{FIREBASE_PROJECT_ID}.cloudfunctions.net"


def is_packaged_runtime():
    return bool(getattr(sys, "frozen", False))


def resource_root():
    if is_packaged_runtime():
        return os.path.dirname(sys.executable)
    return PROJECT_DIR

