"""RDMapDownloader —— 基于 PyQt5 的多图源地图瓦片下载与拼接工具。"""

from __future__ import annotations

import os as _os

__version__ = "1.1.0"

# Qt 5 在 Windows 上通过 Network List Manager 枚举网卡时，遇到虚拟网卡
# （VMware / Hyper-V / VPN / WSL 等）会输出大量以下警告：
#   qt.network.monitor: Could not get the INetworkConnection instance ...
# 这些警告不影响网络请求，仅为噪声；在导入包时关闭该分类的警告。
# 若用户已设置 QT_LOGGING_RULES，则保留原规则并追加本条。
_QT_LOG_RULE = "qt.network.monitor.warning=false"
_existing_qt_rules = _os.environ.get("QT_LOGGING_RULES", "").strip()
if _QT_LOG_RULE not in _existing_qt_rules:
    _os.environ["QT_LOGGING_RULES"] = (
        f"{_existing_qt_rules};{_QT_LOG_RULE}" if _existing_qt_rules else _QT_LOG_RULE
    )
