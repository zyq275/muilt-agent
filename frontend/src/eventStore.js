/**
 * 事件流的本地持久化
 *
 * 【为什么需要它】事件的唯一来源是 WebSocket 的实时推送。api/server.py 只有
 * 四个 REST 路由和一个 WS 端点，**没有任何"查询历史事件"的接口**；output/
 * session_{tid}/ 里也只落产物文件（.md/.pdf），不写事件日志。所以页面一刷新，
 * 之前收到的执行过程就彻底没了——除非前端自己存下来。
 *
 * 这里用 localStorage 存一份按会话分组的事件副本。三条边界必须记住：
 *   1. 这**不是服务端日志**，只是本浏览器的副本。换浏览器、清缓存就没了；
 *   2. 只记录**本浏览器开着页面时收到**的事件。页面没开的那段时间发生的事，
 *      不会补回来（WebSocket 不重放）；
 *   3. 存的是原始事件对象，含 task_result 里的完整报告正文，所以有配额压力，
 *      下面用条数上限 + 配额失败降级来兜住。
 */

const KEY = 'tanyuan.events.v1'

/** 每个会话最多留多少条事件。超出后**保留尾部**——最新的才是用户要看的 */
const MAX_EVENTS_PER_THREAD = 300

/** 最多为多少个会话保存事件，超出丢弃最久未更新的 */
const MAX_THREADS = 20

/**
 * 读取全部会话的事件。
 *
 * 数据可能被用户手工改坏或由旧版本写入，解析失败一律返回空对象并清掉脏数据，
 * 不能让一份坏数据把整个界面卡住。
 *
 * @returns {Record<string, Array>} threadId → 事件数组
 */
export function loadAllEvents() {
  let raw
  try {
    raw = localStorage.getItem(KEY)
  } catch {
    // 隐私模式等场景下 localStorage 可能直接抛异常
    return {}
  }
  if (!raw) return {}

  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('结构不对')
    const out = {}
    for (const [tid, list] of Object.entries(parsed)) {
      if (typeof tid === 'string' && tid && Array.isArray(list)) out[tid] = list
    }
    return out
  } catch {
    try {
      localStorage.removeItem(KEY)
    } catch {
      /* 清不掉也无所谓，下次读取还会走到这里 */
    }
    return {}
  }
}

/**
 * 写入全部会话的事件。
 *
 * 配额是这里的主要风险：报告正文动辄十几 KB，几十个会话很容易顶到 5MB。
 * 所以失败时**逐级降级**而不是直接放弃：先砍会话数，再砍单会话条数，最后
 * 至少保证当前会话能存进去——用户最关心的就是他正在看的这一个。
 *
 * @param {Record<string, Array>} map threadId → 事件数组
 * @param {string} [keepThreadId] 优先保留的会话（通常是当前会话）
 * @returns {boolean} 是否完整写入
 */
export function saveAllEvents(map, keepThreadId = '') {
  /** 取每个会话最新的若干条 */
  const trim = (list, n) => (Array.isArray(list) ? list.slice(-n) : [])

  /** 按"最后一条事件的时间"给会话排序，新的在前 */
  const ordered = Object.entries(map)
    .map(([tid, list]) => {
      const arr = Array.isArray(list) ? list : []
      const last = arr[arr.length - 1]
      const ts = Date.parse(last?.timestamp || '') || 0
      return { tid, list: arr, ts }
    })
    .sort((a, b) => b.ts - a.ts)

  // 逐级收缩，越往后越狠；最后一级只留当前会话
  const plans = [
    { threads: MAX_THREADS, events: MAX_EVENTS_PER_THREAD },
    { threads: 10, events: 150 },
    { threads: 5, events: 80 },
    { threads: 1, events: 50 },
  ]

  for (const plan of plans) {
    const picked = ordered.slice(0, plan.threads)
    // 当前会话无论排第几都必须在里面，否则用户一刷新就丢
    if (keepThreadId && !picked.some((p) => p.tid === keepThreadId) && map[keepThreadId]) {
      picked.unshift({ tid: keepThreadId, list: map[keepThreadId], ts: Date.now() })
    }
    const payload = {}
    for (const p of picked) {
      const arr = trim(p.list, plan.events)
      if (arr.length) payload[p.tid] = arr
    }
    try {
      localStorage.setItem(KEY, JSON.stringify(payload))
      return true
    } catch {
      // 配额满 / 被禁用，换更小的方案再试
    }
  }

  // 全都不行就放弃本次写入：存不下不影响主流程（任务照跑、事件照收），
  // 只是这次刷新后日志会缺一段
  return false
}

/**
 * 删除某会话的事件副本。
 *
 * 注意：只删浏览器里这份，**服务端没有任何东西可删**（本来就没存事件）。
 *
 * @param {Record<string, Array>} map 当前内存中的全量事件
 * @param {string} threadId 会话标识
 * @returns {Record<string, Array>} 删完的新对象
 */
export function dropEvents(map, threadId) {
  const next = { ...map }
  delete next[threadId]
  return next
}
