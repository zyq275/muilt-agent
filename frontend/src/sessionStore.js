/**
 * 会话索引（浏览器本地维护）
 *
 * 为什么存在这里：后端**没有"列出所有会话"的接口**。api/server.py 只有四个
 * REST 路由（/api/task、/api/upload、/api/files、/api/download）加一个 WS，
 * 没有任何一个能枚举历史会话、删除会话或查询任务状态。
 *
 * 所以这份列表完全存在浏览器 localStorage 里，记录的是**本浏览器提交过的任务**，
 * 不是服务端全量会话。由此产生三条边界，界面上必须如实体现，不能让用户误以为
 * 这是服务端的会话总账：
 *
 *   1. 换浏览器或清缓存后列表为空 —— 是这份索引没了，不是会话产物丢了；
 *   2. output/ 下真实存在的产物目录，若不由本浏览器提交，不会出现在列表里；
 *   3. "删除"只删本地这一条记录，**不会删除服务端的会话产物** —— 后端没有删除
 *      接口，做不到。UI 上必须写明，否则用户会以为文件被清掉了。
 */

const STORAGE_KEY = 'tanyuan.sessions.v1'

// 本地索引的条数上限。localStorage 有配额，且这是个便利索引而非权威账本，
// 超出就丢掉最旧的，避免长期使用后把配额撑满导致写入抛异常。
const MAX_SESSIONS = 50

/** 会话状态取值，与界面上的状态标签一一对应 */
export const STATUS = {
  RUNNING: 'running',
  DONE: 'done',
  ERROR: 'error',
  /** 用户主动终止。与 ERROR 分开：这不是失败，是用户的选择 */
  CANCELLED: 'cancelled',
}

/** 状态 → 中文标签 */
export const STATUS_LABEL = {
  [STATUS.RUNNING]: '运行中',
  [STATUS.DONE]: '已完成',
  [STATUS.ERROR]: '失败',
  [STATUS.CANCELLED]: '已终止',
}

/**
 * 读取全部会话，按创建时间倒序（最新的在最前）。
 *
 * localStorage 里的内容可能被用户手工改坏或被旧版本写入，因此解析失败一律
 * 当作空列表并清掉脏数据，不能让一条坏记录把整个界面卡死。
 *
 * @returns {Array<{threadId, query, title, status, createdAt, updatedAt}>}
 */
export function listSessions() {
  let raw
  try {
    raw = localStorage.getItem(STORAGE_KEY)
  } catch {
    // 隐私模式等场景下 localStorage 可能直接抛异常
    return []
  }
  if (!raw) return []

  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) throw new Error('不是数组')
    // 逐条校验必备字段，剔掉残缺记录
    return parsed
      .filter((s) => s && typeof s.threadId === 'string' && s.threadId)
      .sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
  } catch {
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* 清不掉也没关系，下次读取还会走到这里 */
    }
    return []
  }
}

/**
 * 写回全部会话（内部用，外面请走下面几个语义化函数）。
 *
 * @param {Array} list 会话数组
 */
function writeAll(list) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MAX_SESSIONS)))
  } catch {
    // 配额满或被禁用：索引写不进去不影响主流程（任务照跑、事件照收），
    // 只是这次刷新后列表里会少一条，不值得为此打断用户。
  }
}

/**
 * 从 query 生成任务摘要。
 *
 * 取首行并截断——用户可能贴进来一大段带换行的需求，整段塞进侧边栏会把卡片撑爆。
 *
 * @param {string} query 原始任务描述
 * @returns {string} 单行摘要
 */
export function summarize(query) {
  const firstLine = String(query || '').split('\n').find((l) => l.trim()) || ''
  const text = firstLine.trim()
  return text.length > 40 ? `${text.slice(0, 40)}…` : text || '（无描述）'
}

/**
 * 新增一条会话记录。
 *
 * @param {string} threadId 会话标识
 * @param {string} query 任务描述
 * @returns {object} 写入的记录
 */
export function addSession(threadId, query) {
  const now = Date.now()
  const record = {
    threadId,
    query,
    title: summarize(query),
    status: STATUS.RUNNING,
    createdAt: now,
    updatedAt: now,
  }
  const list = listSessions().filter((s) => s.threadId !== threadId)
  list.unshift(record)
  writeAll(list)
  return record
}

/**
 * 更新某会话的状态。
 *
 * @param {string} threadId 会话标识
 * @param {string} status STATUS 之一
 */
export function updateStatus(threadId, status) {
  const list = listSessions()
  const target = list.find((s) => s.threadId === threadId)
  if (!target) return
  target.status = status
  target.updatedAt = Date.now()
  writeAll(list)
}

/**
 * 删除本地会话记录。
 *
 * 注意：**只删这条索引，不删服务端产物** —— 后端没有删除接口。
 * 调用方必须在 UI 上把这一点告诉用户。
 *
 * @param {string} threadId 会话标识
 */
export function removeSession(threadId) {
  writeAll(listSessions().filter((s) => s.threadId !== threadId))
}

/**
 * 按状态统计会话数量（供侧边栏统计卡片使用）。
 *
 * @param {Array} [list] 会话数组；不传则现取
 * @returns {{running: number, done: number, error: number, total: number}}
 */
export function computeStats(list = listSessions()) {
  const stats = { running: 0, done: 0, error: 0, total: list.length }
  for (const s of list) {
    if (s.status in stats) stats[s.status] += 1
  }
  return stats
}
