"""经纬度的十进制度／度分秒输入；内部始终使用十进制度。"""

from __future__ import annotations

import math
import re

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QValidator
from PyQt5.QtWidgets import QDoubleSpinBox


_DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*°?$")
_DMS = re.compile(
    r"^\s*([+-]?)(\d{1,3})\s*(?:°|:|\s)\s*"
    r"(\d{1,2})\s*(?:[′'’]|:|\s)\s*"
    r"(\d{1,2}(?:\.\d*)?)\s*[″\"”]?\s*$"
)
_DMS_CHARACTERS = re.compile(r"^[\d\s+\-.:°′'’″\"”]*$")


def format_dms(value: float) -> str:
    """保留毫角秒，先取整再拆分以正确处理秒／分进位和负零度。"""
    if not math.isfinite(value):
        raise ValueError("经纬度必须是有限数值")
    milliseconds = round(abs(value) * 3_600_000)
    degrees, remainder = divmod(milliseconds, 3_600_000)
    minutes, seconds = divmod(remainder, 60_000)
    sign = "-" if value < 0 and milliseconds else ""
    return f"{sign}{degrees}°{minutes:02d}′{seconds / 1000:06.3f}″"


def parse_coordinate(text: str, coordinate_format: str = "decimal") -> float:
    """度分秒可用 °′″、英文引号、冒号或空格分隔，也接受十进制度粘贴。"""
    text = text.strip().replace("−", "-")
    if _DECIMAL.fullmatch(text):
        value = float(text.removesuffix("°").strip())
    elif coordinate_format == "dms" and (match := _DMS.fullmatch(text)):
        sign, degrees, minutes, seconds = match.groups()
        minutes, seconds = int(minutes), float(seconds)
        if minutes >= 60 or seconds >= 60:
            raise ValueError("分和秒必须小于 60")
        value = int(degrees) + minutes / 60 + seconds / 3600
        if sign == "-":
            value = -value
    else:
        raise ValueError("请输入有效的经纬度")
    if not math.isfinite(value):
        raise ValueError("经纬度必须是有限数值")
    return value


class CoordinateSpinBox(QDoubleSpinBox):
    """切换显示格式时保留六位小数的原值，供地图和下载范围直接使用。"""

    def __init__(self, parent=None) -> None:
        self._coordinate_format = "decimal"
        super().__init__(parent)
        self.setObjectName("coordinateSpin")
        self.setDecimals(6)
        self.setKeyboardTracking(False)
        self.setAlignment(Qt.AlignRight)
        self.setMinimumWidth(96)
        self.set_coordinate_format("decimal")

    @property
    def coordinate_format(self) -> str:
        return self._coordinate_format

    def set_coordinate_format(self, coordinate_format: str) -> None:
        if coordinate_format not in ("decimal", "dms"):
            raise ValueError("不支持的坐标格式")
        # 先按旧格式提交尚未结束的输入，再改变显示；不能把格式化文字当作新值。
        self.interpretText()
        self._coordinate_format = coordinate_format
        self.setSingleStep(1 / 3600 if coordinate_format == "dms" else 0.0001)
        self.lineEdit().setText(self.textFromValue(self.value()))
        self.setToolTip(
            "例如：23°36′00.000″；也可输入 23:36:00 或粘贴十进制度。负数表示南纬／西经，上下键按 1 秒调整。"
            if coordinate_format == "dms"
            else "例如：23.600000。负数表示南纬／西经，上下键按 0.0001° 调整。"
        )
        self.updateGeometry()

    def textFromValue(self, value: float) -> str:
        return format_dms(value) if self._coordinate_format == "dms" else f"{value:.6f}°"

    def valueFromText(self, text: str) -> float:
        try:
            # Qt 不会再次替自定义解析器取整；统一到内部精度，避免仅切换格式
            # 就因毫角秒换算的尾差触发 valueChanged、改变瓦片边界。
            return round(parse_coordinate(text, self._coordinate_format), self.decimals())
        except ValueError:
            return self.value()

    def validate(self, text: str, position: int):
        try:
            value = parse_coordinate(text, self._coordinate_format)
        except ValueError:
            # 允许输入过程中的不完整度／分／秒，但拒绝完整的非法分秒。
            if self._coordinate_format == "dms" and _DMS.fullmatch(text):
                state = QValidator.Invalid
            elif _DMS_CHARACTERS.fullmatch(text):
                state = QValidator.Intermediate
            else:
                state = QValidator.Invalid
        else:
            state = QValidator.Acceptable if self.minimum() <= value <= self.maximum() else QValidator.Invalid
        return state, text, position
