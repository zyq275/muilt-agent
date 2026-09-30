/**
 * 反思校验的前端复算（H1–H4）
 *
 * 为什么要在前端重算一遍：后端的 hard_check()（tools/reflect_check.py）每轮都
 * 执行，但**结果只写进 logger，从不通过 WebSocket 推送**。事件流里能拿到的只有
 * task_result 里那篇报告正文，没有任何一条说"H1 不达标"。
 *
 * 所以这里用**同一套算法**在前端从报告正文复算四项指标。算法逐条对齐
 * tools/reflect_check.py，包括正则和关键词表，避免两边给出不一致的结论。
 *
 * 必须说清的边界：
 *   - 这是**事后复算**，不是后端那一轮真实校验结果的回放。后端可能因为"报告
 *     文件"和"最终正文"的取值差异得出不同结论（后端 _collect_report_text 优先
 *     读工作目录里最新的 .md，前端只能拿到 task_result 的 result 字段）。
 *   - H4 依赖"本轮实际调用了哪些数据源"。后端是从 tool_call 里统计的；
 *     前端只能从 assistant_call 事件数子智能体被调度了几次——子智能体**内部**
 *     的工具调用不推送，所以两者可能不一致。
 *   - 校验结果仅作可视化参考，**不参与后端的纠错循环**。前端改不动后端的判定。
 */

/** H1：报告最少字数（对齐 reflect_check.MIN_WORD_COUNT） */
export const MIN_WORD_COUNT = 1000

/** H2：占位符关键词（对齐 reflect_check.PLACEHOLDER_KEYWORDS） */
export const PLACEHOLDER_KEYWORDS = [
  '待补充',
  'TODO',
  '待办',
  '等待子任务',
  '等待子任务完成',
  '占位',
  '（略）',
  '(略)',
  '待完善',
  '此处省略',
  'XXX',
]

/** H4：数据源标识 → 展示名（对齐 reflect_check.SOURCE_LABELS） */
export const SOURCE_LABELS = {
  web_search: '网络公开信息',
  db_query: '企业结构化业务数据',
  knowledge: '企业内部知识文档',
}

/**
 * 子智能体展示名 → 数据源标识。
 *
 * 对齐 agent/main_agent.py 的 _NAME_TO_SOURCE（由 sub_agents_config 反查），
 * 名字取自 prompt/prompts.yaml。后端换了子智能体名字这里要同步改。
 */
export const SUBAGENT_TO_SOURCE = {
  网络检索助手: 'web_search',
  数据库查询助手: 'db_query',
  内部知识库助手: 'knowledge',
}

/** 三个子智能体的展示名，供分组与配色使用 */
export const SUBAGENT_NAMES = Object.keys(SUBAGENT_TO_SOURCE)

/**
 * 统计报告有效字数。
 *
 * 逐字对齐 reflect_check.count_report_words：先去 Markdown 标记符号，再去所有
 * 空白，然后数字符。中文按 1 字、英文按 1 字符计。
 *
 * @param {string} text 报告全文
 * @returns {number} 有效字数
 */
export function countReportWords(text) {
  if (!text) return 0
  const noMarkdown = String(text).replace(/[#*`>|_~\-[\]()!]/g, '')
  const noSpace = noMarkdown.replace(/\s+/g, '')
  return noSpace.length
}

/**
 * 从 query 推断是否要求产出 PDF。
 *
 * 对齐 main_agent.infer_require_pdf。这也是前端「强制生成 PDF 报告」复选框的
 * 底层机制——后端 POST /api/task 只收 query 和 thread_id 两个字段，没有任何
 * 开关参数，所以只能靠往 query 里塞关键词来表达。
 *
 * @param {string} query 任务描述
 * @returns {boolean}
 */
export function inferRequirePdf(query) {
  return String(query || '').toLowerCase().includes('pdf')
}

/**
 * 从 query 推断应当调用的数据源集合。
 *
 * 逐条对齐 main_agent.infer_required_sources 的关键词表。后端优先用主智能体在
 * 规划阶段输出的【数据源声明】，解析不到才回退到这套关键词；前端拿不到声明行
 * （它混在 AI 文本里，不单独发事件），所以只能直接走关键词这条路。
 *
 * @param {string} query 任务描述
 * @returns {Set<string>} 数据源标识集合
 */
export function inferRequiredSources(query) {
  const q = String(query || '')
  const sources = new Set()

  if (['内部', '知识库', '私有', '制度', '规范', '手册', '资料', '文档'].some((k) => q.includes(k))) {
    sources.add('knowledge')
  }
  if (['数据库', '库存', '销售', '销量', '表', 'MySQL', 'mysql', '药品信息', '批次'].some((k) => q.includes(k))) {
    sources.add('db_query')
  }
  if (['网络', '公开', '最新', '新闻', '政策', '法规', '市场', '行业', '网上'].some((k) => q.includes(k))) {
    sources.add('web_search')
  }
  return sources
}

/**
 * 从事件流里统计本轮实际调度过的子智能体。
 *
 * 注意口径：这是"子智能体被**调度**的次数"，不是"子智能体内部工具调用了几次"。
 * 后者完全不推送（agent/main_agent.py 的 astream 没开 subgraphs=True）。
 *
 * @param {Array} events 事件流
 * @returns {Map<string, number>} 子智能体名 → 调度次数
 */
export function countSubagentCalls(events = []) {
  const counts = new Map()
  for (const e of events) {
    if (e.event !== 'assistant_call') continue
    const name = e.data?.assistant_name
    if (!name) continue
    counts.set(name, (counts.get(name) || 0) + 1)
  }
  return counts
}

/**
 * 复算 H1–H4。
 *
 * @param {object} ctx
 * @param {string} ctx.report 报告正文（通常来自 task_result 的 data.result）
 * @param {string} ctx.query 原始任务描述
 * @param {Array} ctx.events 事件流（用于统计数据源覆盖）
 * @param {string[]} ctx.producedFiles 会话产物文件名列表（用于 H3）
 * @returns {{checks: Array<{id,label,ok,detail}>, passed: boolean, wordCount: number}}
 */
export function evaluateReflection({ report = '', query = '', events = [], producedFiles = [] }) {
  const checks = []

  // ── H1 篇幅 ──
  const wordCount = countReportWords(report)
  checks.push({
    id: 'H1',
    label: '篇幅',
    ok: wordCount >= MIN_WORD_COUNT,
    detail: `${wordCount} / ${MIN_WORD_COUNT} 字`,
  })

  // ── H2 占位符 ──
  // 对齐 Python：先判空报告，否则报告为空时"未命中关键词"会伪装成通过
  let h2ok = true
  let h2detail = '无占位符'
  if (!report) {
    h2ok = false
    h2detail = '报告内容为空'
  } else {
    const lowered = String(report).toLowerCase()
    const hits = PLACEHOLDER_KEYWORDS.filter((kw) => lowered.includes(kw.toLowerCase()))
    if (hits.length) {
      h2ok = false
      h2detail = `命中：${hits.join('、')}`
    }
  }
  checks.push({ id: 'H2', label: '占位符', ok: h2ok, detail: h2detail })

  // ── H3 格式（PDF 产出）──
  const requirePdf = inferRequirePdf(query)
  const hasPdf = (producedFiles || []).some((n) => String(n).toLowerCase().endsWith('.pdf'))
  checks.push({
    id: 'H3',
    label: '格式',
    ok: !requirePdf || hasPdf,
    detail: !requirePdf ? '未要求 PDF' : hasPdf ? '已产出 PDF' : '要求 PDF 但未产出',
  })

  // ── H4 数据源覆盖 ──
  const required = inferRequiredSources(query)
  const calledCounts = countSubagentCalls(events)
  const called = new Set()
  for (const name of calledCounts.keys()) {
    const key = SUBAGENT_TO_SOURCE[name]
    if (key) called.add(key)
  }
  const missing = [...required].filter((s) => !called.has(s))
  checks.push({
    id: 'H4',
    label: '数据源',
    ok: missing.length === 0,
    detail: missing.length
      ? `缺少 ${missing.map((s) => SOURCE_LABELS[s] || s).join('、')}`
      : required.size
        ? `已覆盖 ${[...required].map((s) => SOURCE_LABELS[s] || s).join('、')}`
        : '未识别到必需数据源',
  })

  return {
    checks,
    passed: checks.every((c) => c.ok),
    wordCount,
  }
}
