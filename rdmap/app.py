"""应用入口。"""

from __future__ import annotations

import sys
import traceback

from PyQt5.QtCore import QCoreApplication, Qt, QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QMessageBox

from .config import APP_NAME, APP_TITLE
from .main_window import MainWindow
from .paths import resource_path
from .theme import theme_manager


def _install_excepthook() -> None:
    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            app = QApplication.instance()
            if app is not None:
                # Qt 回调中收到 Ctrl+C 时，返回事件循环后按正常关闭流程处理，
                # 让主窗口保存设置、确认并停止任务，避免直接退出销毁运行中的线程。
                QTimer.singleShot(0, app.closeAllWindows)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(text)
        try:
            QMessageBox.critical(None, "程序错误", text[-4000:])
        except Exception:
            pass

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    try:
        from PyQt5.QtGui import QGuiApplication

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, ValueError):
        pass

    app = QApplication(argv)
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    theme_manager(app)

    icon_path = resource_path("assets/icon.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    _install_excepthook()
    window = MainWindow()
    window.show()
    return app.exec_()
