"""Non-destructive M-column checkbox repair; never writes helper-cell values."""
from datetime import datetime
from pathlib import Path
import re

ANCHOR_PREFIX = 'SMS_M_ANCHOR:'
NAME_PREFIX = '_SMS_M_'


def _validate_cell(ws, cell):
    if (str(cell.Parent.Name) != str(ws.Name)
            or str(cell.Parent.Parent.FullName) != str(ws.Parent.FullName)
            or int(cell.CountLarge) != 1 or int(cell.Row) < 2):
        raise RuntimeError('核取方塊連結不屬於目前工作表的單一資料列，已停止。')
    return int(cell.Row)


def _linked_range(ws, address):
    if '!' in address:
        sheet, address = address.rsplit('!', 1)
        sheet = sheet.strip("'").replace("''", "'")
        if sheet != str(ws.Name):
            raise RuntimeError('核取方塊連結到其他工作表，已停止，未重新配對。')
    if not re.fullmatch(r'\$?[A-Za-z]+\$?[0-9]+', address):
        raise RuntimeError('核取方塊連結格式無法確認，已停止。')
    return ws.Range(address)


def resolve_checkbox_row(ws, cb):
    """Honor persistent anchors first; never fall back when an anchor is broken."""
    shape = ws.Shapes(str(cb.Name))
    tag = str(shape.AlternativeText or '')
    linked = str(cb.LinkedCell or '').strip()
    row = None
    if tag.startswith(ANCHOR_PREFIX):
        key = tag[len(ANCHOR_PREFIX):]
        if not key.startswith(NAME_PREFIX):
            raise RuntimeError('核取方塊位置錨點名稱無法確認，已停止。')
        try:
            cell = ws.Parent.Names(key).RefersToRange
            row = _validate_cell(ws, cell)
            if int(cell.Column) != 13:
                raise RuntimeError('位置錨點已離開 M 欄。')
        except Exception as exc:
            raise RuntimeError('核取方塊位置錨點失效，已停止，未改用畫面位置猜測。') from exc
    if linked:
        linked_row = _validate_cell(ws, _linked_range(ws, linked))
        if row is not None and linked_row != row:
            raise RuntimeError('核取方塊錨點與儲存格連結指向不同列，已停止。')
        row = linked_row
    return (row if row is not None else int(cb.TopLeftCell.Row)), linked


def _snapshot(ws, cb):
    shape = ws.Shapes(str(cb.Name))
    return dict(cb=cb, name=str(cb.Name), row=resolve_checkbox_row(ws, cb)[0],
                tag=str(shape.AlternativeText or ''), linked=str(cb.LinkedCell or ''),
                value=int(cb.Value), action=str(cb.OnAction or ''), caption=str(cb.Caption),
                left=float(cb.Left), top=float(cb.Top), width=float(cb.Width), height=float(cb.Height),
                locked=bool(cb.Locked), placement=int(cb.Placement), visible=bool(cb.Visible),
                printed=bool(cb.PrintObject), shape_id=int(shape.ID),
                source_column=int(cb.TopLeftCell.Column))


def plan_repair(ws):
    """Validate the entire operation before creating names, moving or deleting."""
    groups, snapshots = {}, []
    try:
        count = int(ws.CheckBoxes().Count)
        for index in range(1, count + 1):
            cb = ws.CheckBoxes(index)
            name = str(cb.Name)
            tag = str(ws.Shapes(name).AlternativeText or '')
            if int(cb.TopLeftCell.Column) != 13 and not tag.startswith(ANCHOR_PREFIX):
                continue
            item = _snapshot(ws, cb)
            row = item['row']
            cell = ws.Cells(row, 13)
            if row < 2 or bool(cell.MergeCells):
                raise RuntimeError(f'M{row} 是標題或合併儲存格，無法安全定位。')
            if item['value'] not in (1, -4146):
                raise RuntimeError(f'M{row} 的勾選狀態不明，請先人工核對。')
            if not item['tag'].startswith(ANCHOR_PREFIX) and not item['linked']:
                # Legacy controls can be normalized only if their center is
                # unambiguously in the same M cell as their top-left corner.
                center = item['top'] + item['height'] / 2
                if (bool(cell.EntireRow.Hidden) or bool(cell.EntireColumn.Hidden)
                        or item['source_column'] != 13
                        or not float(cell.Top) <= center < float(cell.Top) + float(cell.Height)
                        or item['left'] < float(cell.Left) - .1
                        or item['left'] + item['width'] > float(cell.Left) + float(cell.Width) + .1):
                    raise RuntimeError(f'M{row} 缺少可信錨點且位置有歧義，已停止，請先核對。')
            if item['tag'] and not item['tag'].startswith(ANCHOR_PREFIX):
                raise RuntimeError(f'M{row} 已有其他物件說明，已停止避免覆寫。')
            snapshots.append(item)
            groups.setdefault(row, []).append(item)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError('有核取方塊無法完整讀取，已停止，未修改任何控制項。請重新開啟 Excel 再試。') from exc
    if not snapshots:
        raise RuntimeError('M 欄沒有可修復的核取方塊。')
    keep, remove = [], []
    for row, members in sorted(groups.items()):
        if len({(item['action'], item['caption']) for item in members}) != 1:
            raise RuntimeError(f'M{row} 有不同功能的控制項，不能自動合併。')
        if any(item['linked'] for item in members) and len({item['value'] for item in members}) > 1:
            raise RuntimeError(f'M{row} 的重複連結控制項勾選狀態衝突，已停止。')
        if len(members) > 1 and any(not item['tag'] and not item['linked'] for item in members):
            first = members[0]
            if any(any(abs(item[key] - first[key]) > .5 for key in ('top', 'left', 'width', 'height')) for item in members[1:]):
                raise RuntimeError(f'M{row} 的重疊物件位置不一致，無法確認是否同一對象，已停止。')
        keeper = next((item for item in members if item['value'] == 1), members[0])
        keep.append(keeper)
        remove.extend(item for item in members if item is not keeper)
    return dict(keep=keep, remove=remove, snapshots=snapshots)


def _protect_objects(ws):
    # Use positional COM arguments: late-bound Excel may silently ignore named
    # optional arguments and otherwise apply Contents=True by default.
    ws.Protect('', True, False, False, True, False, False, False,
               False, False, False, False, False, True, True, False)
    if not ws.ProtectDrawingObjects or ws.ProtectContents or ws.ProtectScenarios:
        raise RuntimeError('Excel 未正確套用僅物件保護，已停止。')


def _restore(ws, snapshots, new_names, was_protected):
    if ws.ProtectDrawingObjects:
        ws.Unprotect()
    for item in snapshots:
        try:
            cb = ws.CheckBoxes(item['name'])
            # Access a property now; Excel may return an invalid COM proxy.
            _ = cb.Name
        except Exception:
            cb = ws.CheckBoxes().Add(item['left'], item['top'], item['width'], item['height'])
            cb.Name = item['name']
        cb.Left, cb.Top = item['left'], item['top']
        cb.Width, cb.Height = item['width'], item['height']
        cb.Caption, cb.OnAction = item['caption'], item['action']
        cb.Placement, cb.Locked = item['placement'], item['locked']
        cb.Visible, cb.PrintObject = item['visible'], item['printed']
        ws.Shapes(item['name']).AlternativeText = item['tag']
        # LinkedCell and values were never changed; restore a deleted control
        # with the same logical state if rollback is required.
        if str(cb.LinkedCell or '') != item['linked']:
            cb.LinkedCell = item['linked']
        if int(cb.Value) != item['value']:
            cb.Value = item['value']
    for key in new_names:
        ws.Parent.Names(key).Delete()
    if was_protected:
        _protect_objects(ws)


def repair_checkboxes(workbook, ws, backup_dir):
    if bool(workbook.ReadOnly):
        raise RuntimeError('工作簿為唯讀，不能修復；仍可照常讀取與寄送簡訊。')
    if bool(ws.ProtectContents) or bool(ws.ProtectScenarios):
        raise RuntimeError('工作表另有內容保護，已停止，未變更原有保護。')
    plan = plan_repair(ws)
    was_protected = bool(ws.ProtectDrawingObjects)
    if was_protected and any(not item['tag'].startswith(ANCHOR_PREFIX) for item in plan['snapshots']):
        raise RuntimeError('工作表有既有物件保護，來源無法確認，已停止避免解鎖其他設定。')
    # Detect name conflicts before making any writes.
    existing_names = {str(name.Name).split('!')[-1] for name in workbook.Names}
    for item in plan['keep']:
        key = NAME_PREFIX + str(item['shape_id'])
        if not item['tag'].startswith(ANCHOR_PREFIX) and key in existing_names:
            raise RuntimeError('預定的位置錨點名稱已存在，已停止避免覆寫。')
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    original = Path(str(workbook.Name))
    backup = backup_dir / f'{original.stem}.checkbox-backup-{datetime.now():%Y%m%d-%H%M%S-%f}{original.suffix}'
    workbook.SaveCopyAs(str(backup))
    if not backup.is_file():
        raise RuntimeError('無法確認備份已建立，修復未執行。')
    app = workbook.Application
    old_events, old_screen, old_interactive = app.EnableEvents, app.ScreenUpdating, app.Interactive
    new_names = []
    mutated = False
    try:
        app.EnableEvents = False
        app.ScreenUpdating = False
        app.Interactive = False
        if was_protected:
            ws.Unprotect()
        mutated = True
        # Keep every existing anchor, LinkedCell and checkbox state intact.
        for item in plan['keep']:
            cb = item['cb']
            cell = ws.Cells(item['row'], 13)
            if not item['tag'].startswith(ANCHOR_PREFIX):
                key = NAME_PREFIX + str(item['shape_id'])
                workbook.Names.Add(key, "='" + str(ws.Name).replace("'", "''") + "'!" + str(cell.Address), False)
                new_names.append(key)
                ws.Shapes(item['name']).AlternativeText = ANCHOR_PREFIX + key
            cb.Locked = True
            cb.Placement = 2  # xlMove: traditional checkboxes ignore xlMoveAndSize.
            if cell.EntireRow.Hidden or cell.EntireColumn.Hidden or float(cell.Height) < 8 or float(cell.Width) < 9:
                cb.Visible = False
            else:
                cb.Visible = True
                cb.Height = min(13., float(cell.Height) - 3.)
                cb.Width = min(18., float(cell.Width) - 4.)
                cb.Top = float(cell.Top) + (float(cell.Height) - float(cb.Height)) / 2
                cb.Left = float(cell.Left) + (float(cell.Width) - float(cb.Width)) / 2
                if cb.Top + cb.Height > cell.Top + cell.Height + .1 or cb.Left + cb.Width > cell.Left + cell.Width + .1:
                    raise RuntimeError('方塊尺寸未正確套用，已停止並還原。')
        for item in plan['remove']:
            item['cb'].Delete()
        _protect_objects(ws)
        after = plan_repair(ws)
        if after['remove'] or len(after['keep']) != len(plan['keep']):
            raise RuntimeError('修復後仍有重疊或方塊數量不符。')
        before_checked = {item['row'] for item in plan['snapshots'] if item['value'] == 1}
        if {item['row'] for item in after['snapshots'] if item['value'] == 1} != before_checked:
            raise RuntimeError('修復前後的勾選列不一致。')
        workbook.Save()
        return dict(count=len(plan['keep']), column=13, backup_path=str(backup),
                    removed=len(plan['remove']), protected_objects=True)
    except Exception as exc:
        if not mutated:
            raise RuntimeError(f'無法解除既有物件保護，未修改任何方塊；備份：{backup}') from exc
        try:
            _restore(ws, plan['snapshots'], new_names, was_protected)
        except Exception as rollback_error:
            raise RuntimeError(f'修復中斷且還原未完成；請使用備份：{backup}') from rollback_error
        raise RuntimeError(f'修復中斷，已還原控制項；備份：{backup}') from exc
    finally:
        app.EnableEvents = old_events
        app.ScreenUpdating = old_screen
        app.Interactive = old_interactive
