"""Isolated regression checks; never connects to Excel or sends SMS."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def load_function(filename, name, namespace, class_name=None):
    tree = ast.parse(Path(__file__).with_name(filename).read_text(encoding='utf-8-sig'))
    body = tree.body
    if class_name:
        body = next(node for node in body if isinstance(node, ast.ClassDef) and node.name == class_name).body
    function = next(node for node in body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), filename, 'exec'), namespace)
    return namespace[name]


class ReadonlySendTests(unittest.TestCase):
    def test_scan_failure_clears_cached_patients(self):
        scan = load_function('sms_modern_gui.py', 'scan_excel', {
            'get_checked_patients': Mock(side_effect=RuntimeError('scan failed')),
            'sms_licensing': SimpleNamespace(PACKAGED=False),
        }, 'ModernSMSApp')
        tree = Mock()
        tree.get_children.return_value = []
        app = SimpleNamespace(
            checked_patients=[{'row': 2}], log=Mock(), lbl_stats_checked=Mock(),
            tree=tree, update_idletasks=Mock(), _on_scan_error=Mock(),
        )
        self.assertFalse(scan(app))
        self.assertEqual(app.checked_patients, [])

    def test_start_stops_immediately_after_failed_scan(self):
        start = load_function('sms_modern_gui.py', 'start_process', {
            'sms_licensing': SimpleNamespace(PACKAGED=False),
        }, 'ModernSMSApp')
        app = SimpleNamespace(is_running=False, scan_excel=lambda: False)
        start(app)  # No template reads, dialogs or worker access may follow.

    def test_readonly_does_not_touch_sheet_or_save(self):
        workbook = Mock(ReadOnly=True)
        uncheck = load_function('sms_core.py', 'uncheck_patient', {
            '_find_target_workbook': lambda path: (None, workbook),
            '_find_target_sheet': Mock(side_effect=AssertionError('must not access worksheet')),
        })
        self.assertIsNone(uncheck({'row': 2}))
        workbook.Save.assert_not_called()

    def test_editable_workbook_still_unchecks_and_saves(self):
        workbook = Mock(ReadOnly=False)
        checkbox = SimpleNamespace(Value=1)
        sheet = Mock()
        sheet.CheckBoxes.return_value = checkbox
        uncheck = load_function('sms_core.py', 'uncheck_patient', {
            '_find_target_workbook': lambda path: (None, workbook),
            '_find_target_sheet': lambda *args: sheet,
            '_checkbox_row': lambda cb: (2, ''),
        })
        self.assertTrue(uncheck({'row': 2, 'checkbox_name': 'test'}))
        self.assertEqual(checkbox.Value, -4146)
        workbook.Save.assert_called_once_with()

    def run_worker(self, *, dry=False, send_ok=True, uncheck_result=None):
        uncheck = Mock(return_value=uncheck_result)
        send = Mock(return_value=(send_ok, 'test result'))
        worker = load_function('sms_modern_gui.py', '_process_worker_inner', {
            'uncheck_patient': uncheck,
            'fill_moding_and_send': send,
            'format_sms_message': lambda *args: 'test message',
            'time': SimpleNamespace(sleep=lambda seconds: None),
        }, 'ModernSMSApp')
        app = SimpleNamespace(
            stop_requested=False, log=Mock(), set_progress=Mock(),
            after=lambda delay, callback: callback(), _finish_process=Mock(),
        )
        worker(app, 'template', dry, [{'row': 2, 'name': 'test', 'mobiles': ['0900000000']}])
        return app, uncheck

    def test_readonly_success_is_counted_and_notified(self):
        app, uncheck = self.run_worker()
        uncheck.assert_called_once()
        app._finish_process.assert_called_once_with(False, 1, 1, 1)
        self.assertTrue(any('唯讀' in call.args[0] and '略過' in call.args[0] for call in app.log.call_args_list))

    def test_write_failure_is_not_treated_as_readonly(self):
        app, _ = self.run_worker(uncheck_result=False)
        app._finish_process.assert_called_once_with(False, 1, 1, 0)
        self.assertTrue(any('無法取消勾選' in call.args[0] for call in app.log.call_args_list))

    def test_dry_run_does_not_uncheck(self):
        app, uncheck = self.run_worker(dry=True)
        uncheck.assert_not_called()
        app._finish_process.assert_called_once_with(False, 1, 1, 0)

    def test_failed_send_does_not_uncheck(self):
        app, uncheck = self.run_worker(send_ok=False)
        uncheck.assert_not_called()
        app._finish_process.assert_called_once_with(False, 0, 1, 0)

    def test_finish_notice_includes_readonly_even_when_stopped(self):
        dialogs = Mock()
        finish = load_function('sms_modern_gui.py', '_finish_process', {
            'messagebox': dialogs,
        }, 'ModernSMSApp')
        for stopped in (False, True):
            app = SimpleNamespace(btn_start=Mock(), after=Mock(), scan_excel=Mock())
            finish(app, stopped, 1, 2, 1)
            dialog = dialogs.showwarning if stopped else dialogs.showinfo
            self.assertIn('唯讀', dialog.call_args.args[1])
            self.assertIn('保留勾選', dialog.call_args.args[1])


if __name__ == '__main__':
    unittest.main()

