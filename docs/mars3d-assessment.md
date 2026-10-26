# Mars3D 接入评估与落地记录

结论：**已作为第三个页签接入，不替换原 Cesium 3D。**

当前顶部切换为 **2D / 3D(Cesium) / Mars3D**，三种模式分别保存图源、视图、选区和相机；
下载、拼接、任务管理完全复用现有 2D 逻辑。Mars3D 页当前是实验性入口，默认仍以 Cesium 3D 为主。

## 1. 版本选择

| 组合 | 说明 | 结果 |
|---|---|---|
| Mars3D 3.11.8 + mars3d-cesium 1.145 | 最新版，peer 要求 Cesium `^1.131` | 需要升级现有 Cesium，POC 可初始化，但会牵动现有 3D/地形/Chrome 83 兼容 |
| **Mars3D 3.7.17 + CesiumJS 1.117** | peer 正好是 `mars3d-cesium ^1.117`，与当前 3D 一致 | **已采用**；不升级 Cesium，只增加 Mars3D/Turf |

这也是本次接入最重要的取舍：用稍旧但兼容当前 Qt 5.15 WebEngine / Cesium 1.117 的 Mars3D，
避免为了最新版把已稳定的 Cesium 3D 整体升级。

资源：

- Mars3D 3.7.17（Apache-2.0，约 6.4 MB 解包；项目只放运行时所需的 JS/CSS/img，约 4.5 MB）
- Turf.js 6.5.0（MIT，`turf.min.js` 约 0.6 MB）
- CesiumJS 1.117：沿用现有运行时缓存与 `ResourceStore`，不新增 `mars3d-cesium` 包
- Mars3D 资源目录：`assets/mars3d/`，随项目分发，无需 Node.js 或前端构建

## 2. 已实现的接入

- `rdmap/cesium_widget.py` 增加 `engine="cesium" | "mars3d"`，页面 URL 由 `TerrainServer` 选择。
- `rdmap/terrain_server.py` 增加 Mars3D 页面/库/图片/Turf 的静态路由，仍只监听 `127.0.0.1`，
  使用随机 token；影像/高程接口与 Cesium 3D 共用。
- `rdmap/map_modes.py` 增加 `mars3d` 模式、独立 `CesiumWidget`、独立相机状态和模式切换。
- `rdmap/workspace_ui.py` 顶部增加 **Mars3D** 按钮。
- `assets/mars3d/index-mars3d.html`、`viewer-mars3d.js`、`viewer-mars3d.css`：
  - `mars3d.Map` + `mars3d.layer.XyzLayer` 指向现有本地影像代理；
  - 复用当前自定义 65×65 高程 provider；
  - 实现与 Cesium 页相同的 `window.rdmap` 命令接口；
  - 保留 Mars3D 社区版 Logo 与控制台版权。
- `assets/mars3d/` 内附 Mars3D Apache-2.0、Turf MIT 许可与第三方说明。

## 3. 验证结果

在本机 Qt 5.15.2 / QtWebEngine Chromium 83 下实际跑通：

```text
Mars3D 地图已就绪：Mars3D 3.7.17 · WGS84
imagery: 1
tiles: true
terrain: Object（自定义高程 provider）
levels: [10, ...]
```

- 通过真实 `MainWindow` 切换到 **Mars3D** 页签，成功加载 Esri 影像、注记、真实地形和默认选区。
- 截图见 [screenshot-mars3d.png](screenshot-mars3d.png)。
- 原有的 **3D(Cesium)** 页签已做回归验证，仍可正常加载。
- 由于当前开发环境是受限沙箱，WebEngine 只能在 `--single-process` 下测试；正式程序使用多进程
  QtWebEngine，多进程下仍需在目标机器上做一次交互验收（拖拽、框选、图源切换、深色主题）。

### 山体视角

默认的 Cesium/Mars3D 页面都是俯视、无光照、1 倍高程，所以看起来没有区别。后来增加了
两页共用的 `assets/cesium/terrain-visual.js`：

- 垂直夸张 2×（界面可在 1–3× 调整）；
- 低角度斜视，视点取当前选区中心；
- `globe.enableLighting`、天空大气、雾和地面大气；
- “恢复俯视”同时关闭这些效果并回到选区俯视。

秦岭实测截图见 [screenshot-mountain.png](screenshot-mountain.png)。它更接近户外复盘视频里的
山体视角，但仍是**实时 Cesium 地形**：当前 DEM 瓦片为 65×65（Mapzen Skadi / SRTM），
远景山脊会显得块状；要达到影视级效果，需要更高分辨率 DEM、地形晕染/等高线着色，
以及 Blender/Aerialod 之类的离线渲染。

## 4. 已知限制

1. **免费版品牌**：Mars3D 社区版会显示 Logo 和控制台版权；商业去 Logo 需购买授权。
   不能通过 CSS/脚本隐藏后分发，详见 [SDK 授权说明](http://mars3d.cn/docs/advanced/authorization/)。
2. **版本不是最新**：3.7.17 是为了兼容当前 Cesium 1.117 的选择；如果要使用 3.11.x 的新功能，
   需要同时升级 Cesium、重新验证当前 3D 页面和 Chrome 83 兼容性。
3. **体积**：`assets/mars3d/` 约 4.9 MB；打包时会随 `assets` 进入安装包。Cesium 仍按现有逻辑
   首次运行时下载缓存，不新增 Cesium 二进制。
4. **维护双引擎**：Cesium 页与 Mars3D 页使用同一 bridge 协议，但页面逻辑各自维护；
   新增 3D 交互时需要两边同步或明确只在一个引擎实现。

## 5. 建议

- 保持 **Mars3D 为可选实验页**，不要删除 Cesium 3D；两者共用下载/地形/选区数据。
- 如果后续要做 **3D Tiles、城市模型、标绘量算、特效**，优先在 Mars3D 页实现，再评估是否迁移。
- 如果要升级到 Mars3D 3.11.x，单独开分支升级 `mars3d-cesium` 并回归 Cesium 页、地形、Chrome 83 兼容性。
- 商业交付前确认 Mars3D 授权，保留 `assets/mars3d/NOTICE.md` 中的版权信息。
