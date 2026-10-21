<template>
  <Teleport to="body">
    <div v-if="open" class="modal-mask" @mousedown.self="emit('close')">
      <section class="modal-panel" :style="{ width }" role="dialog" aria-modal="true">
        <header class="modal-head">
          <div>
            <h2 class="modal-title">{{ title }}</h2>
            <p v-if="subtitle" class="modal-sub">{{ subtitle }}</p>
          </div>
          <button class="icon-btn modal-close" type="button" title="关闭（Esc）" @click="emit('close')">
            <Icon name="x" :size="18" />
          </button>
        </header>
        <div class="modal-body">
          <slot />
        </div>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { onBeforeUnmount, watch } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  width: { type: String, default: 'min(1280px, 94vw)' },
})
const emit = defineEmits(['close'])

// Esc 关闭；打开时锁住背景滚动，避免弹窗内滚动带动整页。
function onKey(event) {
  if (event.key === 'Escape') emit('close')
}

watch(
  () => props.open,
  (open) => {
    document.body.style.overflow = open ? 'hidden' : ''
    if (open) document.addEventListener('keydown', onKey)
    else document.removeEventListener('keydown', onKey)
  },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>