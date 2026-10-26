"""Firebase-backed installation licensing for the SMS desktop app."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sms_app_meta import (
    APP_VERSION,
    DEFAULT_FUNCTIONS_BASE_URL,
    LICENSE_STORE_NAME,
    is_packaged_runtime,
)
from sms_secure_storage import (
    is_encryption_available,
    protect_string,
    unprotect_string,
)

FUNCTIONS_ENV = "SMS_FUNCTIONS_BASE_URL"
LICENSE_DIR_ENV = "SMS_LICENSE_DIR"
PACKAGED = is_packaged_runtime()


class LicenseError(Exception):
    """Raised when an activation or validation request cannot complete."""


def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _license_dir():
    override = os.environ.get(LICENSE_DIR_ENV, "").strip()
    if override:
        return override
    appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "SMSAutoSender")


def _license_store_path():
    return os.path.join(_license_dir(), f"{LICENSE_STORE_NAME}.json")


def _read_store():
    path = _license_store_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_store(data):
    path = _license_store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _get_stored_string(key):
    value = _read_store().get(key)
    return value if isinstance(value, str) else ""


def _set_stored(updates):
    data = _read_store()
    data.update(updates)
    _write_store(data)


def functions_base_url():
    override = os.environ.get(FUNCTIONS_ENV, "").strip()
    return (override or DEFAULT_FUNCTIONS_BASE_URL).rstrip("/")


def ensure_installation_id():
    existing = _get_stored_string("installationId")
    if existing:
        return existing
    installation_id = str(uuid.uuid4())
    _set_stored({"installationId": installation_id})
    return installation_id


def _read_license_token():
    encrypted_b64 = _get_stored_string("encryptedLicenseToken")
    if not encrypted_b64 or not is_encryption_available():
        return ""
    try:
        import base64

        return unprotect_string(base64.b64decode(encrypted_b64))
    except Exception:
        return ""


def _save_license_token(token):
    if not is_encryption_available():
        raise LicenseError("Windows 安全儲存目前無法使用，無法安全保存授權。")
    import base64

    encrypted = base64.b64encode(protect_string(token)).decode("ascii")
    _set_stored({"encryptedLicenseToken": encrypted})


def _post_json(pathname, body, extra_headers=None):
    headers = {
        "Content-Type": "application/json",
        "X-App-Version": APP_VERSION,
    }
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        f"{functions_base_url()}/{pathname}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise LicenseError(f"授權伺服器回傳無法辨識的內容（HTTP {status}）。") from exc
        raise LicenseError(data.get("message") or f"授權伺服器暫時無法使用（HTTP {status}）。") from exc
    except urllib.error.URLError as exc:
        raise LicenseError("無法連線授權伺服器，請確認網路或本機模擬器後再試。") from exc

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise LicenseError(f"授權伺服器回傳無法辨識的內容（HTTP {status}）。") from exc

    if status >= 400 or not data.get("success"):
        raise LicenseError(data.get("message") or f"授權伺服器暫時無法使用（HTTP {status}）。")
    return data


def get_activation_status():
    installation_id = ensure_installation_id()
    secure_storage_available = is_encryption_available()
    token = _read_license_token()
    activated = bool(token)
    return {
        "activated": activated,
        "installationId": installation_id,
        "storeName": _get_stored_string("storeName"),
        "appVersion": APP_VERSION,
        "backendUrl": functions_base_url(),
        "secureStorageAvailable": secure_storage_available,
        "validationStatus": (
            "secure-storage-unavailable"
            if not secure_storage_available
            else "local-only"
            if activated
            else "not-activated"
        ),
        "lastValidatedAt": _get_stored_string("lastValidatedAt") or None,
        "message": (
            None
            if secure_storage_available
            else "Windows 安全儲存目前無法使用，請重新登入 Windows 後再試。"
        ),
    }


def validate_activation_status():
    base_status = get_activation_status()
    token = _read_license_token()

    if not base_status["secureStorageAvailable"]:
        return {
            **base_status,
            "activated": False,
            "validationStatus": "secure-storage-unavailable",
            "message": base_status["message"] or "Windows 安全儲存目前無法使用，無法確認授權。",
        }

    if not token:
        return {
            **base_status,
            "activated": False,
            "validationStatus": "not-activated",
        }

    if not is_packaged_runtime():
        return {
            **base_status,
            "activated": True,
            "validationStatus": "local-only",
            "message": "開發模式僅檢查本機授權資料。",
        }

    try:
        result = _post_json(
            "validateActivation",
            {
                "installationId": base_status["installationId"],
                "appVersion": APP_VERSION,
            },
            extra_headers={
                "Authorization": f"Bearer {token}",
                "X-Installation-Id": base_status["installationId"],
            },
        )
        validated_at = _utc_now_iso()
        store_name = result.get("storeName") or base_status["storeName"]
        _set_stored({"storeName": store_name, "lastValidatedAt": validated_at})
        return {
            **base_status,
            "activated": True,
            "storeName": store_name,
            "validationStatus": "valid",
            "lastValidatedAt": validated_at,
            "message": result.get("message") or "授權檢查通過。",
        }
    except LicenseError as exc:
        _set_stored({"lastValidationError": str(exc)})
        return {
            **base_status,
            "activated": False,
            "validationStatus": "invalid",
            "message": f"啟動授權檢查失敗：{exc}",
        }


def request_activation(store_name_input):
    store_name = str(store_name_input or "").strip()
    if len(store_name) < 2 or len(store_name) > 80:
        raise LicenseError("請輸入 2 到 80 個字的藥局名稱。")
    installation_id = ensure_installation_id()
    result = _post_json(
        "requestActivation",
        {
            "installationId": installation_id,
            "storeName": store_name,
            "appVersion": APP_VERSION,
        },
    )
    _set_stored({"pendingStoreName": store_name})
    return {
        "success": True,
        "message": result.get("message") or "驗證碼已寄到管理者信箱。",
        "expiresAt": result.get("expiresAt"),
        "developmentCode": result.get("developmentCode"),
    }


def verify_activation(code_input):
    code = "".join(ch for ch in str(code_input or "") if ch.isdigit())
    if len(code) != 6:
        raise LicenseError("請輸入六位數驗證碼。")
    installation_id = ensure_installation_id()
    store_name = _get_stored_string("pendingStoreName")
    result = _post_json(
        "verifyActivation",
        {
            "installationId": installation_id,
            "code": code,
            "appVersion": APP_VERSION,
        },
    )
    token = result.get("licenseToken")
    if not token:
        raise LicenseError("授權伺服器沒有回傳授權資料，請重新申請驗證碼。")
    _save_license_token(token)
    activated_at = _utc_now_iso()
    _set_stored(
        {
            "storeName": store_name,
            "pendingStoreName": "",
            "activatedAt": activated_at,
            "lastValidatedAt": activated_at,
        }
    )
    status = get_activation_status()
    status.update(
        {
            "activated": True,
            "validationStatus": "valid",
            "lastValidatedAt": activated_at,
            "message": result.get("message") or "安裝授權完成。",
        }
    )
    return status


def verify_activation_password(password_input, store_name_input):
    password = str(password_input or "")
    store_name = str(store_name_input or "").strip()
    if len(password) < 8 or len(password) > 128:
        raise LicenseError("管理密碼長度必須介於 8 到 128 個字元。")
    if len(store_name) < 2 or len(store_name) > 80:
        raise LicenseError("請輸入 2 到 80 個字的藥局名稱。")
    installation_id = ensure_installation_id()
    result = _post_json(
        "verifyActivationPassword",
        {
            "installationId": installation_id,
            "storeName": store_name,
            "password": password,
            "appVersion": APP_VERSION,
        },
    )
    token = result.get("licenseToken")
    if not token:
        raise LicenseError("授權伺服器沒有回傳授權資料，請稍後再試。")
    _save_license_token(token)
    activated_at = _utc_now_iso()
    _set_stored(
        {
            "storeName": store_name,
            "pendingStoreName": "",
            "activatedAt": activated_at,
            "lastValidatedAt": activated_at,
        }
    )
    status = get_activation_status()
    status.update(
        {
            "activated": True,
            "validationStatus": "valid",
            "lastValidatedAt": activated_at,
            "message": result.get("message") or "分店管理密碼驗證成功，安裝授權完成。",
        }
    )
    return status


def get_update_authentication_headers():
    installation_id = ensure_installation_id()
    token = _read_license_token()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "X-Installation-Id": installation_id,
        "X-App-Version": APP_VERSION,
    }


def get_update_feed_url():
    return f"{functions_base_url()}/updateGateway/releases"


def clear_activation_for_development():
    if is_packaged_runtime():
        raise LicenseError("正式版本不允許從程式內清除授權。")
    _set_stored(
        {
            "encryptedLicenseToken": "",
            "storeName": "",
            "pendingStoreName": "",
            "lastValidatedAt": "",
            "lastValidationError": "",
        }
    )
    return get_activation_status()


def require_activated_for_protected_action(status=None):
    current = status if status is not None else validate_activation_status()
    if not current.get("activated"):
        raise LicenseError(current.get("message") or "請先完成啟動授權檢查。")
    return current


# Small client API used by the Tk UI and updater.
STATE_DIR = Path(_license_dir())
FUNCTIONS_BASE_URL = functions_base_url()


def installation_id():
    return ensure_installation_id()


def auth_headers():
    headers = get_update_authentication_headers()
    if not headers:
        raise LicenseError("尚未完成安裝授權。")
    return headers


def validate():
    if not PACKAGED:
        return {"success": True, "message": "原始碼開發模式"}
    status = require_activated_for_protected_action()
    return {"success": True, **status}


def request_code(store_name):
    return request_activation(store_name)


def verify_code(code):
    return verify_activation(code)


def verify_password(store_name, password):
    return verify_activation_password(password, store_name)


def stored_store_name():
    return _get_stored_string("storeName")

