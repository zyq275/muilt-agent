<script setup>
/**
 * 探渊前端主界面 — TDD §8.3
 *
 * 职责：串起「提交任务 → 连接 WebSocket → 收集事件 → 渲染层级树」这条链路，
 * 并把当前 thread_id 交给文件面板，保证上传/浏览落在同一个会话目录里。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { createEventSocket, submitTask } from './api'
import EventTree from './components/EventTree.vue'
import FilePanel from './components/FilePanel.vue'
import TaskInput from './components/TaskInput.vue'

// 会话标识。客户端自己生成而不是等后端返回——原因见 handleSubmit 里的说明。
const threadId = ref('')
// 事件流（按到达顺序平铺，树形结构由 EventTree 现场构建）
const events = ref([])
// idle | connecting | running | done | error
const status = ref('idle')

let socket = null

const STATUS_TEXT = {
  idle: '待提交',
  connecting: '连接中…',
  running: '执行中…',
  done: '已完成',
  error: '出错',
}

const statusText = computed(() => STATUS_TEXT[status.value] || status.value)

/**
 * 生成会话标识。
 *
 * 必须满足后端的白名单 ^[A-Za-z0-9_-]{1,64}$（api/server.py 的 _safe_thread_id），
 * 否则提交任务时会被 400 拒绝。只用小写字母、数字和连字符最稳妥。
 */
function newThreadId() {
  const stamp = Date.now().toString(36)
  const rand = Math.random().toString(36).slice(2, 8)
  return `web-${stamp}-${rand}`
}

function closeSocket() {
  if (socket) {
    // 先摘掉回调再关，避免主动关闭触发 onClose 把状态改回 idle
    socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null
    socket.close()
    socket = null
  }
}

function handleMessage(msg) {
  // 服务端心跳（每 30s 一条）与心跳应答都不进事件流，否则会把层级树冲乱
  if (msg.type === 'ping' || msg.type === 'pong') return

  if (msg.type === 'monitor_event') {
    events.value.push(msg)
    if (msg.event === 'task_result') status.value = 'done'
    else if (msg.event === 'error') status.value = 'error'
    return
  }

  if (msg.type === 'raw') events.value.push(msg)
}

async function handleSubmit(query) {
  closeSocket()
  events.value = []
  status.value = 'connecting'

  // 会话标识由前端生成，而不是提交后取后端返回的那个。
  //
  // 因为 WebSocket 必须在任务开始之前就连上：后端一收到 /api/task 就
  // asyncio.create_task 立即开跑，若先提交再拿 thread_id 去连，
  // session_created 和最早的一批 tool_start 会在连接建立前就发出去，
  // 从此永久丢失。所以顺序必须是「先定 id → 连上 → 再提交」。
  const tid = newThreadId()
  threadId.value = tid

  let markReady
  const ready = new Promise((resolve) => { markReady = resolve })

  socket = createEventSocket(tid, {
    onOpen: () => {
      status.value = 'running'
      markReady()
    },
    onMessage: handleMessage,
    onClose: () => {
      // 任务跑完或出错后连接可能被回收，此时不要覆盖终态
      if (status.value === 'connecting' || status.value === 'running') {
        status.value = 'idle'
      }
    },
  })

  // 等握手完成再提交，避免漏事件；但不能无限等——WebSocket 起不来时
  // 也得让提交照常发出，并在界面上暴露连接失败，而不是静默卡住。
  await Promise.race([
    ready,
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ])

  try {
    await submitTask(query, tid)
  } catch (e) {
    status.value = 'error'
    events.value.push({
      type: 'monitor_event',
      event: 'error',
      message: `提交任务失败：${e.message}`,
      data: {},
      timestamp: new Date().toISOString(),
    })
  }
}

onBeforeUnmount(closeSocket)
</script>

<template>
  <div class="app">
    <header class="header">
      <h1>探渊 · 多智能体数据分析系统</h1>
      <div class="status">
        <span class="dot" :class="status"></span>
        <span>{{ statusText }}</span>
        <code v-if="threadId" class="tid">{{ threadId }}</code>
      </div>
    </header>

    <main class="main">
      <section class="left">
        <TaskInput :disabled="status === 'connecting' || status === 'running'"
                   @submit="handleSubmit" />
        <EventTree :events="events" />
      </section>

      <aside class="right">
        <FilePanel :thread-id="threadId" />
      </aside>
    </main>
  </div>
</template>

<style>
* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
  background: #f5f6f8;
  color: #222;
}

.app { display: flex; flex-direction: column; height: 100vh; }

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: #1f2937;
  color: #fff;
}
.header h1 { font-size: 17px; margin: 0; font-weight: 600; }

.status { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #9ca3af; }
.dot.running { background: #22c55e; animation: pulse 1.2s infinite; }
.dot.connecting { background: #eab308; animation: pulse 1.2s infinite; }
.dot.done { background: #3b82f6; }
.dot.error { background: #ef4444; }
@keyframes pulse { 50% { opacity: 0.3; } }

.tid {
  background: rgba(255, 255, 255, 0.12);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
}

.main { flex: 1; display: flex; min-height: 0; }
.left { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.right { width: 320px; border-left: 1px solid #e5e7eb; background: #fff; }
</style>
