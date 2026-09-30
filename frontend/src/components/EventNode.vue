<script setup>
/**
 * 事件节点（递归）
 *
 * 自己渲染自己：有 children 就用 <EventNode> 再渲染一遍。Vue 3 的 SFC 可以按
 * 文件名自引用，不需要额外注册或 name 选项。
 *
 * 节点有三种：
 *   root  —— 虚拟根「主智能体」（后端不会为主智能体本身发事件，它只是编排者）
 *   group —— 按子智能体聚合的分组（网络检索 / 数据库 / 知识库）
 *   event —— 真实事件（session_created / tool_start / assistant_call / task_result / error）
 *
 * 【两个"没有"必须在界面上体现】
 * 1. 工具调用**没有耗时字段**，也**没有结束事件**。这里显示的时间是"该事件到
 *    下一条事件的间隔"，只能说明这一步之后隔了多久才有下一个动作，不等于工具
 *    本身的执行耗时。口径直接标在 UI 上。
 * 2. 工具调用**没有返回内容**。tool_start 的 data 里只有 {tool_name, args}。
 *    所以工具节点只能展开参数，展不开"返回摘要/完整内容"——后端根本没推。
 */
import { computed, ref } from 'vue'

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
})

/**
 * 事件样式表。
 *
 * 三个子智能体各有专属配色（需求二.1）：网络检索 #409EFF、数据库 #67C23A、
 * 知识库 #9254DE。颜色通过 CSS 变量引用，深浅色主题自动跟随。
 */
const STYLES = {
  main_agent: { icon: '◆', label: '主智能体', cls: 'main' },
  session_created: { icon: '▸', label: '会话', cls: 'session' },
  assistant_call: { icon: '●', label: '子智能体', cls: 'subagent' },
  tool_start: { icon: '·', label: '工具', cls: 'tool' },
  task_result: { icon: '■', label: '结果', cls: 'result' },
  error: { icon: '✕', label: '错误', cls: 'error' },
  // 用户主动终止：中性灰，与红色报错区分开
  cancelled: { icon: '⏹', label: '已终止', cls: 'cancelled' },
}

/** 子智能体名 → 专属配色类 */
const SUBAGENT_CLS = {
  网络检索助手: 'src-web',
  数据库查询助手: 'src-db',
  内部知识库助手: 'src-kb',
}

const style = computed(() => {
  const base = STYLES[props.node.event] || { icon: '?', label: props.node.event, cls: 'unknown' }
  if (props.node.kind === 'group') {
    return { ...base, cls: SUBAGENT_CLS[props.node.name] || 'subagent' }
  }
  return base
})

/** 单行摘要 */
const detail = computed(() => {
  const d = props.node.data || {}
  switch (props.node.event) {
    case 'assistant_call':
      return d.assistant_name || ''
    case 'tool_start':
      return d.tool_name || ''
    case 'session_created':
      return d.path || ''
    default:
      return ''
  }
})

/** 参数预览 */
const argText = computed(() => {
  const d = props.node.data || {}
  if (props.node.event === 'assistant_call') return d.args || ''
  if (props.node.event === 'tool_start' && d.args) {
    try {
      const s = JSON.stringify(d.args)
      return s.length > 200 ? `${s.slice(0, 200)}…` : s
    } catch {
      return String(d.args)
    }
  }
  return ''
})

/** 结果正文：只有 task_result 事件带 result 字段 */
const resultText = computed(() => {
  const r = props.node.data?.result
  return typeof r === 'string' ? r : ''
})

const showResult = ref(false)

/**
 * 耗时显示。
 *
 * node.elapsed 由 EventTree 计算：本事件到下一条事件的间隔（毫秒）。
 * 最后一条事件没有"下一条"，故为 null，显示「—」而不是 0 —— 0 会被误读成
 * "瞬间完成"。
 */
const elapsedText = computed(() => {
  const ms = props.node.elapsed
  if (ms == null) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
})

const time = computed(() => {
  const t = props.node.timestamp
  if (!t) return ''
  const m = String(t).match(/T(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : t
})

const hasChildren = computed(() => (props.node.children?.length || 0) > 0)
const isRunning = computed(() => props.node.running === true)
</script>

<template>
  <div class="node" :class="`d${Math.min(depth, 4)}`">
    <div class="row" :class="[style.cls, { 'ty-enter': node.fresh }]">
      <span class="icon" :class="{ 'ty-running': isRunning }">{{ style.icon }}</span>
      <span class="label">{{ style.label }}</span>
      <span class="message">{{ node.message }}</span>
      <span v-if="detail && detail !== node.message" class="detail">{{ detail }}</span>

      <span class="spacer"></span>

      <!-- 耗时口径写在 title 上，鼠标悬停可确认 -->
      <span v-if="elapsedText" class="elapsed" title="本事件到下一条事件的间隔，非工具自身执行耗时">
        {{ elapsedText }}
      </span>
      <span v-if="time" class="time">{{ time }}</span>
    </div>

    <div v-if="argText" class="args">{{ argText }}</div>

    <!-- 只有 task_result 带正文；其余事件没有 result 字段，这块不会渲染 -->
    <div v-if="resultText" class="result">
      <el-button size="small" @click="showResult = !showResult">
        {{ showResult ? '收起结果' : `展开结果（${resultText.length} 字符）` }}
      </el-button>
      <pre v-if="showResult" class="result-body">{{ resultText }}</pre>
    </div>

    <!-- 工具节点显式说明为何展不开返回内容 -->
    <div v-if="node.event === 'tool_start'" class="hint">
      后端 tool_start 只推 {{ '{' }}tool_name, args{{ '}' }}，无返回值也无结束事件，故无法展示返回内容
    </div>

    <div v-if="hasChildren" class="children">
      <EventNode v-for="child in node.children" :key="child.id" :node="child" :depth="depth + 1" />
    </div>
  </div>
</template>

<style scoped>
.node { margin-bottom: 5px; }

.row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 9px;
  border-radius: var(--ty-radius-sm);
  font-size: 13px;
  background: var(--ty-surface-soft);
  border: 1px solid transparent;
  transition: background 0.18s, border-color 0.18s, transform 0.18s;
}
.row:hover {
  border-color: var(--ty-border);
  transform: translateX(2px);
}

.icon { width: 12px; text-align: center; font-size: 12px; }
.label {
  font-size: 11px;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 6px;
  background: var(--ty-border);
  color: var(--ty-text-soft);
  white-space: nowrap;
  letter-spacing: 0.02em;
}
.message { color: var(--ty-text); }
.detail { color: var(--ty-primary); font-size: 12px; font-weight: 600; }
.spacer { flex: 1; }
.time,
.elapsed {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
}
.time { color: var(--ty-text-faint); }
.elapsed { color: var(--ty-text-soft); }

/* ── 各事件类型配色：色块 chip + 同色极淡底 ── */
.main .label {
  background-image: var(--ty-grad-primary);
  color: #fff;
  box-shadow: 0 2px 10px -3px var(--ty-glow);
}
.main .message { font-weight: 700; letter-spacing: 0.02em; }

.subagent .label { background: var(--ty-primary-weak); color: var(--ty-primary); }
.subagent { background: color-mix(in srgb, var(--ty-primary) 6%, transparent); }

/* 三个子智能体专属配色（需求二.1 指定色值） */
.src-web .label { background: color-mix(in srgb, var(--ty-src-web) 16%, transparent); color: var(--ty-src-web); }
.src-db .label { background: color-mix(in srgb, var(--ty-src-db) 16%, transparent); color: var(--ty-src-db); }
.src-kb .label { background: color-mix(in srgb, var(--ty-src-kb) 16%, transparent); color: var(--ty-src-kb); }
/* 分组节点左侧留一条专属色竖条，跨轮次也能一眼对上 */
.src-web { background: color-mix(in srgb, var(--ty-src-web) 7%, transparent); box-shadow: inset 3px 0 0 var(--ty-src-web); }
.src-db { background: color-mix(in srgb, var(--ty-src-db) 7%, transparent); box-shadow: inset 3px 0 0 var(--ty-src-db); }
.src-kb { background: color-mix(in srgb, var(--ty-src-kb) 7%, transparent); box-shadow: inset 3px 0 0 var(--ty-src-kb); }

.tool .label { background: var(--ty-border); color: var(--ty-text-soft); }
.session .label { background: color-mix(in srgb, var(--ty-success) 16%, transparent); color: var(--ty-success); }

.result .label { background: var(--ty-primary-weak); color: var(--ty-primary); }
.result .message { font-weight: 700; }

.error .label { background: color-mix(in srgb, var(--ty-error) 16%, transparent); color: var(--ty-error); }
.error { background: color-mix(in srgb, var(--ty-error) 7%, transparent); box-shadow: inset 3px 0 0 var(--ty-error); }

/* 已终止：中性灰。用户主动停的不是故障，不该跟红色报错长一样 */
.cancelled .label { background: var(--ty-border); color: var(--ty-text-soft); }
.cancelled { background: color-mix(in srgb, var(--ty-text-faint) 9%, transparent); }

.args {
  margin: 4px 0 0 36px;
  font-size: 12px;
  color: var(--ty-text-soft);
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
  word-break: break-all;
}

.hint {
  margin: 4px 0 0 36px;
  font-size: 10px;
  color: var(--ty-text-faint);
  font-style: italic;
}

.result { margin: 5px 0 5px 36px; }
.result-body {
  margin: 7px 0 0;
  padding: 11px;
  max-height: 320px;
  overflow: auto;
  /* 深底代码块：与整体浅色/深色主题都拉开层次 */
  background: #0a1020;
  color: #cfe0ff;
  border: 1px solid var(--ty-border);
  border-radius: var(--ty-radius-sm);
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 缩进 + 左侧渐变竖线，把层级画出来 */
.children {
  position: relative;
  margin-left: 16px;
  padding-left: 13px;
  margin-top: 5px;
}
.children::before {
  content: '';
  position: absolute;
  left: 0;
  top: 2px;
  bottom: 2px;
  width: 1px;
  background: linear-gradient(180deg, var(--ty-primary), transparent 85%);
  opacity: 0.35;
}
</style>
