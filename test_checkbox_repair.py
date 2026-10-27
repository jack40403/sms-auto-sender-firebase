"""Pure fake-COM regression tests; no real workbook or SMS operations."""
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from test_readonly_send import load_function

from sms_checkbox_repair import ANCHOR_PREFIX, plan_repair, repair_checkboxes, resolve_checkbox_row


class Cell:
    def __init__(self, sheet, row, column=13):
        self.Parent, self.Row, self.Column = sheet, row, column
        self.Top, self.Left, self.Height, self.Width = 50 + (row - 1) * 16.5, 100., 16.5, 54.
        self.Address = f'${chr(64 + column)}${row}'
        self.CountLarge, self.MergeCells = 1, False
        self.EntireRow = SimpleNamespace(Hidden=False)
        self.EntireColumn = SimpleNamespace(Hidden=False)


class Names:
    def __init__(self, sheet):
        self.sheet, self.items = sheet, {}

    def __iter__(self):
        return iter(self.items.values())

    def __call__(self, key):
        return self.items[key]

    def Add(self, Name, RefersTo, Visible):
        match = re.search(r'\$([A-Z]+)\$([0-9]+)$', RefersTo)
        cell = self.sheet.Cells(int(match[2]), ord(match[1]) - 64)
        item = SimpleNamespace(Name=Name, RefersToRange=cell,
                               Delete=lambda: self.items.pop(Name))
        self.items[Name] = item
        return item


class Box:
    def __init__(self, sheet, name, row, value=-4146, linked='', column=13):
        self.Parent, self.Name, self.row, self.column = sheet, name, row, column
        cell = sheet.Cells(row, 13)
        self.Top, self.Left, self.Width, self.Height = cell.Top + 1.5, cell.Left + 15., 23., 18.75
        self.Value, self.LinkedCell = value, linked
        self.OnAction, self.Caption = '', ''
        self.Placement, self.Locked, self.Visible, self.PrintObject = 2, True, True, True
        self.shape = SimpleNamespace(AlternativeText='', ID=sheet.next_id)
        sheet.next_id += 1

    @property
    def TopLeftCell(self):
        return self.Parent.Cells(self.row, self.column)

    def Delete(self):
        self.Parent.boxes.remove(self)


class Collection:
    def __init__(self, sheet):
        self.sheet = sheet

    @property
    def Count(self):
        return len(self.sheet.boxes)

    def Add(self, left, top, width, height):
        row = int((top - 50) / 16.5) + 1
        box = self.sheet.add(f'new{self.sheet.next_id}', row)
        box.Left, box.Top, box.Width, box.Height = left, top, width, height
        return box


class Sheet:
    def __init__(self):
        self.Name, self.boxes, self.cells, self.next_id = '進階篩選', [], {}, 1
        self.ProtectContents = self.ProtectScenarios = self.ProtectDrawingObjects = False
        self.Parent = SimpleNamespace(Name='test.xlsm', FullName='test.xlsm', ReadOnly=False,
                                      Application=SimpleNamespace(EnableEvents=True, ScreenUpdating=True, Interactive=True))
        self.Parent.Names = Names(self)
        self.Parent.SaveCopyAs = lambda path: Path(path).write_bytes(b'fake workbook backup')
        self.save_count = 0
        self.Parent.Save = self.save

    def save(self):
        self.save_count += 1

    def add(self, name, row, value=-4146, linked='', column=13):
        box = Box(self, name, row, value, linked, column)
        self.boxes.append(box)
        return box

    def Cells(self, row, column):
        return self.cells.setdefault((row, column), Cell(self, row, column))

    def Range(self, address):
        match = re.fullmatch(r'\$?([A-Z])\$?([0-9]+)', address)
        return self.Cells(int(match[2]), ord(match[1]) - 64)

    def CheckBoxes(self, key=None):
        if key is None:
            return Collection(self)
        if isinstance(key, int):
            return self.boxes[key - 1]
        return next(box for box in self.boxes if box.Name == key)

    def Shapes(self, name):
        return self.CheckBoxes(name).shape

    def Protect(self, *options):
        self.ProtectDrawingObjects = options[1]
        self.ProtectContents = options[2]
        self.ProtectScenarios = options[3]

    def Unprotect(self):
        self.ProtectDrawingObjects = False

    def anchor(self, box, row=None):
        row = box.row if row is None else row
        key = '_SMS_M_' + str(box.shape.ID)
        self.Parent.Names.Add(key, f"='進階篩選'!$M${row}", False)
        box.shape.AlternativeText = ANCHOR_PREFIX + key
        return key


class CheckboxRepairTests(unittest.TestCase):
    def setUp(self):
        self.sheet = Sheet()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def repair(self):
        return repair_checkboxes(self.sheet.Parent, self.sheet, self.temp.name)

    def test_existing_anchor_wins_over_drifted_geometry(self):
        box = self.sheet.add('a', 11, value=1)
        self.sheet.anchor(box, 10)
        result = self.repair()
        self.assertEqual(result['count'], 1)
        self.assertEqual(resolve_checkbox_row(self.sheet, box)[0], 10)
        cell = self.sheet.Cells(10, 13)
        self.assertGreaterEqual(box.Top, cell.Top)
        self.assertLessEqual(box.Top + box.Height, cell.Top + cell.Height)
        self.assertEqual(box.Value, 1)
        self.assertEqual(box.LinkedCell, '')
        self.assertTrue(self.sheet.ProtectDrawingObjects)
        self.assertFalse(self.sheet.ProtectContents)

    def test_duplicate_removal_preserves_selected_keeper(self):
        unchecked = self.sheet.add('unchecked', 10)
        checked = self.sheet.add('checked', 10, value=1)
        result = self.repair()
        self.assertEqual(result['removed'], 1)
        self.assertEqual(self.sheet.boxes, [checked])
        self.assertEqual(checked.Value, 1)
        self.assertEqual(checked.LinkedCell, '')

    def test_repeated_repair_is_idempotent(self):
        self.sheet.add('a', 10)
        self.repair()
        names = set(self.sheet.Parent.Names.items)
        result = self.repair()
        self.assertEqual(result['removed'], 0)
        self.assertEqual(len(self.sheet.boxes), 1)
        self.assertEqual(set(self.sheet.Parent.Names.items), names)

    def test_broken_anchor_never_falls_back_to_visual_row(self):
        box = self.sheet.add('a', 10)
        box.shape.AlternativeText = ANCHOR_PREFIX + '_SMS_M_missing'
        with self.assertRaisesRegex(RuntimeError, '失效'):
            self.repair()
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_cross_sheet_link_rejected(self):
        self.sheet.add('a', 10, linked="'其他表'!$Q$10")
        with self.assertRaisesRegex(RuntimeError, '其他工作表'):
            self.repair()

    def test_link_and_anchor_conflict_rejected(self):
        box = self.sheet.add('a', 10, linked='$Q$11')
        self.sheet.anchor(box, 10)
        with self.assertRaisesRegex(RuntimeError, '不同列'):
            self.repair()

    def test_legacy_link_is_not_overwritten(self):
        box = self.sheet.add('a', 10, linked='$Q$10')
        self.repair()
        self.assertEqual(box.LinkedCell, '$Q$10')
        self.assertEqual(box.Value, -4146)

    def test_conflicting_linked_duplicates_are_not_merged(self):
        self.sheet.add('a', 10, linked='$Q$10')
        self.sheet.add('b', 10, value=1, linked='$Q$10')
        with self.assertRaisesRegex(RuntimeError, '狀態衝突'):
            self.repair()
        self.assertEqual(len(self.sheet.boxes), 2)

    def test_failed_unprotect_does_not_touch_controls(self):
        box = self.sheet.add('a', 10)
        self.sheet.anchor(box)
        self.sheet.ProtectDrawingObjects = True
        self.sheet.Unprotect = lambda: (_ for _ in ()).throw(PermissionError('password required'))
        with self.assertRaisesRegex(RuntimeError, '未修改任何方塊'):
            self.repair()
        self.assertTrue(self.sheet.ProtectDrawingObjects)
        self.assertEqual(self.sheet.boxes, [box])

    def test_unlinked_ambiguous_position_rejected_before_any_write(self):
        box = self.sheet.add('a', 10)
        box.Top = self.sheet.Cells(11, 13).Top
        with self.assertRaisesRegex(RuntimeError, '歧義'):
            self.repair()
        self.assertFalse(self.sheet.Parent.Names.items)

    def test_different_macro_duplicates_are_not_deleted(self):
        self.sheet.add('a', 10)
        self.sheet.add('b', 10).OnAction = 'OtherMacro'
        with self.assertRaisesRegex(RuntimeError, '不同功能'):
            self.repair()
        self.assertEqual(len(self.sheet.boxes), 2)

    def test_readonly_rejected_without_backup(self):
        self.sheet.Parent.ReadOnly = True
        with self.assertRaisesRegex(RuntimeError, '唯讀'):
            self.repair()
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_other_columns_are_untouched(self):
        other = self.sheet.add('other', 10, column=12)
        self.sheet.add('a', 10)
        before = vars(other).copy()
        self.repair()
        self.assertEqual(vars(other), before)

    def test_hidden_anchored_row_stays_hidden(self):
        box = self.sheet.add('a', 10)
        self.sheet.anchor(box)
        self.sheet.Cells(10, 13).EntireRow.Hidden = True
        self.repair()
        self.assertFalse(box.Visible)

    def test_failed_save_rolls_back_duplicates_and_restores_app(self):
        a = self.sheet.add('a', 10)
        b = self.sheet.add('b', 10, value=1)
        self.sheet.Parent.Save = lambda: (_ for _ in ()).throw(OSError('disk error'))
        with self.assertRaisesRegex(RuntimeError, '已還原'):
            self.repair()
        self.assertEqual({box.Name for box in self.sheet.boxes}, {'a', 'b'})
        self.assertEqual(self.sheet.CheckBoxes('b').Value, 1)
        self.assertEqual(self.sheet.CheckBoxes('a').Value, -4146)
        self.assertFalse(self.sheet.Parent.Names.items)
        self.assertFalse(self.sheet.ProtectDrawingObjects)
        app = self.sheet.Parent.Application
        self.assertTrue(app.EnableEvents and app.ScreenUpdating and app.Interactive)

    def test_unknown_object_protection_is_preserved(self):
        self.sheet.add('a', 10)
        self.sheet.ProtectDrawingObjects = True
        with self.assertRaisesRegex(RuntimeError, '既有物件保護'):
            self.repair()
        self.assertTrue(self.sheet.ProtectDrawingObjects)


class RepairButtonTests(unittest.TestCase):
    def test_running_send_blocks_repair_before_any_confirmation(self):
        dialogs, repair = Mock(), Mock()
        handler = load_function('sms_modern_gui.py', 'repair_excel_checkboxes', {
            'messagebox': dialogs, 'configure_checkbox_links': repair,
            'sms_licensing': SimpleNamespace(PACKAGED=False),
        }, 'ModernSMSApp')
        handler(SimpleNamespace(is_running=True))
        dialogs.showwarning.assert_called_once()
        dialogs.askyesno.assert_not_called()
        repair.assert_not_called()

    def test_declining_confirmation_does_not_modify_workbook(self):
        dialogs, repair = Mock(), Mock()
        dialogs.askyesno.return_value = False
        handler = load_function('sms_modern_gui.py', 'repair_excel_checkboxes', {
            'messagebox': dialogs, 'configure_checkbox_links': repair,
            'sms_licensing': SimpleNamespace(PACKAGED=False),
        }, 'ModernSMSApp')
        handler(SimpleNamespace(is_running=False))
        repair.assert_not_called()


if __name__ == '__main__':
    unittest.main()
