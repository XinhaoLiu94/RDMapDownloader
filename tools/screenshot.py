"""离屏渲染界面截图，用于开发自检与文档配图。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 平台插件的离屏模式没有字体数据库，会导致文字不渲染；
# 这里用真实平台 + WA_DontShowOnScreen 渲染到内存。
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.setdefault("RDMAP_CACHE_DIR", str(ROOT / ".test_cache" / "screenshots-v2"))
os.environ.setdefault("RDMAP_TASK_DIR", str(ROOT / ".test_cache" / "screenshot-tasks"))

from PyQt5.QtCore import QSettings, Qt, QTimer
from PyQt5.QtWidgets import QApplication

from rdmap.config import APP_NAME
from rdmap.main_window import MainWindow
from rdmap.paths import resource_path
from rdmap.theme import apply_theme
from rdmap.task_manager import TaskManagerDialog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=str(resource_path("docs/screenshot.png")))
    parser.add_argument("--source", default="amap")
    parser.add_argument("--zoom", type=int, default=10)
    parser.add_argument("--width", type=int, default=1480)
    parser.add_argument("--height", type=int, default=940)
    parser.add_argument("--tab", type=int, default=0)
    parser.add_argument("--theme", choices=("light", "dark", "system"), default="light")
    parser.add_argument("--tasks", action="store_true", help="截取任务管理窗口")
    parser.add_argument("--menu", choices=("file", "tools", "help"), help="同时截取展开的菜单")
    parser.add_argument("--levels", default="", help="显示多级连续下载设置，例如 10-12,14")
    parser.add_argument("--coordinate-format", choices=("decimal", "dms"), default="decimal")
    parser.add_argument("--region", default="", help="选择区域预设，例如 china_mainland、us_contiguous")
    args = parser.parse_args()
    app = QApplication(sys.argv if hasattr(sys, "argv") else [])
    app.setOrganizationName(APP_NAME + "Screenshot")
    app.setApplicationName(APP_NAME + "Screenshot")
    settings_dir = ROOT / ".test_cache" / "screenshot-settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(settings_dir))
    QSettings().clear()
    QSettings().setValue("ui/theme", args.theme)
    apply_theme(app)

    window = MainWindow()
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.resize(args.width, args.height)
    window.source_combo.setCurrentIndex(window.source_combo.findData(args.source))
    window.map.set_zoom(args.zoom)
    window.task_tabs.setCurrentIndex(args.tab)
    if args.levels:
        window.multi_level_check.setChecked(True)
        window.multi_levels_edit.setText(args.levels)
    window.show()
    window.coord_format_combo.setCurrentIndex(window.coord_format_combo.findData(args.coordinate_format))
    if args.region:
        window.region_combo.setCurrentIndex(window.region_combo.findData(args.region))
    target = window
    if args.tasks:
        target = TaskManagerDialog(window._task_store, window)
        target.setAttribute(Qt.WA_DontShowOnScreen, True)
        target.show()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    # 定期强制渲染，驱动瓦片网络加载；最后一张保存。
    def refresh() -> None:
        target.grab()

    refresh_timer = QTimer()
    refresh_timer.setInterval(500)
    refresh_timer.timeout.connect(refresh)
    refresh_timer.start()

    def capture() -> None:
        refresh_timer.stop()
        pixmap = target.grab()
        if args.menu and target is window:
            from PyQt5.QtGui import QPainter

            action = window.menuBar().actions()[{"file": 0, "tools": 1, "help": 2}[args.menu]]
            menu = action.menu()
            menu.setAttribute(Qt.WA_DontShowOnScreen, True)
            window._open_app_menu(menu)
            app.processEvents()
            pixmap = target.grab()
            menu_pixmap = menu.grab()
            painter = QPainter(pixmap)
            painter.drawPixmap(window.mapFromGlobal(menu.pos()), menu_pixmap)
            painter.end()
            menu.close()
        pixmap.save(str(output), "PNG")
        print(f"SAVED {output} {pixmap.width()}x{pixmap.height()}")
        if target is not window:
            target.close()
        window.close()
        app.quit()

    QTimer.singleShot(5000, capture)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
