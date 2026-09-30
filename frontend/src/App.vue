<script setup>
/**
 * 应用外壳 —— 三栏 + 底部输入栏
 *
 *   ┌──────────────────── 顶栏（标题 / 会话 / 主题切换）────────────────────┐
 *   ├ 会话栏 220px ┬────── 中栏（反思校验 / 执行树 / 结果 / 输入栏）──────┬ 文件栏 320px ┤
 *   └──────────────┴───────────────────────────────────────────────────┴─────────────┘
 *
 * 【WebSocket 与提交的先后顺序：为什么会话标识是前端生成的】
 * 事件是任务一跑就发出来的（session_created 往往在提交后几十毫秒内到），如果等
 * POST /api/task 返回 thread_id 再去连 /ws/{thread_id}，开头几条必然丢失。
 *
 * 解决办法是**前端先生成 thread_id**，先把 WebSocket 连上、再提交。api/server.py
 * 的 create_task() 允许客户端自带 thread_id（只有格式校验 `[A-Za-z0-9_-]{1,64}`，
 * 不检查是否已存在），所以这条路是通的。生成规则满足该正则。
 *
 * 【为什么提交不会新建会话】
 * 当前 thread_id 在用户点「新建会话」、切换会话或重跑之前保持不变。这样：
 *   1. 用户可以先上传文件到 upload/session_{tid}/，再提交任务让智能体去读——
 *      上传接口和任务执行必须落在**同一个** tid 上，否则文件永远读不到；
 *   2. 在同一会话里多次提交是允许的，后端每次都是一次独立执行，产物写进同一个
 *      output/session_{tid}/ 并累积。
 * 代价是两次提交的产物混在同一目录，这一点在结果区有提示。
 *
 * 【刷新即丢】事件流只存在内存里（后端没有历史事件接口），刷新页面后执行树会空。
 * 会话列表也只是本机浏览器的索引，不是服务端总账。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Moon, Sunny, Plus, InfoFilled } from '@element-plus/icons-vue'
import SessionSidebar from './components/SessionSidebar.vue'
import EventTree from './components/EventTree.vue'
import FilePanel from './components/FilePanel.vue'
import ReflectCard from './components/ReflectCard.vue'
import ResultTabs from './components/ResultTabs.vue'
import TaskInput from './components/TaskInput.vue'
import { createEventSocket, submitTask } from './api'
import { addSession, listSessions, removeSession, updateStatus } from './sessionStore'
import { dropEvents, loadAllEvents, saveAllEvents } from './eventStore'
import { evaluateReflection, SUBAGENT_TO_SOURCE } from './reflect'

// ── 主题 ──────────────────────────────────────────────
const THEME_KEY = 'tanyuan.dark'
const isDark = ref(localStorage.getItem(THEME_KEY) === '1')

function applyTheme() {
  // Element Plus 的暗色变量挂在 html.dark 上，同时自己的 theme.css 也用这个类
  document.documentElement.classList.toggle('dark', isDark.value)
  localStorage.setItem(THEME_KEY, isDark.value ? '1' : '0')
}
watch(isDark, applyTheme)

/**
 * 状态 → 中文。
 *
 * cancelled 是**用户主动终止**，与 error 分开：它不是故障，界面上不报红。
 */
const STATUS_TEXT = {
  idle: '待提交',
  connecting: '连接中',
  running: '执行中',
  done: '已完成',
  error: '出错',
  cancelled: '已终止',
}

/** 状态 → 顶部小圆点的样式（呼吸 = 正在跑） */
const STATUS_DOT = {
  idle: '',
  connecting: 'is-live',
  running: 'is-live',
  done: 'is-done',
  error: 'is-error',
  cancelled: '',
}

const statusDotClass = computed(() => STATUS_DOT[status.value] || '')

// ── 会话标识 ──────────────────────────────────────────
/**
 * 生成合法的 thread_id。
 *
 * 必须匹配 api/server.py 的 `^[A-Za-z0-9_-]{1,64}$`，所以只用 36 进制字符
 * （a-z0-9）加连字符，长度也远在 64 以内。
 */
function makeThreadId() {
  const rand = Math.random().toString(36).slice(2, 8)
  return `task-${Date.now().toString(36)}-${rand}`
}

const threadId = ref(makeThreadId())

// ── 按会话隔离的内存态 ────────────────────────────────
/** threadId → 事件数组 */
const eventsByThread = ref({})
/** threadId → idle | connecting | running | done | error */
const statusByThread = ref({})
/** threadId → 任务描述（反思校验要用原始 query 推断数据源与 PDF 要求） */
const queryByThread = ref({})
/** threadId → 会话产物 { mdPath, pdfPath, files } */
const artifactsByThread = ref({})

const events = computed(() => eventsByThread.value[threadId.value] || [])
const status = computed(() => statusByThread.value[threadId.value] || 'idle')
const artifacts = computed(
  () => artifactsByThread.value[threadId.value] || { mdPath: '', pdfPath: '', files: [] }
)

/** 报告正文：取最后一条 task_result 的 result */
const report = computed(() => {
  for (let i = events.value.length - 1; i >= 0; i -= 1) {
    if (events.value[i].event === 'task_result') {
      const r = events.value[i].data?.result
      if (typeof r === 'string') return r
    }
  }
  return ''
})

/** 产物文件名列表（供 H3 判断有没有 .pdf） */
const producedFiles = computed(() => (artifacts.value.files || []).map((f) => f.name))

/** 报告生成次数：作为反思轮次的粗略代理（见 ReflectCard 的说明） */
const reportGenerations = computed(
  () =>
    events.value.filter(
      (e) => e.event === 'tool_start' && String(e.data?.tool_name || '').includes('markdown')
    ).length
)

/** H1–H4 复算（前端按 reflect.js 的同一套算法算，非后端推送，见该文件头注释） */
const reflection = computed(() =>
  evaluateReflection({
    report: report.value,
    query: queryByThread.value[threadId.value] || '',
    events: events.value,
    producedFiles: producedFiles.value,
  })
)

// ── 会话列表（本机 localStorage 索引）──────────────────
const sessions = ref([])
function reloadSessions() {
  sessions.value = listSessions()
}

// ── WebSocket ─────────────────────────────────────────
const ws = ref(null)
const wsThreadId = ref('')
let reconnectTimer = null
let manualClose = false

function setStatus(tid, value) {
  statusByThread.value = { ...statusByThread.value, [tid]: value }
  // 会话列表里的状态同步过去；未登记的会话（还没提交过）忽略即可
  if (listSessions().some((s) => s.threadId === tid)) {
    updateStatus(tid, value)
    reloadSessions()
  }
}

function pushEvent(tid, evt) {
  const prev = eventsByThread.value[tid] || []
  eventsByThread.value = { ...eventsByThread.value, [tid]: [...prev, evt] }
}

// ── 事件的本地持久化 ──────────────────────────────────
/**
 * 事件是流式到达的，来一条存一次 localStorage 会拖慢界面（JSON.stringify 是
 * 同步的，报告正文又大），所以做成防抖：安静 800ms 后才落盘一次。
 */
let saveTimer = null
function scheduleSave() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    const ok = saveAllEvents(eventsByThread.value, threadId.value)
    if (!ok) {
      // 配额实在放不下。不弹错误打断用户——任务照跑、事件照收，
      // 只是本次刷新后日志会缺一段，控制台留个记录即可。
      console.warn('[探渊] 事件日志未能写入 localStorage（配额不足），刷新后可能丢失部分执行过程')
    }
  }, 800)
}

watch(eventsByThread, scheduleSave, { deep: false })

/**
 * 连接某会话的事件通道。
 *
 * 一个会话一条连接：切走时主动关掉旧的，避免后台连接持续堆积消息。
 * 心跳消息（ping/pong）不是业务事件，只用于确认链路还活着，不进事件树——
 * 否则每 30 秒就会多出一条噪声。
 */
function connect(tid) {
  if (!tid) return
  if (ws.value && wsThreadId.value === tid && ws.value.readyState === WebSocket.OPEN) return

  closeSocket()
  manualClose = false
  wsThreadId.value = tid

  // 连接状态不等于任务状态。
  //
  // 页面一打开就会为当前会话预连通道（为了提交时不丢开头几条事件），那时还
  // **一个任务都没提交过**。如果在这里无条件置成 connecting/running，界面就会
  // 平白显示"执行中"。所以只有"任务确实在跑"这个前提下才谈连接态。
  if (statusByThread.value[tid] === 'running') setStatus(tid, 'connecting')

  const socket = createEventSocket(tid, {
    onOpen: () => {
      // 仅当刚才确实是从 running 降下来的 connecting，才回升到 running。
      // 空闲会话（还没提交任务）保持 idle，已完成的会话保持 done。
      if (statusByThread.value[tid] === 'connecting') setStatus(tid, 'running')
    },
    onMessage: (msg) => {
      if (msg.type === 'ping' || msg.type === 'pong') return
      if (msg.type !== 'monitor_event') return

      pushEvent(tid, msg)

      if (msg.event === 'task_result') {
        setStatus(tid, 'done')
        // 报告文件刚落盘，刷新一次产物列表，让 PDF Tab 与 H3 立刻生效
        filePanelRef.value?.refresh()
      } else if (msg.event === 'error') {
        setStatus(tid, 'error')
      } else if (msg.event === 'cancelled') {
        // 后端在取消时主动推的终结事件。定成 terminated 而非 error：
        // 这是用户的选择，不是故障，界面上不该报红。
        setStatus(tid, 'cancelled')
      } else if (msg.event === 'session_created') {
        if (statusByThread.value[tid] !== 'done') setStatus(tid, 'running')
        // 后端在这一刻刚建好 output/session_{tid}/，立刻重读一次，
        // 让文件面板从「还没有产出文件」切到正常的空目录状态，
        // 而不是一直挂着"提交任务后这里会出现报告"这句已经过时的话。
        filePanelRef.value?.refresh()
      }
    },
    onClose: () => {
      if (manualClose) return
      // 只有任务确实在跑时才值得重连。空闲/已完成/出错的会话断了就算了——
      // 否则后端没起来时，每次打开页面都会每 2 秒空转一次
      const cur = statusByThread.value[tid]
      if (cur !== 'running' && cur !== 'connecting') return
      clearTimeout(reconnectTimer)
      reconnectTimer = setTimeout(() => {
        if (!manualClose) connect(tid)
      }, 2000)
    },
    onError: () => {
      // onClose 一定会跟着来，重连逻辑统一放在那里
    },
  })

  ws.value = socket
}

function closeSocket() {
  manualClose = true
  clearTimeout(reconnectTimer)
  if (ws.value) {
    try {
      ws.value.close()
    } catch {
      /* 已经关了或从未打开，忽略 */
    }
    ws.value = null
  }
}

// ── 提交流程 ──────────────────────────────────────────
const submitting = ref(false)
const taskInputRef = ref(null)
const filePanelRef = ref(null)

async function onSubmit(query) {
  const tid = threadId.value
  submitting.value = true
  try {
    // 1. 先连通道：事件在任务一开始就发，连晚了开头几条会丢
    connect(tid)

    // 2. 记下任务描述并登记会话（状态先置运行中）
    queryByThread.value = { ...queryByThread.value, [tid]: query }
    addSession(tid, query)
    reloadSessions()
    setStatus(tid, 'running')

    // 3. 提交。后端 asyncio.create_task 后台跑，这里几乎立刻返回
    await submitTask(query, tid)
    taskInputRef.value?.clear()
    ElMessage.success(`任务已提交到会话 ${tid}`)
  } catch (e) {
    setStatus(tid, 'error')
    pushEvent(tid, {
      type: 'monitor_event',
      event: 'error',
      message: `提交失败：${e.message}`,
      data: {},
      timestamp: new Date().toISOString(),
    })
    ElMessage.error(`提交失败：${e.message}`)
  } finally {
    submitting.value = false
  }
}

/** 侧边栏「重跑」：同一个任务描述，新会话 */
function onRerun(item) {
  threadId.value = makeThreadId()
  connect(threadId.value)
  // 等价于用户在输入框里重新提交一次
  onSubmit(item.query)
}

/** 切换会话 */
function onSelect(item) {
  threadId.value = item.threadId
  // 首次切到某会话时，用它在本机记录里的状态当初值——否则会出现侧栏标着
  // "运行中"、顶栏却写"待提交"的矛盾。真实状态仍以随后收到的事件为准。
  if (!statusByThread.value[item.threadId]) {
    statusByThread.value = { ...statusByThread.value, [item.threadId]: item.status }
  }
  connect(item.threadId)
}

/** 新建会话：换一个 thread_id，旧会话的事件仍留在内存里可切回 */
function newSession() {
  threadId.value = makeThreadId()
  connect(threadId.value)
  ElMessage.info('已新建会话，提交任务前可先把文件上传到该会话')
}

function onRemove(item) {
  removeSession(item.threadId)
  // 会话记录删了，它的事件副本也一并清掉——否则 localStorage 里会留下
  // 一份再也访问不到、却一直占配额的数据
  eventsByThread.value = dropEvents(eventsByThread.value, item.threadId)
  reloadSessions()
  // 删掉的正好是当前会话：换一个新会话，不然会停在一个已不在列表里的会话上
  if (item.threadId === threadId.value) newSession()
}

function onTemplate(text) {
  taskInputRef.value?.fill(text)
}

/**
 * 清空当前会话的事件日志。
 *
 * 前后端一起说清：浏览器里这份副本会被清掉，服务端**本来就没存事件**，
 * 所以清掉的只有本地这一份，再刷新也回不来了。
 */
function clearEvents() {
  eventsByThread.value = { ...eventsByThread.value, [threadId.value]: [] }
  ElMessage.info('已清空执行过程日志（含本机保存的副本，不可恢复）')
}

/**
 * 收录文件面板报上来的产物清单。
 *
 * 用 payload 自带的 threadId 而不是当前的 threadId.value：目录读取是异步的，
 * 返回时用户可能已经切到别的会话，用当前值会把 A 的产物挂到 B 头上。
 */
function onArtifacts(payload) {
  const tid = payload.threadId || threadId.value
  artifactsByThread.value = { ...artifactsByThread.value, [tid]: payload }
}

// ── 生命周期 ──────────────────────────────────────────
onMounted(() => {
  applyTheme()
  reloadSessions()

  // 恢复本浏览器上次存下的执行过程。
  //
  // 必须在 connect() 之前做：pushEvent 是"在已有数组后面追加"，如果先连上
  // WebSocket，新事件会以空数组为起点写进去，随后再灌历史就会把它覆盖掉。
  eventsByThread.value = loadAllEvents()

  // 顺带把任务描述补回来——反思校验要用原始 query 推断数据源和 PDF 要求，
  // 而这些从事件流里反推不出来。
  const q = {}
  for (const s of sessions.value) q[s.threadId] = s.query
  queryByThread.value = { ...q, ...queryByThread.value }

  // 恢复出来的状态以本机记录的为准，避免侧栏标"已完成"而顶栏写"待提交"
  const st = {}
  for (const s of sessions.value) st[s.threadId] = s.status
  statusByThread.value = { ...st, ...statusByThread.value }

  connect(threadId.value)
})

onBeforeUnmount(closeSocket)

/**
 * 任务是否在跑（含刚提交、WebSocket 还在连的那一小段）。
 *
 * 传给 FilePanel 决定要不要轮询产物目录——运行期没有"文件已落盘"的推送，
 * 不轮询就只能在 task_result 那一刻才一次性看到全部产物。
 */
const isRunning = computed(() => status.value === 'running' || status.value === 'connecting')

/** 当前会话调度的子智能体数（顶栏概要） */
const subagentCount = computed(() => {
  let n = 0
  for (const e of events.value) {
    if (e.event === 'assistant_call' && SUBAGENT_TO_SOURCE[e.data?.assistant_name]) n += 1
  }
  return n
})
</script>

<template>
  <div class="app ty-backdrop">
    <!-- ── 顶栏 ── -->
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">探</span>
        <span class="brand-text">
          <b class="ty-grad-text">探渊</b>
          <em>多智能体研究平台</em>
        </span>
      </div>

      <span class="divider" />

      <span class="ty-mono tid" :title="threadId">{{ threadId }}</span>

      <span class="status-pill" :class="`is-${status}`">
        <i class="ty-dot" :class="statusDotClass" />
        {{ STATUS_TEXT[status] || status }}
      </span>

      <span v-if="events.length" class="summary">
        {{ events.length }} 条事件 · 子智能体调度 {{ subagentCount }} 次
      </span>

      <span class="spacer" />

      <el-button size="small" :icon="Plus" @click="newSession">新建会话</el-button>

      <el-tooltip :content="isDark ? '切换到浅色' : '切换到深色'" placement="bottom">
        <el-button size="small" :icon="isDark ? Sunny : Moon" circle @click="isDark = !isDark" />
      </el-tooltip>
    </header>

    <!-- ── 三栏主体 ── -->
    <div class="main">
      <SessionSidebar
        :sessions="sessions"
        :current-thread-id="threadId"
        @select="onSelect"
        @rerun="onRerun"
        @remove="onRemove"
        @template="onTemplate"
      />

      <section class="center">
        <div class="center-top">
          <ReflectCard
            :checks="reflection.checks"
            :word-count="reflection.wordCount"
            :report-generations="reportGenerations"
            :has-report="Boolean(report)"
          />
        </div>

        <EventTree :events="events" :thread-id="threadId" :status="status" @clear="clearEvents" />

        <div class="center-bottom">
          <ResultTabs
            :report="report"
            :pdf-path="artifacts.pdfPath"
            :md-path="artifacts.mdPath"
          />
          <p v-if="events.length" class="hint">
            <el-icon><InfoFilled /></el-icon>
            <span>
              同一会话内的多次提交产物会写进同一个
              <code>output/session_{{ threadId }}/</code> 并累积，因此这里显示的可能不止
              本次任务的产物。执行过程日志保存在本机浏览器中，刷新后可恢复；但服务器
              没有事件历史接口，换浏览器或清缓存后即消失，页面未打开期间的事件也不会补回。
            </span>
          </p>
        </div>

        <TaskInput
          ref="taskInputRef"
          :loading="submitting"
          :thread-id="threadId"
          @submit="onSubmit"
        />
      </section>

      <aside class="right">
        <FilePanel
          ref="filePanelRef"
          :thread-id="threadId"
          :running="isRunning"
          @artifacts="onArtifacts"
        />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.app {
  height: 100vh;
  display: flex;
  flex-direction: column;
  min-height: 0;
  color: var(--ty-text);
}

/* ── 顶栏 ── */
.topbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  background: var(--ty-surface);
  backdrop-filter: var(--ty-glass);
  -webkit-backdrop-filter: var(--ty-glass);
  border-bottom: 1px solid var(--ty-border);
  box-shadow: 0 1px 12px -6px rgba(11, 23, 48, 0.3);
  position: relative;
  z-index: 5;
}

/* 品牌区：渐变方块 + 渐变字 */
.brand {
  display: flex;
  align-items: center;
  gap: 9px;
}
.brand-mark {
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background-image: var(--ty-grad-primary);
  color: #fff;
  font-size: 16px;
  font-weight: 800;
  box-shadow: 0 3px 14px -3px var(--ty-glow), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}
.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}
.brand-text b { font-size: 15px; font-weight: 800; letter-spacing: 0.02em; }
.brand-text em {
  font-style: normal;
  font-size: 10px;
  color: var(--ty-text-faint);
  letter-spacing: 0.04em;
}

.divider {
  width: 1px;
  height: 20px;
  background: var(--ty-border);
}

.tid {
  max-width: 230px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 状态胶囊：玻璃底 + 呼吸圆点 */
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px 3px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid var(--ty-border);
  background: var(--ty-surface-soft);
  color: var(--ty-text-soft);
  white-space: nowrap;
}
.status-pill.is-running,
.status-pill.is-connecting {
  color: var(--ty-primary);
  border-color: color-mix(in srgb, var(--ty-primary) 40%, transparent);
  background: var(--ty-primary-weak);
}
.status-pill.is-done {
  color: var(--ty-success);
  border-color: color-mix(in srgb, var(--ty-success) 38%, transparent);
  background: color-mix(in srgb, var(--ty-success) 10%, transparent);
}
.status-pill.is-error {
  color: var(--ty-error);
  border-color: color-mix(in srgb, var(--ty-error) 38%, transparent);
  background: color-mix(in srgb, var(--ty-error) 10%, transparent);
}

.summary { font-size: 11px; color: var(--ty-text-faint); white-space: nowrap; }
.spacer { flex: 1; }

/* ── 主体三栏 ── */
.main {
  flex: 1;
  min-height: 0;
  display: flex;
}

.center {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.center-top {
  flex: 0 0 auto;
  padding: 12px 16px 0;
}

.center-bottom {
  flex: 0 0 auto;
  padding: 0 16px 12px;
}

.hint {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 9px 0 0;
  padding: 8px 10px;
  font-size: 10px;
  line-height: 1.65;
  color: var(--ty-text-faint);
  background: var(--ty-surface-soft);
  border: 1px dashed var(--ty-border);
  border-radius: var(--ty-radius-sm);
}
.hint code {
  background: var(--ty-surface-sunken);
  padding: 0 4px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
}

/* ── 右栏 ── */
.right {
  flex: 0 0 var(--ty-filepanel-w);
  width: var(--ty-filepanel-w);
  min-height: 0;
  overflow: hidden;
  background: var(--ty-surface);
  backdrop-filter: var(--ty-glass);
  -webkit-backdrop-filter: var(--ty-glass);
  border-left: 1px solid var(--ty-border);
}
</style>
