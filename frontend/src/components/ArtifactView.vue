<template>
  <div class="artifact">
    <div v-if="artifact.kind === 'geojson'" ref="mapEl" class="mini-map"></div>
    <table v-else-if="artifact.kind === 'table'" class="artifact-table">
      <thead>
        <tr>
          <th v-for="c in artifact.data.columns" :key="c">{{ c }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in artifact.data.rows" :key="i">
          <td v-for="c in artifact.data.columns" :key="c">{{ row[c] }}</td>
        </tr>
      </tbody>
    </table>
    <pre v-else class="artifact-json">{{ JSON.stringify(artifact.data, null, 2) }}</pre>
    <div v-if="artifact.name" class="artifact-name">{{ artifact.name }}</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import GeoJSON from 'ol/format/GeoJSON'
import Map from 'ol/Map'
import TileLayer from 'ol/layer/Tile'
import VectorLayer from 'ol/layer/Vector'
import { fromLonLat } from 'ol/proj'
import OSM from 'ol/source/OSM'
import VectorSource from 'ol/source/Vector'
import View from 'ol/View'

const props = defineProps({
  artifact: { type: Object, required: true },
})

const mapEl = ref(null)
let map = null

onMounted(() => {
  if (props.artifact.kind !== 'geojson' || !mapEl.value) return
  const features = new GeoJSON().readFeatures(props.artifact.data, {
    dataProjection: 'EPSG:4326',
    featureProjection: 'EPSG:3857',
  })
  if (!features.length) return

  const source = new VectorSource({ features })
  map = new Map({
    target: mapEl.value,
    layers: [
      new TileLayer({ source: new OSM() }),
      new VectorLayer({ source }),
    ],
    view: new View({ center: fromLonLat([116.4, 39.9]), zoom: 10 }),
    controls: [],
  })
  map.getView().fit(source.getExtent(), { padding: [24, 24, 24, 24], maxZoom: 17 })
})

onBeforeUnmount(() => {
  if (map) {
    map.setTarget(undefined)
    map = null
  }
})
</script>
