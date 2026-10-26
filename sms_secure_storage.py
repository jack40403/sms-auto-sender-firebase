"""Protect the installation token for the current Windows user with DPAPI."""

import win32crypt


def is_encryption_available():
    try:
        return unprotect_bytes(protect_bytes(b"sms-license-probe")) == b"sms-license-probe"
    except Exception:
        return False


def protect_bytes(value):
    if not isinstance(value, bytes):
        raise TypeError("value must be bytes")
    return win32crypt.CryptProtectData(value, "SMSAutoSender license", None, None, None, 0)


def unprotect_bytes(value):
    if not isinstance(value, bytes):
        raise TypeError("value must be bytes")
    return win32crypt.CryptUnprotectData(value, None, None, None, 0)[1]


def protect_string(value):
    return protect_bytes(value.encode("utf-8"))


def unprotect_string(value):
    return unprotect_bytes(value).decode("utf-8")

