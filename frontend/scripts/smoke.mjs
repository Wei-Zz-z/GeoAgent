// 端到端冒烟脚本：模拟前端通过 Vite 代理与后端完成一次真实对话。
// 前置条件：后端(uvicorn :8000)与 vite dev(:5173)均已启动，且 .env 已配置千问 key。
// 用法：node scripts/smoke.mjs

const BASE = 'http://localhost:5173/api'

async function main() {
  const models = await (await fetch(`${BASE}/models`)).json()
  console.log(
    'models:',
    models.models.map((m) => `${m.id}${m.available ? '' : '(no key)'}`).join(', '),
  )

  const conv = await (
    await fetch(`${BASE}/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'smoke', model: 'qwen-flash' }),
    })
  ).json()
  console.log('conversation:', conv.id, conv.model)

  await new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://localhost:5173/api/conversations/${conv.id}/ws`)
    const artifacts = []
    const timer = setTimeout(() => reject(new Error('timeout')), 60000)

    ws.onopen = () => ws.send(JSON.stringify({ type: 'user', content: '给国贸做一个 10 公里的缓冲区' }))
    ws.onmessage = (msg) => {
      const event = JSON.parse(msg.data)
      if (event.type === 'route') console.log('route ->', event.target)
      if (event.type === 'tool_call') console.log('tool_call:', event.name, JSON.stringify(event.arguments))
      if (event.type === 'tool_result') console.log('tool_result:', event.name, 'error=', event.is_error)
      if (event.type === 'artifact') artifacts.push(`${event.name}:${event.kind}`)
      if (event.type === 'message') console.log('final message:', event.content)
      if (event.type === 'error') console.log('error:', event.message)
      if (event.type === 'turn_end') {
        console.log('artifacts:', artifacts.join(', ') || '(none)')
        clearTimeout(timer)
        ws.close()
        resolve()
      }
    }
    ws.onerror = (err) => {
      clearTimeout(timer)
      reject(err)
    }
  })

  const msgs = await (await fetch(`${BASE}/conversations/${conv.id}/messages`)).json()
  console.log('persisted roles:', msgs.messages.map((m) => m.role).join(', '))
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err)
    process.exit(1)
  })
