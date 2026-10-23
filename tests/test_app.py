"""应用异常提示与中断退出流程。"""

import io
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

from rdmap.app import _install_excepthook


class InterruptCloseWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.close_requested = False
        self.allow_close = False

    def closeEvent(self, event):
        self.close_requested = True
        event.accept() if self.allow_close else event.ignore()


class AppExceptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_hook = sys.excepthook
        _install_excepthook()

    def tearDown(self):
        sys.excepthook = self.original_hook

    def test_keyboard_interrupt_uses_normal_close_and_respects_close_rejection(self):
        window = InterruptCloseWindow()
        window.show()
        self.app.processEvents()
        try:
            with patch("rdmap.app.QMessageBox.critical") as critical:
                sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
                self.app.processEvents()
                critical.assert_not_called()
                self.assertTrue(window.close_requested)
                self.assertTrue(window.isVisible(), "中断不能绕过窗口的退出确认或任务清理")
                window.allow_close = True
                sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
                self.app.processEvents()
                self.assertFalse(window.isVisible())
        finally:
            window.allow_close = True
            window.close()

    def test_regular_errors_still_report_traceback(self):
        with patch("rdmap.app.QMessageBox.critical") as critical, patch("sys.stderr", new_callable=io.StringIO) as stderr:
            error = ValueError("test error")
            sys.excepthook(type(error), error, None)
            critical.assert_called_once()
            self.assertIn("ValueError: test error", critical.call_args.args[2])
            self.assertIn("ValueError: test error", stderr.getvalue())

    def test_interrupt_before_application_exists_has_no_error_dialog(self):
        with patch("rdmap.app.QApplication.instance", return_value=None), patch("rdmap.app.QMessageBox.critical") as critical:
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
            critical.assert_not_called()


if __name__ == "__main__":
    unittest.main()
