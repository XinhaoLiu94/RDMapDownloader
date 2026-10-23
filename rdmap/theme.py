"""Material 3 浅色主题与共享颜色角色。"""
from string import Template

from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication

from .paths import resource_path

COLORS = {
    "primary": "#0B57D0", "on_primary": "#FFFFFF",
    "primary_hover": "#084FBE", "primary_pressed": "#0847AA",
    "primary_container": "#D3E3FD", "on_primary_container": "#041E49",
    "tonal_hover": "#C2D7F8", "tonal_pressed": "#AECBF5",
    "surface": "#F8FAFD", "surface_lowest": "#FFFFFF",
    "surface_container_low": "#F0F4F9", "surface_container": "#E9EEF6",
    "surface_container_high": "#E1E7EF",
    "on_surface": "#1F1F1F", "on_surface_variant": "#444746",
    "outline": "#747775", "outline_variant": "#C4C7C5", "divider": "#DEE3EB",
    "state_hover": "#E7EFFA", "state_pressed": "#D5E3F7",
    "disabled_container": "#E3E5E8", "on_disabled": "#909499",
    "error": "#B3261E", "error_container": "#F9DEDC", "success": "#146C2E",
    "inverse_surface": "#303134", "inverse_on_surface": "#F2F2F2",
}

STYLE_SHEET = Template("""
* { font-family: "Roboto", "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; color: $on_surface; }
QMainWindow, QDialog, QWidget#workspace, QWidget#sidePanel { background: $surface; }
QLabel { background: transparent; }
QLabel#panelTitle { font-size: 17px; font-weight: 600; padding: 3px 0; }
QLabel#sectionTitle { font-size: 14px; font-weight: 600; padding: 0 4px; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; }
QLabel#stepNumber { background: $primary_container; color: $primary; border-radius: 12px; font-size: 12px; font-weight: 600; }
QFrame#mapFrame { background: $surface_lowest; border: 1px solid $divider; border-radius: 16px; }
QToolBar#mapToolbar { background: $surface_container_low; border: none; border-top-left-radius: 16px; border-top-right-radius: 16px; padding: 8px 10px; spacing: 6px; }
QToolBar#mapToolbar QLabel { font-size: 12px; }
QToolBar::separator { background: $divider; width: 1px; margin: 8px 4px; }
QToolButton { background: transparent; border: none; border-radius: 18px; padding: 8px 12px; min-height: 18px; color: $primary; }
QToolButton:hover { background: $state_hover; }
QToolButton:pressed { background: $state_pressed; }
QToolButton:focus { background: $primary_container; }
QToolButton:disabled { color: $on_disabled; }
QLabel#mapFooter { background: $surface_lowest; color: $on_surface_variant; font-size: 11px; border-bottom-left-radius: 16px; border-bottom-right-radius: 16px; padding: 0 12px; }
QMenuBar#appMenuBar { background: $surface; border: none; padding: 6px 10px 2px; spacing: 6px; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QMenuBar#appMenuBar::item { background: $surface_container_low; color: $on_surface_variant; border: 1px solid transparent; padding: 6px 16px; border-radius: 16px; font-weight: 500; }
QMenuBar#appMenuBar::item:selected { background: $primary_container; color: $primary; }
QMenuBar#appMenuBar::item:pressed { background: $tonal_pressed; color: $on_primary_container; }
QMenu { background: $surface_lowest; color: $on_surface; border: 1px solid $outline_variant; border-radius: 12px; padding: 6px; min-width: 208px; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QMenu::item { padding: 9px 22px 9px 30px; margin: 2px 0; border-radius: 6px; }
QMenu::item:non-exclusive, QMenu::item:exclusive { padding-left: 14px; }
QMenu::item:selected { background: $primary_container; color: $on_primary_container; }
QMenu::item:disabled { color: $on_disabled; }
QMenu::separator { height: 1px; background: $divider; margin: 6px 12px; }
QMenu::indicator { width: 16px; height: 16px; }
QMenu::indicator:checked { background: $primary; border-radius: 3px; image: url("$check"); }
QStatusBar { background: $surface; border-top: 1px solid $divider; font-size: 11px; }
QStatusBar QLabel { color: $on_surface_variant; padding: 3px 8px; font-size: 11px; }
QStatusBar::item { border: none; }
QGroupBox { background: $surface_container_low; border: none; border-radius: 16px; margin: 0; padding: 0; font-weight: 400; }
QGroupBox#taskCard { border: none; border-top-left-radius: 0; border-top-right-radius: 0; }
QLabel#hintLabel { color: $on_surface_variant; font-size: 11px; font-weight: 400; }
QLabel#layerDescription { background: $surface_container; color: $on_surface_variant; border-radius: 8px; padding: 8px 10px; font-size: 11px; font-weight: 400; }
QLabel#taskStatus { color: $on_surface_variant; font-size: 12px; font-weight: 400; }
QLabel#tileCount { color: $primary; font-size: 22px; font-weight: 600; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: $surface_lowest; border: 1px solid $outline_variant; border-radius: 8px; padding: 5px 8px; min-height: 22px; font-weight: 400; selection-background-color: $primary_container; selection-color: $on_primary_container; }
QSpinBox, QDoubleSpinBox { padding-right: 22px; }
QDoubleSpinBox { font-size: 12px; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: $on_surface_variant; }
QLineEdit:focus, QComboBox:focus { border: 2px solid $primary; padding: 4px 7px; }
QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid $primary; padding: 4px 21px 4px 7px; }
QDoubleSpinBox#coordinateSpin { padding-right: 3px; }
QDoubleSpinBox#coordinateSpin:focus { padding-right: 2px; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { background: $surface_container; border-color: $divider; color: $on_disabled; }
QComboBox { padding-right: 28px; }
QComboBox:focus { padding-right: 27px; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: url("$chevron_down"); width: 18px; height: 18px; }
QComboBox QAbstractItemView { background: $surface_lowest; border: 1px solid $divider; border-radius: 8px; selection-background-color: $primary_container; selection-color: $on_primary_container; outline: none; padding: 6px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 20px; border: none; border-top-right-radius: 7px; margin: 1px 1px 0 0; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 20px; border: none; border-bottom-right-radius: 7px; margin: 0 1px 1px 0; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: $state_hover; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("$chevron_up"); width: 12px; height: 12px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("$chevron_down"); width: 12px; height: 12px; }
QPushButton { background: transparent; border: 1px solid $outline_variant; border-radius: 18px; padding: 6px 14px; min-height: 22px; color: $primary; font-weight: 500; }
QPushButton:hover { background: $state_hover; border-color: $outline; }
QPushButton:pressed { background: $state_pressed; }
QPushButton:focus { border: 2px solid $primary; padding: 5px 13px; }
QPushButton:disabled { background: transparent; color: $on_disabled; border-color: $disabled_container; }
QPushButton#primaryButton { background: $primary; color: $on_primary; border-color: $primary; font-weight: 600; }
QPushButton#primaryButton:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton#primaryButton:pressed { background: $primary_pressed; border-color: $primary_pressed; }
QPushButton#primaryButton:focus { border: 2px solid $on_primary_container; }
QPushButton#primaryButton:disabled { background: $disabled_container; border-color: $disabled_container; color: $on_disabled; }
QPushButton#tonalButton { background: $primary_container; color: $on_primary_container; border-color: $primary_container; }
QPushButton#tonalButton:hover { background: $tonal_hover; border-color: $tonal_hover; }
QPushButton#tonalButton:pressed { background: $tonal_pressed; border-color: $tonal_pressed; }
QPushButton#tonalButton:disabled { background: $disabled_container; border-color: $disabled_container; color: $on_disabled; }
QPushButton#dangerButton { color: $error; }
QPushButton#dangerButton:hover { background: $error_container; }
QPushButton#dangerButton:disabled { color: $on_disabled; background: transparent; border-color: $disabled_container; }
QPushButton#browseButton { padding: 0; min-width: 34px; max-width: 34px; min-height: 32px; border-radius: 17px; }
QPushButton#logToggle, QPushButton#textButton { background: transparent; border: 1px solid transparent; color: $primary; border-radius: 16px; text-align: left; padding: 5px 10px; min-height: 18px; font-size: 12px; }
QPushButton#logToggle:hover, QPushButton#textButton:hover { background: $state_hover; }
QPushButton#logToggle:pressed, QPushButton#textButton:pressed { background: $state_pressed; }
QPushButton#logToggle:checked, QPushButton#textButton:checked { background: $primary_container; }
QPushButton#logToggle:focus, QPushButton#textButton:focus { border-color: $primary; }
QTabWidget::pane { background: $surface_container_low; border: none; border-bottom-left-radius: 16px; border-bottom-right-radius: 16px; top: 0; }
QTabBar { background: $surface_container_low; }
QTabBar::tab { background: $surface_container_low; color: $on_surface_variant; padding: 10px 24px; border: none; border-bottom: 3px solid transparent; font-weight: 500; }
QTabBar::tab:selected { color: $primary; border-bottom-color: $primary; }
QTabBar::tab:hover { background: $state_hover; }
QTabBar::tab:first { border-top-left-radius: 16px; }
QTabBar::tab:last { border-top-right-radius: 16px; }
QProgressBar { background: $primary_container; border: none; border-radius: 2px; min-height: 4px; max-height: 4px; color: transparent; }
QProgressBar::chunk { background: $primary; border-radius: 2px; }
QPlainTextEdit#logView { background: $inverse_surface; color: $inverse_on_surface; border: none; border-radius: 12px; font-family: Consolas, monospace; font-size: 11px; padding: 8px; }
QTableWidget { background: $surface_lowest; alternate-background-color: $surface; border: 1px solid $divider; border-radius: 12px; gridline-color: $divider; selection-background-color: $primary_container; selection-color: $on_primary_container; }
QTableWidget::item { padding: 4px 8px; }
QHeaderView::section { background: $surface_container_low; color: $on_surface_variant; border: none; border-bottom: 1px solid $divider; padding: 10px 12px; font-weight: 600; }
QPlainTextEdit#taskDetails { background: $surface_lowest; color: $on_surface_variant; border: 1px solid $divider; border-radius: 12px; padding: 12px; }
QSplitter::handle { background: transparent; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0; }
QScrollBar::handle:vertical { background: $outline_variant; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $outline; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QCheckBox { spacing: 8px; color: $on_surface_variant; font-size: 12px; }
QCheckBox::indicator { width: 16px; height: 16px; background: transparent; border: 2px solid $outline; border-radius: 3px; }
QCheckBox::indicator:hover { border-color: $primary; background: $state_hover; }
QCheckBox::indicator:checked { background: $primary; border-color: $primary; image: url("$check"); }
QCheckBox::indicator:checked:hover { background: $primary_hover; border-color: $primary_hover; }
QCheckBox::indicator:disabled { border-color: $outline_variant; }
QCheckBox::indicator:checked:disabled { background: $on_disabled; border-color: $on_disabled; }
QToolTip { background: $inverse_surface; color: $inverse_on_surface; border: none; border-radius: 4px; padding: 7px; }
""").substitute(
    **COLORS,
    check=resource_path("assets/material/check.svg").as_posix(),
    chevron_down=resource_path("assets/material/chevron-down.svg").as_posix(),
    chevron_up=resource_path("assets/material/chevron-up.svg").as_posix(),
)


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI")
    if not font.exactMatch():
        font = QFont("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)
    palette = QPalette()
    for role, color in (
        (QPalette.Window, "surface"), (QPalette.WindowText, "on_surface"),
        (QPalette.Base, "surface_lowest"), (QPalette.AlternateBase, "surface_container_low"),
        (QPalette.Text, "on_surface"), (QPalette.Button, "surface_container_low"),
        (QPalette.ButtonText, "primary"), (QPalette.Highlight, "primary_container"),
        (QPalette.HighlightedText, "on_primary_container"), (QPalette.Link, "primary"),
        (QPalette.ToolTipBase, "inverse_surface"), (QPalette.ToolTipText, "inverse_on_surface"),
    ):
        palette.setColor(role, QColor(COLORS[color]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(COLORS["on_disabled"]))
    app.setPalette(palette)
    app.setStyleSheet(STYLE_SHEET)
