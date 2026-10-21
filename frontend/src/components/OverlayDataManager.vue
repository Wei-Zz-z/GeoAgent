<template>
  <section class="overlay-manager">
    <div class="overlay-manager-head">
      <h3>套合数据管理</h3>
      <div class="overlay-manager-actions">
        <span v-if="!backupAvailable" class="overlay-warn">未找到 pg_dump，删除前无法自动备份</span>
        <button class="ghost-btn" type="button" :disabled="busy" @click="load">刷新</button>
      </div>
    </div>
    <p class="overlay-note">
      删除会同时移除该期次的登记，对话页的“数据表”下拉框随即更新；默认先备份到服务器本地文件再删表。
    </p>
    <p v-if="error" class="overlay-error" role="alert">{{ error }}</p>

    <table v-if="tables.length" class="overlay-summary">
      <thead>
        <tr>
          <th>期次</th><th>表名</th><th>行数</th><th>面积（亩）</th><th>几何</th><th>创建时间</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="item in tables" :key="item.table_name">
          <tr>
            <td>
              <span v-if="item.label">{{ item.label }}</span>
              <span v-else class="overlay-muted">未登记</span>
              <span v-if="item.is_default" class="overlay-tag">默认期次</span>
            </td>
            <td class="overlay-mono">{{ item.table_name }}</td>
            <td>{{ item.rows.toLocaleString() }}</td>
            <td>{{ item.area_mu.toLocaleString() }}</td>
            <td>{{ item.with_geom.toLocaleString() }}</td>
            <td class="overlay-mono">{{ item.created_at ? item.created_at.slice(0, 19) : '—' }}</td>
            <td>
              <button class="danger-btn" type="button" :disabled="busy" @click="ask(item)">删除</button>
            </td>
          </tr>
          <tr v-if="pending === item.table_name" class="overlay-confirm-row">
            <td colspan="7">
              删除 <strong>{{ item.table_ref }}</strong>（{{ item.rows.toLocaleString() }} 行）不可恢复，请输入表名确认：
              <input v-model.trim="confirmText" :placeholder="item.table_name" class="overlay-confirm-input" />
              <label class="overlay-check">
                <input v-model="backup" type="checkbox" :disabled="!backupAvailable" /> 删除前备份
              </label>
              <button class="danger-btn" type="button" :disabled="busy || !confirmed" @click="remove(item)">确认删除</button>
              <button class="ghost-btn" type="button" :disabled="busy" @click="cancel">取消</button>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <p v-else class="overlay-note">还没有套合表。</p>

    <p v-if="message" class="overlay-ok">{{ message }}</p>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { chat } from '../stores/chat'

const tables = ref([])
const pending = ref('')
const confirmText = ref('')
const backup = ref(true)
const backupAvailable = ref(true)
const busy = ref(false)
const error = ref('')
const message = ref('')

const confirmed = computed(() => {
  const target = tables.value.find((item) => item.table_name === pending.value)
  return Boolean(target) && confirmText.value.toLowerCase() === target.table_name.toLowerCase()
})

async function load() {
  error.value = ''
  try {
    const response = await fetch('/api/overlay/tables')
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '读取套合表失败')
    tables.value = data.tables || []
    backupAvailable.value = Boolean(data.backup_available)
    if (!backupAvailable.value) backup.value = false
  } catch (err) {
    error.value = err.message
  }
}

function ask(item) {
  pending.value = item.table_name
  confirmText.value = ''
  message.value = ''
  error.value = ''
}

function cancel() {
  pending.value = ''
  confirmText.value = ''
}

async function remove(item) {
  if (!confirmed.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await fetch('/api/overlay/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dataset_id: item.dataset_id || '',
        table_name: item.table_name,
        confirm: confirmText.value,
        backup: backup.value,
      }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '删除失败')
    const parts = [`已删除 ${data.table_ref}`]
    if (data.unregistered) parts.push('已移除期次登记')
    if (data.backup) parts.push(`备份：${data.backup}`)
    message.value = parts.join('；')
    cancel()
    await load()
    if (typeof chat.refreshDatasets === 'function') await chat.refreshDatasets()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

onMounted(load)
defineExpose({ load })
</script>