"""地图图源定义与请求头。

支持图源：
* 高德地图：GCJ-02 坐标，提供矢量 / 影像 / 路网；
* Google 地图：卫星 / 街道 / 混合 / 地形，大陆道路注记有坐标偏移；
* 天地图：CGCS2000，影像与中文注记，需用户自己的 Key；
* Esri：WGS-84，全球影像与道路、地名参考层。

Google 使用的是网页版地图的公开瓦片端点（非 Maps Platform 正式 API），
可能随 Google 调整而失效；正式或商业用途请改用官方 API。
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random
from urllib.parse import quote

# 现代浏览器 UA，按瓦片坐标轮换，避免单一 UA 请求过于集中。
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
)

AMAP_ATTRIBUTION = "© 高德地图"
GOOGLE_ATTRIBUTION = "© Google"
AMAP_REFERER = "https://www.amap.com/"
GOOGLE_REFERER = "https://www.google.com/maps/"


@dataclass(frozen=True)
class MapProvider:
    """一个 XYZ 瓦片图层。"""

    key: str
    name: str
    source: str
    url_template: str
    subdomains: tuple[str, ...]
    referer: str
    coordinate_system: str
    attribution: str
    description: str = ""
    overlay_templates: tuple[str, ...] = ()
    requires_key: bool = False
    max_zoom: int = 20

    def url(self, zoom: int, col: int, row: int, api_key: str = "") -> str:
        """按瓦片坐标生成下载 URL。"""
        return self.urls(zoom, col, row, api_key)[0]

    def urls(self, zoom: int, col: int, row: int, api_key: str = "") -> tuple[str, ...]:
        """返回底图和透明注记，顺序即合成顺序。"""
        if self.requires_key and not api_key.strip():
            raise ValueError("请先填写天地图 Key，或设置 TIANDITU_KEY 环境变量")
        index = (col * 7 + row * 3 + zoom) % len(self.subdomains)
        values = dict(
            s=self.subdomains[index],
            x=int(col),
            y=int(row),
            z=int(zoom),
            key=quote(api_key.strip(), safe=""),
        )
        return tuple(t.format(**values) for t in (self.url_template,) + self.overlay_templates)


@dataclass(frozen=True)
class MapSource:
    """一组共享坐标系和品牌的瓦片图层。"""

    key: str
    name: str
    coordinate_system: str
    default_provider_key: str
    description: str
    provider_keys: tuple[str, ...]


AMAP_PROVIDERS: tuple[MapProvider, ...] = (
    MapProvider(
        key="vector",
        name="矢量",
        source="amap",
        url_template=(
            "https://webrd0{s}.is.autonavi.com/appmaptile"
            "?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}"
        ),
        subdomains=("1", "2", "3", "4"),
        referer=AMAP_REFERER,
        coordinate_system="GCJ-02",
        attribution=AMAP_ATTRIBUTION,
        description="高德标准矢量底图（含道路与注记）",
    ),
    MapProvider(
        key="satellite",
        name="影像",
        source="amap",
        url_template=(
            "https://webst0{s}.is.autonavi.com/appmaptile"
            "?style=6&x={x}&y={y}&z={z}"
        ),
        subdomains=("1", "2", "3", "4"),
        referer=AMAP_REFERER,
        coordinate_system="GCJ-02",
        attribution=AMAP_ATTRIBUTION,
        description="高德卫星影像底图",
    ),
    MapProvider(
        key="road",
        name="路网",
        source="amap",
        url_template=(
            "https://webst0{s}.is.autonavi.com/appmaptile"
            "?style=8&ltype=3&x={x}&y={y}&z={z}"
        ),
        subdomains=("1", "2", "3", "4"),
        referer=AMAP_REFERER,
        coordinate_system="GCJ-02",
        attribution=AMAP_ATTRIBUTION,
        description="高德路网/注记图层",
    ),
    MapProvider(
        key="amap_hybrid", name="卫星 + 标注", source="amap",
        url_template="https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
        overlay_templates=("https://webst0{s}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}",),
        subdomains=("1", "2", "3", "4"), referer=AMAP_REFERER,
        coordinate_system="GCJ-02", attribution=AMAP_ATTRIBUTION,
        description="推荐国内使用：高德卫星影像叠加同源道路、地名标注；下载和拼接保留标注。",
    ),
)

# Google 的 mt0–mt3 端点实测可用；lyrs 参数：
#   m = 标准街道，s = 卫星，y = 混合（卫星+道路注记），p = 地形
GOOGLE_PROVIDERS: tuple[MapProvider, ...] = (
    MapProvider(
        key="google_street",
        name="街道",
        source="google",
        url_template=(
            "https://mt{s}.google.com/vt/lyrs=m&hl=zh-CN&x={x}&y={y}&z={z}"
        ),
        subdomains=("0", "1", "2", "3"),
        referer=GOOGLE_REFERER,
        coordinate_system="GCJ-02 / WGS-84",
        attribution=GOOGLE_ATTRIBUTION,
        description="Google 街道图；中国大陆道路/注记采用 GCJ-02，不能统一视为 WGS-84。",
    ),
    MapProvider(
        key="google_satellite",
        name="卫星",
        source="google",
        url_template=(
            "https://mt{s}.google.com/vt/lyrs=s&hl=zh-CN&x={x}&y={y}&z={z}"
        ),
        subdomains=("0", "1", "2", "3"),
        referer=GOOGLE_REFERER,
        coordinate_system="WGS-84",
        attribution=GOOGLE_ATTRIBUTION,
        description="Google 卫星影像（WGS-84）",
    ),
    MapProvider(
        key="google_hybrid",
        name="混合",
        source="google",
        url_template=(
            "https://mt{s}.google.com/vt/lyrs=y&hl=zh-CN&x={x}&y={y}&z={z}"
        ),
        subdomains=("0", "1", "2", "3"),
        referer=GOOGLE_REFERER,
        coordinate_system="WGS-84 / GCJ-02",
        attribution=GOOGLE_ATTRIBUTION,
        description="Google 卫星 + 标注；中国大陆影像与道路注记可能错位，建议改用高德或天地图卫星标注。",
    ),
    MapProvider(
        key="google_terrain",
        name="地形",
        source="google",
        url_template=(
            "https://mt{s}.google.com/vt/lyrs=p&hl=zh-CN&x={x}&y={y}&z={z}"
        ),
        subdomains=("0", "1", "2", "3"),
        referer=GOOGLE_REFERER,
        coordinate_system="GCJ-02 / WGS-84",
        attribution=GOOGLE_ATTRIBUTION,
        description="Google 地形图；中国大陆道路/注记采用 GCJ-02，坐标基准随地区变化。",
    ),
)

def _tdt_template(layer: str) -> str:
    return (
        "https://t{s}.tianditu.gov.cn/" + layer + "_w/wmts?SERVICE=WMTS&REQUEST=GetTile"
        "&VERSION=1.0.0&LAYER=" + layer + "&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles"
        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={key}"
    )


TIANDITU_PROVIDERS = tuple(
    MapProvider(
        key=key, name=name, source="tianditu", url_template=_tdt_template("img"),
        overlay_templates=(_tdt_template("cia"),) if hybrid else (),
        subdomains=tuple(str(i) for i in range(8)), referer="https://www.tianditu.gov.cn/",
        coordinate_system="CGCS2000", attribution="© 天地图 · 国家地理信息公共服务平台",
        requires_key=True, max_zoom=18,
        description="天地图影像 + 中文地名注记；需申请自己的 Key，使用墨卡托瓦片。" if hybrid
        else "天地图纯影像；需申请自己的 Key，使用墨卡托瓦片。",
    )
    for key, name, hybrid in (("tianditu_hybrid", "卫星 + 标注", True), ("tianditu_satellite", "卫星影像", False))
)

ESRI_PROVIDERS = tuple(
    MapProvider(
        key=key, name=name, source="esri",
        url_template="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        overlay_templates=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
            "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        ) if hybrid else (),
        subdomains=("0",), referer="https://www.arcgis.com/", coordinate_system="WGS-84",
        attribution="© Esri, Maxar, Earthstar Geographics, GIS User Community", max_zoom=19,
        description="全球影像 + 地名/道路；中文注记较少。旧版道路层预计 2028-03 退役，地名层预计 2029-12 退役。"
        if hybrid else "Esri 全球卫星影像；不同地区清晰度与拍摄时间不同。",
    )
    for key, name, hybrid in (("esri_hybrid", "卫星 + 标注", True), ("esri_satellite", "卫星影像", False))
)

MAP_PROVIDERS: tuple[MapProvider, ...] = AMAP_PROVIDERS + GOOGLE_PROVIDERS + TIANDITU_PROVIDERS + ESRI_PROVIDERS
PROVIDER_MAP: dict[str, MapProvider] = {provider.key: provider for provider in MAP_PROVIDERS}

MAP_SOURCES: tuple[MapSource, ...] = (
    MapSource(
        key="amap",
        name="高德地图",
        coordinate_system="GCJ-02",
        default_provider_key="amap_hybrid",
        description="高德底图，GCJ-02 坐标，国内访问稳定",
        provider_keys=tuple(provider.key for provider in AMAP_PROVIDERS),
    ),
    MapSource(
        key="google",
        name="Google 地图",
        coordinate_system="区域相关",
        default_provider_key="google_satellite",
        description="Google 网页瓦片端点；卫星为 WGS-84，大陆道路注记为 GCJ-02，混合图可能错位",
        provider_keys=tuple(provider.key for provider in GOOGLE_PROVIDERS),
    ),
    MapSource("tianditu", "天地图", "CGCS2000", "tianditu_hybrid",
              "中文影像注记，需填写个人 Key", tuple(p.key for p in TIANDITU_PROVIDERS)),
    MapSource("esri", "Esri 全球影像", "WGS-84", "esri_hybrid",
              "全球卫星与参考注记；中文地名较少，旧注记层有退役计划", tuple(p.key for p in ESRI_PROVIDERS)),
)
SOURCE_MAP: dict[str, MapSource] = {source.key: source for source in MAP_SOURCES}

DEFAULT_SOURCE_KEY = "amap"
DEFAULT_PROVIDER_KEY = "amap_hybrid"

_rng = Random()


def provider_by_key(key: str) -> MapProvider:
    try:
        return PROVIDER_MAP[key]
    except KeyError as exc:
        raise ValueError(f"未知地图类型：{key}") from exc


def source_by_key(key: str) -> MapSource:
    try:
        return SOURCE_MAP[key]
    except KeyError as exc:
        raise ValueError(f"未知地图源：{key}") from exc


def providers_for_source(source_key: str) -> tuple[MapProvider, ...]:
    source = source_by_key(source_key)
    return tuple(PROVIDER_MAP[key] for key in source.provider_keys)


def tile_headers(col: int = 0, row: int = 0, referer: str = AMAP_REFERER) -> dict[str, str]:
    """模拟浏览器的瓦片请求头。"""
    index = abs(int(col) * 31 + int(row) * 17) % len(USER_AGENTS)
    return {
        "User-Agent": USER_AGENTS[index],
        "Referer": referer,
        "Accept": "image/webp,image/png,image/jpeg,image/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }


def random_user_agent() -> str:
    return USER_AGENTS[_rng.randrange(len(USER_AGENTS))]
