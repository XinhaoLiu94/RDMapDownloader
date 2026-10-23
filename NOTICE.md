# 来源、许可与第三方声明

## 本项目

RDMapDownloader 的 Python 实现及本项目原创资源采用 **GNU General Public License v3.0 only（GPL-3.0-only）**。
完整条款见 [LICENSE](LICENSE)。Copyright (C) 2026 XinhaoLiu94 and contributors.

分发本项目或其修改版本时，应保留版权、许可证及来源说明，并按 GPL-3.0 提供相应源码。
本项目按许可证原文提供，不附带保证。

## 原项目参考

- 项目：[hejustbrave/GMapDownloader](https://github.com/hejustbrave/GMapDownloader)
- 本项目参考其地图浏览、矩形框选、瓦片下载与拼接流程，以及 XYZ 行列和目录约定，采用 Python / PyQt5 实现。
- 本仓库未打包原项目的 C# 源文件、GMap.NET DLL 或原项目图标。
- 截至 2026-10-23，原仓库未声明明确的开源许可证；其 README 包含个人学习与科研用途声明。
  本项目的 GPL-3.0 许可不对原项目代码或资源授予权利，也不代表原作者已许可衍生分发。
  如需复制、移植或分发原项目受版权保护的代码或资源，应另行取得适用许可。
- 保留此致谢不代表原作者参与、认可或支持本项目。

## 第三方组件

本仓库通过依赖管理安装以下组件，不将其许可证替换为本项目许可证：

- [PyQt5](https://www.riverbankcomputing.com/software/pyqt/)：本项目按其开源 GPL v3 条款使用。
  Qt 库自身亦有独立许可要求，参见 [Qt 许可说明](https://www.qt.io/licensing/)。
- [Requests](https://requests.readthedocs.io/)：Apache License 2.0。
- 打包、图标生成等开发工具以各自许可证为准；发布可执行文件时，仍需保留随附依赖的版权和许可文件。

## 图源与地图数据

高德、Google、天地图及 Esri 提供的地图瓦片、卫星影像、道路和地名标注，以及文档截图中的地图内容，
由相应服务商和数据提供者保留权利，不适用本项目的 GPL-3.0 许可。
项目源码公开不代表获得地图数据的批量下载、离线使用或再分发授权，使用时应遵守相应服务条款。

常用区域的近似矩形范围参考 [Natural Earth 国家数据](https://www.naturalearthdata.com/downloads/110m-cultural-vectors/110m-admin-0-countries/)，
用于快速框选，不表示行政边界，也不进行国界裁剪。
