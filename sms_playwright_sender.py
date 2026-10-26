"""Direct MoDing browser automation without a Chrome extension.

The app launches a dedicated Chrome profile with a loopback-only DevTools port.
Playwright connects to that window for each operation, so browser objects are
never shared across Tk/worker threads.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime

from sms_credentials import load_credentials


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = (
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SMSAutoSender")
    if getattr(sys, "frozen", False) else PROJECT_DIR
)
PROFILE_DIR = os.path.join(DATA_DIR, ".moding_chrome_profile")
SEND_LOG_FILE = os.path.join(DATA_DIR, "sms_send_log.jsonl")
DEBUG_HOST = "127.0.0.1"
DEBUG_PORT = 9223
DEBUG_URL = f"http://{DEBUG_HOST}:{DEBUG_PORT}"
MODING_URL = "https://sms.moding.com.tw/"
EXPECTED_ACCOUNT_NAME = "一安藥局新泰店"
# User confirmed that this account's different display name is expected.
ACCEPTED_ACCOUNT_NAMES = frozenset((EXPECTED_ACCOUNT_NAME, "一安藥局新杏店"))
RECIPIENT_SELECTOR = 'textarea[name^="destinationText"]:visible'
CONTENT_SELECTOR = 'textarea[name^="msgText"]:visible'


def format_moding_phone(phone_str):
    """Format 09xxxxxxxx as the value expected by the MoDing recipient box."""
    p = re.sub(r"\D", "", str(phone_str))
    if p.startswith("09") and len(p) == 10:
        return f"886{p[1:]};"
    if p.startswith("8869") and len(p) == 12:
        return f"{p};"
    raise ValueError(f"不支援的手機格式：{phone_str}")


def _find_chrome_executable():
    candidates = [
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    return next((path for path in candidates if path and os.path.isfile(path)), None)


def is_browser_ready():
    """Return whether the dedicated Chrome DevTools endpoint is reachable."""
    try:
        with urllib.request.urlopen(f"{DEBUG_URL}/json/version", timeout=0.8) as response:
            data = json.loads(response.read().decode("utf-8"))
            return bool(data.get("webSocketDebuggerUrl"))
    except Exception:
        return False


def start_moding_browser(log_func=print):
    """Launch the dedicated persistent Chrome profile used for MoDing."""
    if is_browser_ready():
        return True, "摩叮專用瀏覽器已開啟"

    chrome_path = _find_chrome_executable()
    if not chrome_path:
        return False, "找不到 Google Chrome 執行檔"

    os.makedirs(PROFILE_DIR, exist_ok=True)
    args = [
        chrome_path,
        f"--remote-debugging-address={DEBUG_HOST}",
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        MODING_URL,
    ]
    try:
        subprocess.Popen(args, close_fds=True)
    except Exception as exc:
        return False, f"無法啟動摩叮專用 Chrome：{exc}"

    for _ in range(30):
        if is_browser_ready():
            log_func("已開啟摩叮專用 Chrome；首次使用請在該視窗手動登入。")
            return True, "已開啟摩叮專用 Chrome"
        time.sleep(0.25)
    return False, "Chrome 已啟動，但無法建立本機自動化連線"


def _iter_frames(page):
    yield page.main_frame
    for frame in page.frames:
        if frame != page.main_frame:
            yield frame


def _find_sms_frame(page):
    """Find one unambiguous frame containing the named SMS fields."""
    matches = []
    for frame in _iter_frames(page):
        try:
            recipients = frame.locator(RECIPIENT_SELECTOR).count()
            contents = frame.locator(CONTENT_SELECTOR).count()
        except Exception:
            continue
        if recipients > 1 or contents > 1:
            raise RuntimeError("找到多個簡訊輸入欄位，請只保留一個簡訊發送面板。")
        if recipients == 1 and contents == 1:
            matches.append(frame)
    if len(matches) > 1:
        raise RuntimeError("找到多個簡訊表單，無法判定要填入哪一個。")
    return matches[0] if matches else None


def _wait_for_sms_frame(page, stop_check_func=None, timeout_seconds=15):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if stop_check_func and stop_check_func():
            raise RuntimeError("已緊急停止")
        frame = _find_sms_frame(page)
        if frame is not None:
            recipient = frame.locator(RECIPIENT_SELECTOR)
            content = frame.locator(CONTENT_SELECTOR)
            if recipient.is_editable() and content.is_editable():
                return frame
        page.wait_for_timeout(100)
    raise RuntimeError("等待簡訊欄位逾時；請在專用 Chrome 開啟【簡訊發送】並確認沒有遮罩或彈窗。")


def _fill_sms_fields(frame, destination, message_text, stop_check_func=None):
    recipient = frame.locator(RECIPIENT_SELECTOR)
    content = frame.locator(CONTENT_SELECTOR)
    for field, value in ((recipient, destination), (content, message_text)):
        if stop_check_func and stop_check_func():
            raise RuntimeError("已緊急停止")
        field.fill(value, timeout=5000)
        field.blur(timeout=5000)
    if recipient.input_value().strip() != destination or content.input_value() != message_text:
        raise RuntimeError("網頁欄位未正確接受資料，已停止避免錯誤發送")


def _click_exact_text(frame, labels):
    for label in labels:
        pattern = re.compile(rf"^\s*{re.escape(label)}\s*$")
        try:
            locator = frame.get_by_text(pattern)
            count = locator.count()
        except Exception:
            continue
        for index in range(min(count, 10)):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    candidate.click(timeout=2500)
                    return True
            except Exception:
                continue
    return False


def _find_moding_page(browser):
    pages = [page for context in browser.contexts for page in context.pages]
    candidates = [page for page in pages if urlsplit(page.url or '').hostname == 'sms.moding.com.tw']
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        with_form = [page for page in candidates if _find_sms_frame(page) is not None]
        if len(with_form) == 1:
            return with_form[0]
        raise RuntimeError("偵測到多個摩叮分頁，請只保留要操作的簡訊發送分頁。")
    if pages:
        page = pages[0]
        page.goto(MODING_URL, wait_until="domcontentloaded", timeout=20000)
        return page
    if not browser.contexts:
        raise RuntimeError("找不到可用的 Chrome 瀏覽器內容")
    page = browser.contexts[0].new_page()
    page.goto(MODING_URL, wait_until="domcontentloaded", timeout=20000)
    return page


def _login_if_needed(page):
    password_box = page.locator("input[name='password']")
    if not ("moding.com.tw" in (page.url or "") and password_box.count()):
        return True, ""

    credentials = load_credentials()
    if not credentials:
        return False, "尚未設定摩叮登入認證"
    username, password = credentials
    page.locator("input[name='username']").fill(username)
    password_box.fill(password)
    page.locator("input[name='btnSumit']").click()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1000)
    if page.locator("input[name='password']").count():
        return False, "摩叮登入失敗，請確認已儲存的帳號密碼"
    return True, ""


def _validate_account_and_terms(page):
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
    except Exception as exc:
        return False, f"無法讀取摩叮帳戶資訊：{exc}"

    account_match = re.search(r"全名\s*[:：]\s*([^\t\r\n\u00a0]+)", body_text)
    if not account_match:
        return False, "無法確認摩叮登入帳戶名稱，請確認登入已完成。"
    actual_name = account_match.group(1).strip()
    if actual_name not in ACCEPTED_ACCOUNT_NAMES:
        return False, f"摩叮帳戶【{actual_name}】不在已確認的帳戶名稱清單，已停止。"

    terms_text = '享用服務及接受"使用協議條款"'
    try:
        terms = page.get_by_text(re.compile(re.escape(terms_text)))
        for index in range(min(terms.count(), 5)):
            if terms.nth(index).is_visible():
                return False, "摩叮正在顯示使用協議條款；請在專用 Chrome 閱讀並手動選擇是否接受"
    except Exception:
        pass
    return True, ""


_SEND_ERROR = re.compile(
    r'失敗|錯誤|不足|無效|無法|不能|未填|請輸入|請填|超過|未成功|不成功|'
    r'\berror\b|\bfailed\b|\binvalid\b|\binsufficient\b', re.I
)
MODAL_SELECTOR = '[role="dialog"]:visible, .x-window:visible, .x-message-box:visible'


def _find_send_button(page):
    """Locate before clicking; never retry a send click with uncertain outcome."""
    for frame in _iter_frames(page):
        for locator in (frame.get_by_role('button', name='發送', exact=True),
                        frame.get_by_text('發送', exact=True)):
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible() and candidate.is_enabled():
                    return candidate
    return None


def _confirm_with_enter(modal):
    """Focus the confirmation button before Enter so it cannot resend the form."""
    buttons = modal.get_by_role('button', name=re.compile(r'^\s*(確\s*定|OK|是|Yes)\s*$', re.I))
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if button.is_visible() and button.is_enabled():
            button.focus(timeout=2000)
            button.press('Enter', timeout=2500)
            return True
    return False


def _send_and_confirm(page, stop_check_func=None, timeout_seconds=15):
    """Acknowledge this send's dialog and return once the form is ready again.

    MoDing provides no additional success marker after OK (user-confirmed flow).
    'ok' means the send/acknowledgement flow completed, not handset delivery.
    """
    if stop_check_func and stop_check_func():
        return 'error', '已緊急停止，尚未操作發送'
    # An already-open dialog cannot be attributed to this patient.
    for frame in _iter_frames(page):
        if frame.locator(MODAL_SELECTOR).count():
            return 'error', '摩叮已有未關閉的對話框，請先處理後再開始發送'
    button = _find_send_button(page)
    if button is None:
        return 'error', '找不到可點擊的發送按鈕'

    state = {'acknowledged': 0, 'error': False, 'stopped': False}

    def handle_dialog(dialog):
        if stop_check_func and stop_check_func():
            state['stopped'] = True
            dialog.dismiss()
        elif _SEND_ERROR.search(dialog.message) or dialog.type == 'prompt':
            state['error'] = True
            dialog.dismiss()
        else:
            # Native browser dialogs cannot receive page keyboard events.
            # accept() is their equivalent of pressing Enter/OK.
            dialog.accept()
            state['acknowledged'] += 1

    page.on('dialog', handle_dialog)
    try:
        # Click once only. A timeout may occur after the request was submitted.
        button.click(timeout=5000)
        deadline = time.monotonic() + timeout_seconds
        ready_since = None
        while time.monotonic() < deadline:
            if state['error']:
                return 'unknown', '摩叮顯示錯誤提示，未列為完成；請核對發送紀錄後再重試'
            if state['stopped'] or (stop_check_func and stop_check_func()):
                return 'unknown', '發送流程已中止；請核對本筆發送紀錄，勿直接重送'

            pending_modal = False
            for frame in _iter_frames(page):
                modals = frame.locator(MODAL_SELECTOR)
                for index in range(modals.count()):
                    modal = modals.nth(index)
                    if not modal.is_visible():
                        continue
                    pending_modal = True
                    if _SEND_ERROR.search(modal.inner_text(timeout=1000)):
                        return 'unknown', '摩叮顯示錯誤提示，未列為完成；請核對發送紀錄後再重試'
                    # Only acknowledge a button inside this dialog, never an
                    # unrelated OK/Yes button elsewhere on the page.
                    if _confirm_with_enter(modal):
                        state['acknowledged'] += 1
                        modal.wait_for(state='hidden', timeout=5000)

            form = _find_sms_frame(page)
            ready = (not pending_modal and form is not None
                     and form.locator(RECIPIENT_SELECTOR).is_editable()
                     and form.locator(CONTENT_SELECTOR).is_editable()
                     and not any(frame.locator('.x-mask:visible, .x-mask-msg:visible, .x-el-mask:visible').count()
                                 for frame in _iter_frames(page)))
            if state['acknowledged'] and ready:
                if ready_since is None:
                    ready_since = time.monotonic()
                elif time.monotonic() - ready_since >= 0.4:
                    return 'ok', '已操作發送並以 Enter 確認，繼續下一位（非手機送達回報）'
            else:
                ready_since = None
            page.wait_for_timeout(100)
        return 'unknown', '已操作發送，但未完成確定或表單尚未就緒；請核對本筆紀錄，勿直接重送'
    except Exception:
        return 'unknown', '發送／確定流程發生異常；請核對本筆紀錄，勿直接重送'
    finally:
        page.remove_listener('dialog', handle_dialog)


def _append_send_log(mobiles, message_text, status, detail, dry_run):
    task_fingerprint = hashlib.sha256(
        ("|".join(sorted(str(phone) for phone in mobiles)) + "\n" + message_text).encode("utf-8")
    ).hexdigest()
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "dry_run" if dry_run else "live",
        "phones": [f"******{str(phone)[-4:]}" for phone in mobiles],
        "message_sha256": hashlib.sha256(message_text.encode("utf-8")).hexdigest(),
        "task_fingerprint": task_fingerprint,
        "status": status,
        "detail": detail,
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SEND_LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def find_recent_live_attempt(mobiles, message_text, max_age_hours=24):
    """Return a recent live success/unknown record for duplicate-send protection."""
    if not os.path.exists(SEND_LOG_FILE):
        return None
    fingerprint = hashlib.sha256(
        ("|".join(sorted(str(phone) for phone in mobiles)) + "\n" + message_text).encode("utf-8")
    ).hexdigest()
    cutoff = time.time() - (max_age_hours * 3600)
    try:
        with open(SEND_LOG_FILE, "r", encoding="utf-8") as handle:
            lines = handle.readlines()[-500:]
        for line in reversed(lines):
            try:
                record = json.loads(line)
                timestamp = datetime.fromisoformat(record["timestamp"]).timestamp()
            except Exception:
                continue
            if timestamp < cutoff:
                continue
            if (
                record.get("mode") == "live"
                and record.get("task_fingerprint") == fingerprint
                and record.get("status") in ("ok", "unknown")
            ):
                return record
    except Exception:
        return None
    return None


def fill_moding_and_send(mobiles, message_text, dry_run=True, stop_check_func=None, log_func=print):
    """Fill and optionally send one MoDing SMS task using Playwright over CDP."""
    if stop_check_func and stop_check_func():
        return False, "已緊急停止"

    try:
        destination = "".join(format_moding_phone(phone) for phone in mobiles)
    except ValueError as exc:
        return False, str(exc)

    ready, reason = start_moding_browser(log_func)
    if not ready:
        return False, reason

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "尚未安裝 Playwright，請重新執行安裝程序"

    playwright = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.connect_over_cdp(DEBUG_URL, timeout=10000)
        page = _find_moding_page(browser)
        page.bring_to_front()
        page.wait_for_load_state("domcontentloaded", timeout=15000)

        logged_in, login_error = _login_if_needed(page)
        if not logged_in:
            return False, login_error
        if "sms.moding.com.tw" not in (page.url or ""):
            page.goto(MODING_URL, wait_until="domcontentloaded", timeout=20000)

        if "sms.moding.com.tw" not in (page.url or ""):
            if "moding.com.tw" in (page.url or "") and page.locator("input[name='password']").count():
                return False, "摩叮登入未完成"
            return False, "摩叮專用瀏覽器尚未進入正確網站"

        preflight_ok, preflight_error = _validate_account_and_terms(page)
        if not preflight_ok:
            _append_send_log(mobiles, message_text, "error", preflight_error, dry_run)
            return False, preflight_error

        frame = _find_sms_frame(page)
        if frame is None:
            for current_frame in _iter_frames(page):
                if _click_exact_text(current_frame, ("簡訊發送",)):
                    break
        frame = _wait_for_sms_frame(page, stop_check_func)
        _fill_sms_fields(frame, destination, message_text, stop_check_func)

        if dry_run:
            detail = "模擬完成：已填入門號與內容，未點擊發送"
            _append_send_log(mobiles, message_text, "ok", detail, True)
            log_func(f"  [模擬測試] {detail}")
            return True, detail

        if stop_check_func and stop_check_func():
            return False, "已緊急停止"

        status, detail = _send_and_confirm(page, stop_check_func)
        _append_send_log(mobiles, message_text, status, detail, False)
        log_func(f"  [發送流程{'完成' if status == 'ok' else '未完成'}] {detail}")
        return status == 'ok', detail
    except Exception as exc:
        detail = f"瀏覽器自動化失敗：{exc}"
        _append_send_log(mobiles, message_text, "error", detail, dry_run)
        return False, detail
    finally:
        # Stopping Playwright disconnects from CDP; it must not close the
        # persistent browser because the next patient reuses the same login.
        try:
            if playwright:
                playwright.stop()
        except Exception:
            pass


