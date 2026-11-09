// 地图底图配置：天地图 WMTS（矢量底图 vec_c + 中文注记 cva_c）。
//
// 坐标系（实测确认，重要）：天地图 `vec_c` 的 WMTS 矩阵集 `c` 是**经纬度网格**
// （能力文档：SupportedCRS=EPSG:4490、TopLeftCorner=90/-180、第 1 级 2×1 瓦片），
// 不是球面墨卡托网格。因此：
//   - 地图 View 用 EPSG:4326（经纬度），GeoJSON 也按 4326 直接读入，不做重投影；
//   - 瓦片分辨率 = 360 / 256 / 2^z 度/像素，视图 zoom 与天地图瓦片级号一一对应。
// 若按 Web Mercator 建网格，请求会落到南极附近（返回空白瓦片），底图与数据就对不上了。
//
// 密钥：优先取前端环境变量 VITE_TIANDITU_TOKEN（frontend/.env），否则用内置 key。
// 注意：浏览器端密钥一定会打进前端产物，请在天地图控制台按访问域名做白名单限制。
// 底图只使用天地图：瓦片加载失败时在控制台告警，地图容器底色（浅灰）透出来。
import TileLayer from 'ol/layer/Tile'
import { getTopLeft } from 'ol/extent'
import { get as getProjection } from 'ol/proj'
import WMTS from 'ol/source/WMTS'
import WMTSTileGrid from 'ol/tilegrid/WMTS'

const TIANDITU_TOKEN = import.meta.env.VITE_TIANDITU_TOKEN || 'd880f2cd5a09225329942c8916884151'
// 天地图同一密钥支持 t0-t7 多子域，轮询可绕过浏览器的单域名并发限制；只想用 t0 时删掉其余即可。
const TIANDITU_HOSTS = ['t0', 't1', 't2', 't3', 't4', 't5', 't6', 't7']
export const MAP_PROJECTION = 'EPSG:4326'
export const MAP_MAX_ZOOM = 18
// 底图与数据统一在经纬度下工作，View 也用这个范围约束
export const MAP_EXTENT = [-180, -90, 180, 90]
// 第 1 级：2×1 瓦片覆盖全球 → 每像素 360/256/2 度
const LEVEL1_RESOLUTION = 360 / 256 / 2

// 页面用 https 时底图也要走 https，否则浏览器按混合内容拦截
const PROTOCOL = typeof location !== 'undefined' && location.protocol === 'https:' ? 'https' : 'http'

function createTileGrid() {
  const projection = getProjection(MAP_PROJECTION)
  const resolutions = []
  const matrixIds = []
  // 天地图矩阵集从第 1 级开始；holes[0] 不用，视图 minZoom 固定为 1
  for (let z = 1; z <= MAP_MAX_ZOOM; z += 1) {
    resolutions[z] = LEVEL1_RESOLUTION / 2 ** (z - 1)
    matrixIds[z] = String(z)
  }
  return new WMTSTileGrid({
    origin: getTopLeft(projection.getExtent()), // 经纬度网格左上角 = (90, -180)
    resolutions,
    matrixIds,
  })
}

function tiandituSource(layer) {
  return new WMTS({
    urls: TIANDITU_HOSTS.map(
      (host) => `${PROTOCOL}://${host}.tianditu.gov.cn/${layer}_c/wmts?tk=${TIANDITU_TOKEN}`,
    ),
    layer,
    matrixSet: 'c',
    format: 'tiles',
    style: 'default',
    projection: MAP_PROJECTION,
    tileGrid: createTileGrid(),
    wrapX: true,
  })
}

/**
 * 生成底图图层：天地图矢量底图 + 中文注记。
 * @returns {import('ol/layer/Base').default[]} [底图, 注记（可选）]
 */
export function createBaseLayers({ withLabels = true } = {}) {
  const baseLayer = new TileLayer({ source: tiandituSource('vec'), zIndex: 0 })
  const labelLayer = withLabels ? new TileLayer({ source: tiandituSource('cva'), zIndex: 1 }) : null
  let warned = false
  const warnOnce = () => {
    if (warned) return
    warned = true
    console.warn(
      '[map] 天地图底图瓦片加载失败：请检查密钥（VITE_TIANDITU_TOKEN）与网络连通性',
    )
  }
  baseLayer.getSource().on('tileloaderror', warnOnce)
  labelLayer?.getSource().on('tileloaderror', warnOnce)
  return labelLayer ? [baseLayer, labelLayer] : [baseLayer]
}