/**
 * 接口封装 — TDD §8.1 / §8.2
 *
 * REST 走 fetch，WebSocket 用原生 WebSocket。
 *
 * 全部使用**相对路径**（/api/...、/ws/...）：
 *   - 开发期由 vite.config.js 的代理转发到 localhost:8000；
 *   - 生产期 npm run build 后由 FastAPI 静态托管，与 API 同源。
 * 两种情况都不需要在前端写死后端地址，也不必处理跨域。
 *
 * 注意 /api/files 与 /api/download 的 path 参数是**绝对路径**，且必须落在
 * 后端结果目录内，否则返回 403。列表接口返回的每一项自带绝对路径 path，
 * 前端应直接把它回传，不要自己拼接。
 */

/** 统一的错误构造：尽量把后端 {"error"/"detail": ...} 里的说明带出来 */
async function toError(resp) {
  let detail = ''
  try {
    const body = await resp.json()
    detail = body.detail || body.error || JSON.stringify(body)
  } catch {
    detail = await resp.text().catch(() => '')
  }
  return new Error(`HTTP ${resp.status}${detail ? `：${detail}` : ''}`)
}

/**
 * 提交任务（POST /api/task）
 *
 * 后端用 asyncio.create_task 后台执行，立即返回，不等任务跑完。
 * 因此这个请求会很快回来，真正的进度要从 WebSocket 上收。
 *
 * @param {string} query 任务描述
 * @param {string|null} threadId 会话标识；null 表示让后端新建
 * @returns {Promise<{status: string, thread_id: string}>}
 */
export async function submitTask(query, threadId = null) {
  const resp = await fetch('/api/task', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, thread_id: threadId || null }),
  })
  if (!resp.ok) throw await toError(resp)
  return resp.json()
}

// 注：终止任务（POST /api/cancel）的前端封装已随按钮一起移除。
// 后端路由仍保留在 api/server.py，需要时按上面的 submitTask 照抄即可。

/**
 * 上传文件（POST /api/upload，multipart）
 *
 * 后端字段名是 files（可重复）与 thread_id，均为必填。
 * 文件会落到 upload/session_{thread_id}/ 下。
 *
 * @param {string} threadId 会话标识
 * @param {FileList|File[]} files 待上传文件
 * @returns {Promise<{status: string, files: string[]}>} files 是保存后的文件名
 */
export async function uploadFiles(threadId, files) {
  const form = new FormData()
  form.append('thread_id', threadId)
  for (const f of files) {
    // 同名重复 append 即构成后端的 list[UploadFile]
    form.append('files', f)
  }
  // 不要手工设置 Content-Type：浏览器需要自己补 multipart 的 boundary
  const resp = await fetch('/api/upload', { method: 'POST', body: form })
  if (!resp.ok) throw await toError(resp)
  return resp.json()
}

/**
 * 列出目录内容（GET /api/files），按 mtime 倒序。
 *
 * @param {string} path 结果目录内的绝对路径；空字符串表示结果目录根
 * @returns {Promise<Array<{name,type,path,size,mtime}>>}
 */
export async function listFiles(path = '') {
  const qs = path ? `?path=${encodeURIComponent(path)}` : ''
  const resp = await fetch(`/api/files${qs}`)
  if (!resp.ok) throw await toError(resp)
  const body = await resp.json()
  return body.files || []
}

/**
 * 拼下载链接（GET /api/download）。
 *
 * 返回 URL 而不是直接触发下载：交给 <a href> 更省事，也让浏览器自己
 * 处理文件名与断点续传。注意 path 必须是列表接口给出的绝对路径。
 */
export function downloadUrl(path) {
  return `/api/download?path=${encodeURIComponent(path)}`
}

/**
 * 建立会话事件 WebSocket（WS /ws/{thread_id}）。
 *
 * 后端会推送两类消息，靠 type 区分：
 *   - {type:"pong"}                    心跳应答（我们主动发文本时）
 *   - {type:"ping"}                    服务端每 30s 的主动心跳
 *   - {type:"monitor_event", event, message, data, timestamp}
 *                                      编排层事件，见 TDD §8.2
 *
 * @param {string} threadId 会话标识，必须与提交任务时的一致
 * @param {object} handlers
 * @param {(msg: object) => void} handlers.onMessage 收到任意消息时调用
 * @param {() => void} [handlers.onOpen]
 * @param {(ev: CloseEvent) => void} [handlers.onClose]
 * @param {(ev: Event) => void} [handlers.onError]
 * @returns {WebSocket}
 */
export function createEventSocket(threadId, handlers = {}) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const ws = new WebSocket(`${proto}//${location.host}/ws/${encodeURIComponent(threadId)}`)

  ws.onopen = () => handlers.onOpen?.()
  ws.onclose = (ev) => handlers.onClose?.(ev)
  ws.onerror = (ev) => handlers.onError?.(ev)
  ws.onmessage = (ev) => {
    let msg
    try {
      msg = JSON.parse(ev.data)
    } catch {
      // 后端只会发 JSON；解析不了就原样透传，免得静默丢消息
      handlers.onMessage?.({ type: 'raw', message: ev.data })
      return
    }
    handlers.onMessage?.(msg)
  }

  return ws
}
