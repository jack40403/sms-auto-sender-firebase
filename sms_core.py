import re
import sys
import os
import json
import time
import ctypes
from ctypes import wintypes
import win32service
import win32con
import win32gui
import win32ui
import win32com.client
import pythoncom
from datetime import datetime
from sms_checkbox_repair import repair_checkboxes, resolve_checkbox_row

CONFIG_FILE = (
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SMSAutoSender", "sms_config.json")
    if getattr(sys, "frozen", False) else os.path.join(os.path.dirname(__file__), "sms_config.json")
)
DEFAULT_TEMPLATE = "您好！【一安藥局】新泰店【處方領藥通知】：{姓名} 先生/小姐的健保處方箋已可領藥，請攜帶健保卡，感謝您！ 一安藥局服務電話：02-29941618"

def load_template_config():
    """Loads saved template from JSON file or returns default."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("template", DEFAULT_TEMPLATE)
        except Exception as e:
            print(f"Error loading config: {e}")
    return DEFAULT_TEMPLATE

def save_template_config(template_text):
    """Saves current template to JSON file for persistence."""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        data = {"template": template_text.strip()}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False

# Mobile regex helper
def extract_mobiles(raw_phone_str):
    """
    Extracts all 10-digit Taiwan mobile numbers (starting with 09) from raw string.
    Filters out landlines and non-mobile numbers.
    Returns: (list_of_valid_mobiles, list_of_filtered_out_landlines)
    """
    if not raw_phone_str:
        return [], []
    
    text = str(raw_phone_str).strip()
    items = re.split(r'[,/、;\r\n]+', text)
    
    mobiles = []
    landlines = []
    
    for item in items:
        item = item.strip()
        if not item:
            continue
        cleaned = re.sub(r'[\s-]', '', item)
        found_in_item = []
        for m in re.finditer(r'(?:09\d{8}|8869\d{8})', cleaned):
            num = m.group(0)
            if num.startswith('886'):
                num = '0' + num[3:]
            if num not in mobiles:
                mobiles.append(num)
            found_in_item.append(num)
            
        if not found_in_item:
            if re.search(r'\d', item):
                landlines.append(item)
                
    return mobiles, landlines

def switch_to_default_desktop():
    """Switches current thread desktop to WinSta0\\Default if running in a separate desktop."""
    try:
        user32 = ctypes.windll.user32
        hdesk = win32service.OpenDesktop('Default', 0, False, win32con.GENERIC_ALL)
        if hdesk:
            user32.SetThreadDesktop(int(hdesk))
        return hdesk
    except Exception:
        return None

def get_excel_apps():
    """Return every reachable Excel application on the interactive desktop."""
    switch_to_default_desktop()
    hdesk = win32service.OpenDesktop('Default', 0, False, win32con.GENERIC_ALL)
    excel7_hwnds = []
    
    def cb(hwnd, extra):
        if win32gui.GetClassName(hwnd) == 'XLMAIN':
            def child_cb(chwnd, extra2):
                if win32gui.GetClassName(chwnd) == 'EXCEL7':
                    excel7_hwnds.append(chwnd)
                return True
            win32gui.EnumChildWindows(hwnd, child_cb, 0)
        return True
        
    win32gui.EnumDesktopWindows(hdesk, cb, 0)
    if not excel7_hwnds:
        return []
        
    oleacc = ctypes.oledll.oleacc
    OBJID_NATIVEOM = -16
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8)
        ]
    iid = GUID(0x00020400, 0x0000, 0x0000, (wintypes.BYTE * 8)(0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46))
    apps = []
    seen_hwnds = set()
    for hwnd in excel7_hwnds:
        p_acc = ctypes.c_void_p()
        res = oleacc.AccessibleObjectFromWindow(
            hwnd, OBJID_NATIVEOM, ctypes.byref(iid), ctypes.byref(p_acc)
        )
        if res != 0 or not p_acc.value:
            continue
        try:
            disp = pythoncom.ObjectFromAddress(p_acc.value, pythoncom.IID_IDispatch)
            app = win32com.client.Dispatch(disp).Application
            app_hwnd = int(app.Hwnd)
            if app_hwnd not in seen_hwnds:
                seen_hwnds.add(app_hwnd)
                apps.append(app)
        except Exception:
            continue
    return apps


def _is_target_workbook_name(name):
    lowered = str(name).lower()
    return any(k.lower() in lowered for k in ('1002', '藥局', '寄單', '1150919'))


def _find_target_workbook(expected_fullname=None):
    matches = []
    normalized_expected = os.path.normcase(str(expected_fullname)) if expected_fullname else None

    for app in get_excel_apps():
        try:
            for wb in app.Workbooks:
                fullname = str(wb.FullName)
                if normalized_expected and os.path.normcase(fullname) == normalized_expected:
                    return app, wb
                if _is_target_workbook_name(wb.Name):
                    matches.append((app, wb))
        except Exception:
            continue

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = '、'.join(str(wb.Name) for _, wb in matches)
        raise RuntimeError(f"偵測到多個可能的寄單工作簿：{names}。請只保留要操作的版本。")
    return None, None


def get_excel_app():
    """Return the Excel application that owns the pharmacy workbook."""
    app, _ = _find_target_workbook()
    return app


def _find_target_sheet(workbook, expected_name=None):
    if expected_name:
        try:
            return workbook.Worksheets(expected_name)
        except Exception:
            pass

    exact = []
    partial = []
    for sheet in workbook.Worksheets:
        name = str(sheet.Name)
        if name == '進階篩選':
            exact.append(sheet)
        elif '進階' in name or '篩選' in name:
            partial.append(sheet)
    candidates = exact or partial
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise RuntimeError("找到多個名稱相似的篩選工作表，無法安全判斷要讀取哪一張。")
    raise RuntimeError("找不到【進階篩選】工作表，已停止以避免讀取錯誤工作表。")


def get_excel_status():
    """Return target workbook status without mutating Excel."""
    app, wb = _find_target_workbook()
    if not app or not wb:
        return {'connected': False, 'readonly': None, 'name': '', 'fullname': ''}
    return {
        'connected': True,
        'readonly': bool(wb.ReadOnly),
        'name': str(wb.Name),
        'fullname': str(wb.FullName),
    }


def _checkbox_row(cb_obj):
    """Resolve persistent M-column anchors before LinkedCell or geometry."""
    return resolve_checkbox_row(cb_obj.Parent, cb_obj)

def get_checked_patients():
    """
    Connects to Excel, locates the pharmacy workbook and '進階篩選' sheet,
    and returns a sorted list of checked patient dicts.
    """
    excel_app, target_wb = _find_target_workbook()
    if not excel_app or not target_wb:
        raise RuntimeError("找不到已開啟的 Excel 視窗，請先確認【1002藥局寄單系統】已開啟！")
    ws = _find_target_sheet(target_wb)

    try:
        cbs = ws.CheckBoxes()
        total_cbs = cbs.Count
    except Exception as e:
        raise RuntimeError(f"無法讀取工作表核取方塊：{e}")

    checked_items = []
    checked_rows = set()
    unreadable_checkboxes = []

    # 使用索引逐一讀取，避免單一損壞控制項使 COM enumerator 整批中止。
    for checkbox_index in range(1, total_cbs + 1):
        try:
            cb_obj = cbs(checkbox_index)
            cb_val = cb_obj.Value
        except Exception:
            unreadable_checkboxes.append(checkbox_index)
            continue

        # xlOn = 1, 1.0, True, '1'
        if cb_val in (1, 1.0, True, '1'):
            try:
                row, linked_cell = _checkbox_row(cb_obj)
                if row in checked_rows:
                    raise RuntimeError(f"第 {row} 列存在多個已勾選核取方塊，已停止以避免重複發送。")
                name = str(ws.Cells(row, 2).Value or '').strip()
                raw_phone = str(ws.Cells(row, 3).Value or '').strip()
                contact = str(ws.Cells(row, 4).Value or '').strip()
                hospital = str(ws.Cells(row, 6).Value or '').strip()
                d2 = str(ws.Cells(row, 9).Value or '').strip()
                d3 = str(ws.Cells(row, 10).Value or '').strip()

                mobiles, landlines = extract_mobiles(raw_phone)

                if not name:
                    raise RuntimeError(f"第 {row} 列已勾選，但姓名欄是空白，已停止以避免傳錯對象。")

                checked_rows.add(row)
                checked_items.append({
                    'cb_index': checkbox_index,
                    'checkbox_name': str(cb_obj.Name),
                    'linked_cell': linked_cell,
                    'row': row,
                    'name': name,
                    'raw_phone': raw_phone,
                    'mobiles': mobiles,
                    'landlines': landlines,
                    'contact': contact,
                    'hospital': hospital,
                    'date_range2': d2,
                    'date_range3': d3,
                    'workbook_name': str(target_wb.Name),
                    'workbook_fullname': str(target_wb.FullName),
                    'sheet_name': str(ws.Name),
                    'workbook_readonly': bool(target_wb.ReadOnly),
                })
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(f"無法讀取第 {checkbox_index} 個已勾選控制項：{e}") from e

    if unreadable_checkboxes:
        preview = ', '.join(str(index) for index in unreadable_checkboxes[:10])
        raise RuntimeError(
            f"有 {len(unreadable_checkboxes)} 個核取方塊無法讀取（索引：{preview}）。"
            "請先執行『修復核取方塊』，避免漏掉病患。"
        )

    # 4. 依照 Excel 列號 (row) 由小到大排序
    checked_items.sort(key=lambda x: x['row'])
    return checked_items

def uncheck_patient(patient_item):
    """Return True on save, None when read-only skips writes, or False on failure."""
    try:
        _, wb = _find_target_workbook(patient_item.get('workbook_fullname'))
        if not wb:
            return False
        if bool(wb.ReadOnly):
            return None
        ws = _find_target_sheet(wb, patient_item.get('sheet_name'))
        cb_obj = None
        checkbox_name = patient_item.get('checkbox_name')
        if checkbox_name:
            try:
                cb_obj = ws.CheckBoxes(checkbox_name)
            except Exception:
                cb_obj = None
        if cb_obj is None:
            target_row = int(patient_item['row'])
            for index in range(1, ws.CheckBoxes().Count + 1):
                candidate = ws.CheckBoxes(index)
                row, _ = _checkbox_row(candidate)
                if row == target_row:
                    cb_obj = candidate
                    break
        if cb_obj is None:
            return False

        row, linked = _checkbox_row(cb_obj)
        if row != int(patient_item['row']):
            print("核取方塊列號已改變，為避免取消錯列而停止。")
            return False

        cb_obj.Value = -4146  # xlOff
        if linked:
            try:
                address = re.search(r'(\$?[A-Z]+\$?\d+)$', linked.upper()).group(1)
                ws.Range(address).Value = False
            except Exception:
                pass
        wb.Save()
        return True
    except Exception as e:
        print(f"Error unchecking row {patient_item.get('row')}: {e}")
        return False


def configure_checkbox_links(helper_column=17):
    """Compatibility entrypoint: align/deduplicate without resetting LinkedCell.

    helper_column is retained for old callers; no helper column is written.
    """
    _, wb = _find_target_workbook()
    if not wb:
        raise RuntimeError("找不到寄單工作簿。")
    ws = _find_target_sheet(wb)
    backup_dir = os.path.join(os.path.dirname(CONFIG_FILE), 'backup')
    return repair_checkboxes(wb, ws, backup_dir)

def format_sms_message(template, patient_name):
    """
    Substitutes {姓名} or XXX / xxx into template.
    If no placeholder is found, prepends '{姓名} 您好！'.
    """
    if not template:
        template = DEFAULT_TEMPLATE
        
    placeholders = ["{姓名}", "XXX", "xxx", "[姓名]", "【姓名】", "{name}", "{NAME}"]
    for ph in placeholders:
        if ph in template:
            return template.replace(ph, patient_name)
            
    if template.startswith("您好！") or template.startswith("您好!"):
        return f"{patient_name} {template}"
    else:
        return f"{patient_name} 您好！{template}"

def get_moding_chrome_window():
    """Finds and ensures the MoDing SMS window in Chrome is restored and foregrounded."""
    switch_to_default_desktop()
    hwnd = win32gui.FindWindow('Chrome_WidgetWin_1', 'SMS/MMS System - Google Chrome')
    if not hwnd:
        hdesk = win32service.OpenDesktop('Default', 0, False, win32con.GENERIC_ALL)
        hwnds = []
        def cb(h, extra):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h)
                if 'SMS/MMS System' in t or '摩叮' in t:
                    hwnds.append(h)
            return True
        win32gui.EnumDesktopWindows(hdesk, cb, 0)
        if hwnds:
            hwnd = hwnds[0]
            
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        time.sleep(0.3)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    return hwnd


