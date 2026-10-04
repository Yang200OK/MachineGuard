import type { AgentEvent } from './types'

export interface RunState { run_id: string; event_sequence: number; status: string; stage: string; progress: number; cancel_requested: boolean }
export interface StreamCallbacks { event: (event: AgentEvent) => void; state: (state: RunState) => void; connection: (message: string) => void }

// 使用带认证头的 fetch 流；令牌不出现在 URL 中，重连只补读持久化序号。
export async function followRun(token: string, id: string, review: boolean, after: number, signal: AbortSignal, callbacks: StreamCallbacks) {
  const origin = new URL(import.meta.env.VITE_API_BASE_URL).origin
  let cursor = after; let retries = 0
  while (!signal.aborted) {
    const connection = new AbortController(); const abort = () => connection.abort()
    signal.addEventListener('abort', abort, { once: true })
    let watchdog: number | undefined; let fatal = false
    const pulse = () => { window.clearTimeout(watchdog); watchdog = window.setTimeout(() => connection.abort(), 15000) }
    try {
      callbacks.connection(retries ? `连接中断，正在从记录 ${cursor} 补读进度…` : '正在连接进度…'); pulse()
      const response = await fetch(`${origin}/api/${review ? 'reviews' : 'agent/runs'}/${encodeURIComponent(id)}/stream?after=${cursor}`, {
        headers: { Authorization: `Bearer ${token}`, 'Last-Event-ID': String(cursor) }, signal: connection.signal, cache: 'no-store',
      })
      if (!response.ok) {
        const error = await response.json() as { error: { message: string } }
        fatal = true; throw new Error(error.error.message)
      }
      if (!response.body || !response.headers.get('content-type')?.startsWith('text/event-stream')) { fatal = true; throw new Error('进度响应格式无效。') }
      callbacks.connection('进度已连接'); retries = 0
      const reader = response.body.getReader(); const decoder = new TextDecoder('utf-8', { fatal: true }); let buffer = ''; let reconnect = false
      try {
        while (!signal.aborted) {
          const chunk = await reader.read(); pulse()
          if (chunk.done) break
          buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, '\n')
          let boundary: number
          while ((boundary = buffer.indexOf('\n\n')) >= 0) {
            const lines = buffer.slice(0, boundary).split('\n'); buffer = buffer.slice(boundary + 2)
            const field = (name: string) => lines.filter(line => line.startsWith(`${name}:`)).map(line => line.slice(name.length + 1).replace(/^ /, '')).join('\n')
            const data = field('data'); if (!data) continue
            let value: unknown
            try { value = JSON.parse(data) } catch { fatal = true; throw new Error('进度数据损坏，请刷新核对。') }
            const kind = field('event')
            if (kind === 'run-event') {
              const event = value as AgentEvent; const sequence = Number(field('id'))
              if (!Number.isInteger(sequence) || sequence !== event.sequence || sequence > cursor + 1) { fatal = true; throw new Error('进度序号不连续，请刷新核对。') }
              if (sequence <= cursor) continue
              cursor = sequence; callbacks.event(event)
            } else if (kind === 'state') callbacks.state(value as RunState)
            else if (kind === 'done') { callbacks.state(value as RunState); callbacks.connection('进度已完成'); return }
            else if (kind === 'reconnect') reconnect = true
            else if (kind === 'error') { fatal = true; throw new Error((value as { message: string }).message) }
          }
          if (buffer.length > 2_000_000) { fatal = true; throw new Error('进度帧超出大小限制。') }
        }
        if (signal.aborted) return
        if (!reconnect) throw new Error('进度连接中断。')
      } finally { await reader.cancel().catch(() => undefined) }
    } catch (error) {
      if (signal.aborted) return
      if (fatal || error instanceof TypeError && error.message.includes('encoded data')) {
        callbacks.connection(`进度连接已停止：${error instanceof Error ? error.message : '请刷新核对。'}`); return
      }
      retries += 1; callbacks.connection(`连接中断，正在从记录 ${cursor} 补读进度…`)
      await new Promise<void>(resolve => {
        const done = () => { window.clearTimeout(wait); signal.removeEventListener('abort', done); resolve() }
        const wait = window.setTimeout(done, Math.min(5000, 500 * 2 ** Math.min(retries, 4)))
        signal.addEventListener('abort', done, { once: true }); if (signal.aborted) done()
      })
    } finally { window.clearTimeout(watchdog); connection.abort(); signal.removeEventListener('abort', abort) }
  }
}
