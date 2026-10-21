// 入口验证：省域平台跳转带的 ticket → 本系统登录态（HttpOnly Cookie）
//
// 省域平台的 ticket 只能使用一次、2 分钟过期，因此流程是：
// 前端取参数 → 交给后端换取登录态 → 立刻抹掉 URL 上的 ticket → 之后靠 Cookie。
import { reactive } from 'vue'
import { api, setUnauthorizedHandler } from './api/client'

// 省域平台要求参数名用 sykjzlptv2_ticket，避免与其他对接的 ticket 重复
const TICKET_PARAM = import.meta.env.VITE_TICKET_PARAM || 'sykjzlptv2_ticket'

export const auth = reactive({
  status: 'idle', // idle | checking | ready | blocked
  mode: 'dev', // dev（未开启入口验证）| ticket（省域平台）
  user: null,
  error: '',
  ready: false,
})

let pending = null

// 平台跳转可能把 ticket 放在查询串，也可能放在 # 之后的片段里（接入文档示例是后者）
function readTicket() {
  for (const raw of [window.location.search, window.location.hash]) {
    if (!raw || !raw.includes('=')) continue
    const query = raw.startsWith('#') ? raw.slice(raw.indexOf('?') + 1) : raw
    const value = new URLSearchParams(query).get(TICKET_PARAM)
    if (value) return value
  }
  return ''
}

// 换到登录态后立刻把 ticket 从地址栏去掉：避免刷新/复制链接重放一次性票据
function stripTicketFromUrl() {
  const url = new URL(window.location.href)
  let changed = false
  const search = new URLSearchParams(url.search)
  if (search.has(TICKET_PARAM)) {
    search.delete(TICKET_PARAM)
    url.search = search.toString()
    changed = true
  }
  const hash = url.hash
  const index = hash.indexOf('?')
  if (index >= 0) {
    const params = new URLSearchParams(hash.slice(index + 1))
    if (params.has(TICKET_PARAM)) {
      params.delete(TICKET_PARAM)
      const rest = params.toString()
      url.hash = hash.slice(0, index) + (rest ? `?${rest}` : '')
      changed = true
    }
  }
  if (changed) window.history.replaceState(window.history.state, '', url.toString())
}

function apply(payload) {
  auth.user = payload.user || null
  auth.mode = payload.auth_mode || 'dev'
  auth.status = 'ready'
  auth.error = ''
  auth.ready = true
}

function block(message) {
  auth.user = null
  auth.ready = false
  auth.status = 'blocked'
  auth.error = message || '登录失败，请从省域平台重新进入。'
}

async function ensure() {
  if (auth.ready) return auth
  if (pending) return pending
  pending = (async () => {
    auth.status = 'checking'
    auth.error = ''
    const ticket = readTicket()
    try {
      if (!ticket) {
        apply(await api.me())
      } else {
        try {
          apply(await api.loginWithTicket(ticket))
        } catch (err) {
          // 刷新页面会重放已消费的 ticket：本机登录态还有效就直接放行
          const existing = await api.me().catch(() => null)
          if (!existing || !existing.user) throw err
          apply(existing)
        }
      }
      stripTicketFromUrl()
    } catch (err) {
      block(err.message)
    } finally {
      pending = null
    }
    return auth
  })()
  return pending
}

// 退出登录：服务端清 Cookie（凭据本身无状态，客户端删除即视为退出）
async function logout() {
  try {
    await api.logout()
  } catch {
    // 忽略：本地状态清掉即可
  }
  block('已退出登录，请从省域平台重新进入。')
}

// 会话过期：任意 /api 返回 401 时回到入口页
setUnauthorizedHandler(() => {
  if (!auth.ready) return
  block('登录已过期，请从省域平台重新进入。')
})

auth.ensure = ensure
auth.logout = logout
