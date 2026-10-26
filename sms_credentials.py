"""Store MoDing credentials in Windows Credential Manager, never in project files."""

import win32cred


CREDENTIAL_TARGET = "YiAnPharmacy.MoDingSMS"


def save_credentials(username, password):
    if not username or not password:
        raise ValueError("帳號與密碼不可為空")
    credential = {
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": CREDENTIAL_TARGET,
        "UserName": str(username),
        "CredentialBlob": str(password),
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        "Comment": "一安藥局摩叮簡訊自動發送系統",
    }
    win32cred.CredWrite(credential, 0)


def load_credentials():
    try:
        credential = win32cred.CredRead(
            CREDENTIAL_TARGET,
            win32cred.CRED_TYPE_GENERIC,
            0,
        )
    except Exception:
        return None
    blob = credential.get("CredentialBlob", b"")
    if isinstance(blob, bytes):
        password = blob.decode("utf-16-le")
    else:
        password = str(blob)
    username = str(credential.get("UserName") or "")
    return username, password


def credentials_configured():
    credentials = load_credentials()
    return bool(credentials and credentials[0] and credentials[1])

