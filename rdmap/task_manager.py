"""下载任务列表与失败记录查看。"""
from __future__ import annotations

import csv
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .providers import PROVIDER_MAP, SOURCE_MAP
from .tasks import TaskStore

STATUS_NAMES = {
    "pending": "待开始", "running": "下载中", "paused": "已暂停",
    "partial": "有失败，可继续", "completed": "已完成", "error": "未完成，可继续",
}


class TaskManagerDialog(QDialog):
    resumeRequested = pyqtSignal(str)
    tasksChanged = pyqtSignal()

    def __init__(self, store: TaskStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.tasks: dict[str, dict] = {}
        self.setWindowTitle("下载任务管理")
        self.resize(1000, 640)
        self.setMinimumSize(780, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("下载任务")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        hint = QLabel("任务和失败记录自动保存。选择未完成的任务继续下载，无需重新框选；已下载瓦片会保留。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["任务 / 图源", "级别", "已完成 / 总数", "失败", "状态", "创建时间"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.update_details)
        layout.addWidget(self.table, 2)
        self.empty_label = QLabel("还没有下载任务。点击主界面的“开始下载”后，任务会自动出现在这里。")
        self.empty_label.setObjectName("hintLabel")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        self.details = QPlainTextEdit()
        self.details.setObjectName("taskDetails")
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(280)
        layout.addWidget(self.details, 1)
        buttons = QHBoxLayout()
        self.btn_resume = QPushButton("继续下载")
        self.btn_resume.setObjectName("primaryButton")
        self.btn_resume.clicked.connect(self.resume_selected)
        buttons.addWidget(self.btn_resume)
        self.btn_export = QPushButton("导出失败清单")
        self.btn_export.clicked.connect(self.export_failures)
        buttons.addWidget(self.btn_export)
        self.btn_open = QPushButton("打开瓦片目录")
        self.btn_open.clicked.connect(self.open_directory)
        buttons.addWidget(self.btn_open)
        buttons.addStretch()
        self.btn_delete = QPushButton("删除记录")
        self.btn_delete.setToolTip("只删除任务记录，保留下载的瓦片")
        self.btn_delete.clicked.connect(self.delete_selected)
        buttons.addWidget(self.btn_delete)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def selected_id(self) -> str | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item is not None else None

    def refresh(self, selected_id: str | None = None) -> None:
        selected_id = selected_id or self.selected_id()
        try:
            tasks = self.store.list_groups()
        except Exception as exc:
            self.details.setPlainText(f"读取任务失败：{exc}")
            return
        self.tasks = {task["id"]: task for task in tasks}
        self.table.blockSignals(True)
        self.table.setRowCount(len(tasks))
        selected_row = 0
        for row, task in enumerate(tasks):
            if task["id"] == selected_id or any(item["id"] == selected_id for item in task["level_tasks"]):
                selected_row = row
            created = datetime.fromisoformat(task["created_at"]).astimezone().strftime("%m-%d %H:%M:%S")
            values = [task["name"], task["zoom_label"], f'{task["done"]:,} / {task["total"]:,}',
                      str(task["failed"]), STATUS_NAMES.get(task["status"], task["status"]), created]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(task["output_dir"])
                if col == 0:
                    item.setData(Qt.UserRole, task["id"])
                self.table.setItem(row, col, item)
        if tasks:
            self.table.selectRow(selected_row)
        self.table.blockSignals(False)
        self.empty_label.setVisible(not tasks)
        self.update_details()

    def update_details(self) -> None:
        task = self.tasks.get(self.selected_id())
        running = any(t["status"] == "running" for t in self.tasks.values())
        self.btn_resume.setEnabled(bool(task) and not running and task["status"] != "completed")
        self.btn_resume.setText("继续整组下载" if task and len(task["level_tasks"]) > 1 else "继续下载")
        self.btn_delete.setEnabled(bool(task) and task["status"] != "running")
        self.btn_open.setEnabled(bool(task))
        self.btn_export.setEnabled(bool(task) and task["failed"] > 0)
        if not task:
            self.details.clear()
            return
        provider = PROVIDER_MAP.get(task["provider_key"])
        source = SOURCE_MAP.get(provider.source) if provider else None
        display = f"{source.name} · {provider.name}" if source else task["provider_key"]
        text = (f"图源：{display}　｜　Z{task['zoom_label']}\n目录：{task['output_dir']}\n"
                f"已完成 {task['done']:,} / {task['total']:,}，失败 {task['failed']:,}，"
                f"待补齐 {task['total'] - task['done']:,}\n{task['message']}")
        for item in task["level_tasks"]:
            text += (f"\nZ{item['zoom']}：{item['done']:,}/{item['total']:,} 张，失败 {item['failed']:,}，"
                     f"{STATUS_NAMES.get(item['status'], item['status'])}；"
                     f"行 {item['min_row']}–{item['max_row']}，列 {item['min_col']}–{item['max_col']}")
        try:
            failures = self.store.failures(task["id"], limit=200)
            if failures:
                text += "\n\n失败记录（显示前 200 条，导出可查看全部）：\n"
                text += "\n".join(f"Z{f['zoom']} 行 {f['row']}，列 {f['col']}：{f['error']}" for f in failures)
        except Exception as exc:
            text += f"\n读取失败记录出错：{exc}"
        if self.details.toPlainText() != text:
            scroll = self.details.verticalScrollBar().value()
            self.details.setPlainText(text)
            self.details.verticalScrollBar().setValue(scroll)

    def resume_selected(self) -> None:
        task_id = self.selected_id()
        if task_id:
            self.resumeRequested.emit(task_id)
            self.refresh(task_id)

    def open_directory(self) -> None:
        task = self.tasks.get(self.selected_id())
        if task:
            QDesktopServices.openUrl(QUrl.fromLocalFile(task["output_dir"]))

    def delete_selected(self) -> None:
        task_id = self.selected_id()
        if not task_id:
            return
        if QMessageBox.question(self, "删除任务记录", "删除所选任务（含全部级别）的记录？已下载的瓦片文件会保留。") != QMessageBox.Yes:
            return
        try:
            self.store.delete(task_id)
            self.refresh()
            self.tasksChanged.emit()
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def export_failures(self) -> None:
        task_id = self.selected_id()
        if not task_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出失败清单", "失败瓦片.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            task = self.tasks[task_id]
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["级别", "行", "列", "失败原因"])
                for failure in self.store.failures(task_id):
                    writer.writerow([failure["zoom"], failure["row"], failure["col"], failure["error"]])
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)
