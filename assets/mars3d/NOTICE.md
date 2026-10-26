# Mars3D / Turf 第三方说明

本目录包含用于 Mars3D 三维页的第三方前端资源：

- `mars3d.js`、`mars3d.css`、`img/`：**Mars3D 3.7.17**
  - 版权归合肥火星科技有限公司所有
  - Apache License 2.0（见 `LICENSE-Apache-2.0.txt`）
  - npm：https://www.npmjs.com/package/mars3d
  - 本项目使用社区免费版，保留页面 Logo 与控制台版权信息；去除 Logo 的商业授权见
    http://mars3d.cn/docs/advanced/authorization/
  - 本项目已移除 SDK 附带的 Mapbox 访问 token；如自行接入 Mapbox，应配置自己的凭据。
- `turf.min.js`：**Turf.js 6.5.0**
  - MIT License（见 `LICENSE-Turf-MIT.txt`）
  - https://turfjs.org/

Mars3D 的 `peerDependencies` 要求 `mars3d-cesium ^1.117.0`，因此本项目复用运行时缓存的
CesiumJS 1.117，不额外引入 `mars3d-cesium` 包。页面和三维服务由本项目的
`rdmap/terrain_server.py` 仅监听 `127.0.0.1` 提供。

请勿删除或裁剪本目录中的版权/许可说明，也不要移除 Mars3D 的 Logo 或控制台版权信息后分发；
需要去 Logo 时请购买相应商业授权。
