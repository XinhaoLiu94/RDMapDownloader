"""浅色／深色配色与运行中跟随系统的外观管理。"""
from __future__ import annotations

import os
import weakref
from string import Template
from PyQt5.QtCore import QObject, QSettings, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication
from .paths import resource_path

LIGHT_COLORS = {
    'primary': '#0B6B5D', 'on_primary': '#FFFFFF', 'primary_hover': '#09594D', 'primary_pressed': '#074B41',
    'primary_container': '#DEECE7', 'on_primary_container': '#0B6B5D', 'tonal_hover': '#D1E5DC', 'tonal_pressed': '#C2DCCE',
    'surface': '#FBFAF7', 'surface_lowest': '#FFFFFF', 'surface_container_low': '#F4F2EC',
    'surface_container': '#F0EEE7', 'surface_container_high': '#EDEBE3', 'header': '#FBFAF7', 'actions': '#FFFFFF',
    'on_surface': '#1B2421', 'on_surface_variant': '#3C4742', 'muted': '#5F6B66',
    'outline': '#D9D6CB', 'outline_variant': '#E3E1D8', 'divider': '#E8E6DD',
    'state_hover': '#EDEBE3', 'state_pressed': '#E3E5DC', 'disabled_container': '#F4F2EC', 'on_disabled': '#9AA39F',
    'error': '#B63D36', 'error_container': '#FBE8E5', 'success': '#0B6B5D',
    'inverse_surface': '#18231E', 'inverse_on_surface': '#E7ECE9',
    'map_accent': '#4CC9A8', 'map_overlay': '#0E1813', 'map_on_overlay': '#FFFFFF',
}
DARK_COLORS = {
    'primary': '#4CC9A8', 'on_primary': '#06211A', 'primary_hover': '#6BD8BA', 'primary_pressed': '#39B998',
    'primary_container': '#1D3730', 'on_primary_container': '#4CC9A8', 'tonal_hover': '#24483D', 'tonal_pressed': '#2B5145',
    'surface': '#121715', 'surface_lowest': '#1B2220', 'surface_container_low': '#181F1C',
    'surface_container': '#1F2723', 'surface_container_high': '#2B3530', 'header': '#151B18', 'actions': '#181F1C',
    'on_surface': '#E7ECE9', 'on_surface_variant': '#BAC5BF', 'muted': '#98A59F',
    'outline': '#36413B', 'outline_variant': '#2A342F', 'divider': '#26302B',
    'state_hover': '#222B26', 'state_pressed': '#2D3D35', 'disabled_container': '#1E2622', 'on_disabled': '#6B7872',
    'error': '#FF9E97', 'error_container': '#472C28', 'success': '#77D5AE',
    'inverse_surface': '#0C120F', 'inverse_on_surface': '#E7ECE9',
    'map_accent': '#4CC9A8', 'map_overlay': '#0E1813', 'map_on_overlay': '#FFFFFF',
}
# 原地更新，保证地图等模块已导入的颜色引用也随主题切换。
COLORS = LIGHT_COLORS.copy()
_STYLE = Template('''
* { font-family: "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; color: $on_surface; }
QMainWindow, QDialog, QWidget#workspace, QWidget#controlPanel, QWidget#sidePanel { background: $surface; }
QLabel { background: transparent; }
QFrame#appHeader { background: $header; border-bottom: 1px solid $outline_variant; }
QLabel#appTitle { font-size: 15px; font-weight: 600; }
QLabel#sourceBadge { background: $surface_container; color: $on_surface_variant; border-radius: 15px; padding: 5px 12px; font-size: 12px; }
QComboBox#themeCombo { background: transparent; border-color: transparent; min-height: 24px; font-size: 12px; }
QComboBox#themeCombo:hover { background: $state_hover; }
QMenuBar#appMenuBar { background: transparent; border: none; padding: 0; spacing: 2px; }
QMenuBar#appMenuBar::item { background: transparent; color: $on_surface_variant; padding: 7px 12px; border-radius: 8px; font-weight: 500; }
QMenuBar#appMenuBar::item:selected { background: $state_hover; color: $on_surface; }
QMenuBar#appMenuBar::item:pressed { background: $primary_container; color: $primary; }
QMenu { background: $surface_lowest; border: 1px solid $outline; border-radius: 10px; padding: 6px; min-width: 208px; }
QMenu::item { padding: 9px 22px 9px 30px; margin: 2px 0; border-radius: 6px; }
QMenu::item:non-exclusive, QMenu::item:exclusive { padding-left: 14px; }
QMenu::item:selected { background: $primary_container; color: $primary; }
QMenu::item:disabled { color: $on_disabled; }
QMenu::separator { height: 1px; background: $divider; margin: 6px 12px; }
QMenu::indicator { width: 16px; height: 16px; }
QMenu::indicator:checked { background: $primary; border-radius: 3px; image: url("$check"); }
QFrame#mapFrame { border: none; background: $surface_container; }
QToolBar#mapToolbar { background: $header; border: 1px solid $outline_variant; border-radius: 14px; padding: 4px; spacing: 2px; }
QToolBar#mapToolbar QLabel { color: $muted; font-size: 12px; }
QToolBar::separator { background: $outline; width: 1px; margin: 10px 4px; }
QToolButton { background: transparent; border: none; border-radius: 8px; padding: 7px 10px; min-height: 20px; color: $on_surface_variant; }
QToolButton:hover { background: $state_hover; }
QToolButton:pressed, QToolButton:checked { background: $primary_container; color: $primary; }
QToolButton:focus { border: 1px solid $primary; padding: 6px 9px; }
QToolButton:disabled { color: $on_disabled; }
QToolButton#zoomButton { padding: 0; min-height: 30px; font-size: 21px; }
QToolButton#zoomButton:focus { padding: 0; }
QSpinBox#mapZoom { border: none; background: transparent; padding: 0; font-family: Consolas, monospace; font-weight: 500; }
QLabel#mapHud, QFrame#mapCoordinates { background: rgba(14,24,19,205); border: 1px solid rgba(255,255,255,38); border-radius: 10px; color: white; }
QLabel#mapHud { padding: 6px 12px; font-size: 12px; }
QFrame#mapCoordinates QLabel { color: white; font-family: Consolas, monospace; font-size: 12px; }
QLabel#mapToast { background: rgba(14,24,19,225); border: 1px solid rgba(255,255,255,38); border-radius: 8px; padding: 7px 12px; color: white; font-size: 12px; }
QWidget#controlPanel { border-left: 1px solid $outline_variant; }
QGroupBox { background: transparent; border: none; border-bottom: 1px solid $divider; border-radius: 0; margin: 0; padding: 0; font-weight: 400; }
QGroupBox#precisionSection, QGroupBox#taskCard { border: none; }
QLabel#panelTitle { font-size: 17px; font-weight: 600; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; }
QLabel#stepNumber { background: $primary_container; color: $primary; border-radius: 11px; font-size: 12px; font-weight: 600; }
QLabel#hintLabel, QLabel#fieldLabel { color: $muted; font-size: 11px; font-weight: 400; }
QLabel#layerDescription { color: $muted; font-size: 11px; }
QToolButton#layerButton { background: $surface_lowest; border: 1px solid $outline_variant; border-radius: 12px; padding: 5px; font-size: 11px; color: $on_surface_variant; }
QToolButton#layerButton:hover { border-color: $primary; }
QToolButton#layerButton:checked { background: $primary_container; border: 1px solid $primary; color: $primary; font-weight: 600; }
QToolButton#layerButton:focus { border: 2px solid $primary; padding: 4px; }
QFrame#statCard { background: $surface_lowest; border: 1px solid $outline_variant; border-radius: 10px; }
QLabel#statTitle { color: $muted; font-size: 11px; }
QLabel#tileCount { color: $primary; font-size: 18px; font-weight: 600; font-family: Consolas, "Noto Sans SC", monospace; }
QLabel#tileCount[state="error"] { color: $error; font-size: 13px; }
QLabel#statValue { font-size: 13px; font-weight: 500; font-family: Consolas, "Noto Sans SC", monospace; }
QLabel#taskStatus { color: $muted; font-size: 11px; }
QLabel#taskStatus[statusKind="busy"] { color: $primary; }
QLabel#taskStatus[statusKind="ok"] { color: $success; }
QLabel#taskStatus[statusKind="error"] { color: $error; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: $surface_lowest; border: 1px solid $outline; border-radius: 8px; padding: 5px 10px; min-height: 22px; font-weight: 400; selection-background-color: $primary_container; selection-color: $on_surface; }
QSpinBox, QDoubleSpinBox { padding-right: 3px; font-family: Consolas, monospace; }
QDoubleSpinBox { font-size: 13px; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: $muted; }
QLineEdit:focus, QComboBox:focus { border: 2px solid $primary; padding: 4px 9px; }
QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid $primary; padding: 4px 2px 4px 9px; }
QDoubleSpinBox#coordinateSpin { padding-right: 3px; }
QDoubleSpinBox#coordinateSpin:focus { padding-right: 2px; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { background: $disabled_container; color: $on_disabled; border-color: $outline_variant; }
QComboBox { padding-right: 4px; }
QComboBox:focus { padding-right: 3px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url("$chevron_down"); width: 16px; height: 16px; }
QComboBox QAbstractItemView { background: $surface_lowest; border: 1px solid $outline; selection-background-color: $primary_container; selection-color: $on_surface; outline: none; padding: 4px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 20px; border: none; border-top-right-radius: 7px; margin: 1px 1px 0 0; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 20px; border: none; border-bottom-right-radius: 7px; margin: 0 1px 1px 0; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: $state_hover; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("$chevron_up"); width: 12px; height: 12px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("$chevron_down"); width: 12px; height: 12px; }
QSlider::groove:horizontal { height: 4px; border-radius: 2px; background: $outline_variant; }
QSlider::sub-page:horizontal { background: $primary; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; background: $primary; border: 2px solid $surface; border-radius: 9px; margin: -7px 0; }
QSlider::handle:horizontal:disabled, QSlider::sub-page:horizontal:disabled { background: $on_disabled; }
QPushButton { background: $surface_lowest; border: 1px solid $outline; border-radius: 9px; padding: 6px 12px; min-height: 22px; font-weight: 500; }
QPushButton:hover { background: $state_hover; border-color: $muted; }
QPushButton:pressed { background: $state_pressed; }
QPushButton:focus { border: 2px solid $primary; padding: 5px 11px; }
QPushButton:disabled { background: $disabled_container; color: $on_disabled; border-color: $outline_variant; }
QPushButton#primaryButton { background: $primary; color: $on_primary; border-color: $primary; border-radius: 12px; font-size: 14px; font-weight: 600; }
QPushButton#primaryButton:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton#primaryButton:pressed { background: $primary_pressed; }
QPushButton#primaryButton:disabled { background: $disabled_container; color: $on_disabled; border-color: $outline_variant; }
QPushButton#tonalButton { background: $primary_container; color: $primary; border-color: $primary_container; font-weight: 600; }
QPushButton#tonalButton:hover { background: $tonal_hover; }
QPushButton#dangerButton { border-radius: 12px; }
QPushButton#browseButton { padding: 5px 12px; min-width: 32px; border-radius: 8px; }
QPushButton#logToggle, QPushButton#textButton { background: transparent; border: 1px solid transparent; color: $primary; border-radius: 6px; padding: 3px 4px; min-height: 18px; font-size: 11px; text-align: left; }
QPushButton#logToggle:hover, QPushButton#textButton:hover { background: $state_hover; }
QPushButton#logToggle:checked { background: $primary_container; }
QFrame#downloadDock { background: $actions; border-top: 1px solid $outline_variant; }
QTabWidget::pane { background: $actions; border: none; top: 0; }
QTabBar { background: $surface_container; border-radius: 10px; }
QTabBar::tab { background: $surface_container; color: $on_surface_variant; padding: 7px 14px; border: none; margin: 3px; border-radius: 7px; font-size: 12px; }
QTabBar::tab:selected { background: $surface_lowest; color: $on_surface; font-weight: 600; }
QTabBar::tab:hover:!selected { background: $state_hover; }
QProgressBar { background: $outline_variant; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; color: transparent; }
QProgressBar::chunk { background: $primary; border-radius: 3px; }
QPlainTextEdit#logView { background: $inverse_surface; color: $inverse_on_surface; border: none; border-radius: 10px; font-family: Consolas, monospace; font-size: 11px; padding: 8px; }
QTableWidget { background: $surface_lowest; alternate-background-color: $surface; border: 1px solid $outline_variant; border-radius: 10px; gridline-color: $divider; selection-background-color: $primary_container; selection-color: $on_surface; }
QTableWidget::item { padding: 4px 8px; }
QHeaderView::section { background: $surface_container_low; color: $on_surface_variant; border: none; border-bottom: 1px solid $divider; padding: 10px 12px; font-weight: 600; }
QPlainTextEdit#taskDetails { background: $surface_lowest; color: $on_surface_variant; border: 1px solid $outline_variant; border-radius: 10px; padding: 12px; }
QSplitter::handle { background: $outline_variant; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 4px 0; }
QScrollBar::handle:vertical { background: $outline; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QCheckBox { spacing: 7px; color: $on_surface_variant; font-size: 12px; }
QCheckBox::indicator { width: 14px; height: 14px; background: transparent; border: 1px solid $muted; border-radius: 3px; }
QCheckBox::indicator:hover { border-color: $primary; background: $state_hover; }
QCheckBox::indicator:checked { background: $primary; border-color: $primary; image: url("$check"); }
QCheckBox::indicator:disabled { border-color: $outline; }
QCheckBox::indicator:checked:disabled { background: $on_disabled; border-color: $on_disabled; }
QToolTip { background: $inverse_surface; color: $inverse_on_surface; border: 1px solid $outline; border-radius: 4px; padding: 7px; }
''')

def stylesheet(scheme: str) -> str:
    suffix = '-dark' if scheme == 'dark' else ''
    return _STYLE.substitute(**COLORS,
        check=resource_path(f'assets/material/check{suffix}.svg').as_posix(),
        chevron_down=resource_path(f'assets/material/chevron-down{suffix}.svg').as_posix(),
        chevron_up=resource_path(f'assets/material/chevron-up{suffix}.svg').as_posix())

def apply_theme(app: QApplication, scheme: str = 'light') -> None:
    if scheme not in ('light', 'dark'):
        raise ValueError('Unknown color scheme')
    if app.property('rdmapNativeDark') is None:
        app.setProperty('rdmapNativeDark', app.palette().color(QPalette.Window).lightness() < 128)
    COLORS.clear()
    COLORS.update(DARK_COLORS if scheme == 'dark' else LIGHT_COLORS)
    if app.style().objectName().lower() != 'fusion':
        app.setStyle('Fusion')
    font = QFont('Noto Sans SC')
    if not font.exactMatch(): font = QFont('Microsoft YaHei UI')
    if not font.exactMatch(): font = QFont('Segoe UI')
    font.setPointSize(10)
    app.setFont(font)
    app.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    palette = QPalette()
    for role, color in (
        (QPalette.Window, 'surface'), (QPalette.WindowText, 'on_surface'),
        (QPalette.Base, 'surface_lowest'), (QPalette.AlternateBase, 'surface_container_low'),
        (QPalette.Text, 'on_surface'), (QPalette.Button, 'surface_lowest'), (QPalette.ButtonText, 'on_surface'),
        (QPalette.Highlight, 'primary_container'), (QPalette.HighlightedText, 'on_surface'),
        (QPalette.Link, 'primary'), (QPalette.ToolTipBase, 'inverse_surface'),
        (QPalette.ToolTipText, 'inverse_on_surface'), (QPalette.Light, 'outline'), (QPalette.Mid, 'outline_variant')):
        palette.setColor(role, QColor(COLORS[color]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(COLORS['on_disabled']))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(scheme))
    app.setProperty('rdmapColorScheme', scheme)

def system_theme() -> str:
    """读取应用颜色模式，不修改系统设置。"""
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            return 'light' if value else 'dark'
        except OSError:
            pass
    app = QApplication.instance()
    return 'dark' if app and app.property('rdmapNativeDark') else 'light'

def style_window_titlebar(window, scheme: str) -> None:
    if os.name != 'nt' or QApplication.platformName() == 'offscreen': return
    try:
        import ctypes
        dark = ctypes.c_int(scheme == 'dark')
        handle = ctypes.c_void_p(int(window.winId()))
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        if result != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 19, ctypes.byref(dark), ctypes.sizeof(dark))
    except (AttributeError, OSError):
        pass

class ThemeManager(QObject):
    changed = pyqtSignal(str)
    def __init__(self, app: QApplication, system_reader=None) -> None:
        super().__init__(app)
        self._app_ref = weakref.ref(app)
        self.mode = 'system'
        self.scheme = ''
        self._read_system = system_reader or system_theme
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        app.focusChanged.connect(self._focus_changed)
        self.restore()

    @property
    def app(self) -> QApplication:
        return self._app_ref()

    def restore(self) -> None:
        mode = str(QSettings().value('ui/theme', 'system'))
        self.set_mode(mode if mode in ('system', 'light', 'dark') else 'system', persist=False)

    def set_mode(self, mode: str, persist: bool = True) -> None:
        if mode not in ('system', 'light', 'dark'): raise ValueError('Unknown theme mode')
        changed = mode != self.mode
        self.mode = mode
        if persist:
            settings = QSettings()
            settings.setValue('ui/theme', mode)
            settings.sync()
        self._timer.start() if mode == 'system' else self._timer.stop()
        self.refresh(force=changed)

    def refresh(self, force: bool = False) -> None:
        scheme = self._read_system() if self.mode == 'system' else self.mode
        if scheme not in ('light', 'dark'): scheme = 'light'
        if scheme == self.scheme and not force: return
        self.scheme = scheme
        apply_theme(self.app, scheme)
        for window in self.app.topLevelWidgets():
            if window.isWindow() and window.isVisible(): style_window_titlebar(window, scheme)
            window.update()
        self.changed.emit(scheme)

    def _focus_changed(self, _old, current):
        if current is not None:
            style_window_titlebar(current.window(), self.scheme)

def theme_manager(app: QApplication | None = None) -> ThemeManager:
    app = app or QApplication.instance()
    if app is None: raise RuntimeError('QApplication required')
    manager = getattr(app, '_rdmap_theme_manager', None)
    if manager is None:
        manager = ThemeManager(app)
        app._rdmap_theme_manager = manager
    return manager

def set_state(widget, name: str, value) -> None:
    if widget.property(name) == value: return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
