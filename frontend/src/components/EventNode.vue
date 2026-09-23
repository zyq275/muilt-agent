<script setup>
/**
 * 事件节点（递归） — TDD §8.3
 *
 * 自己渲染自己：节点若有 children，就用 <EventNode> 再渲染一遍。
 * Vue 3 的 SFC 可以按文件名自引用，所以这里不需要额外的注册或 name 选项。
 *
 * 当前层级（见 EventTree 的建树逻辑）：
 *   main_agent（虚拟根） → 各类事件（子智能体、工具……）平级
 *
 * 递归目前只走到第二层就终止——因为事件流里还拿不到更深的父子关系
 * （原因见 EventTree 顶部注释）。组件本身支持任意深度，一旦后端开始推
 * 子图事件，这里不用改就能渲染出「子智能体 → 工具」的第三层。
 */
import { computed, ref } from 'vue'

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
})

/** 每种事件的展示形态与配色 */
const STYLES = {
  main_agent: { icon: '◆', label: '主智能体', cls: 'main' },
  session_created: { icon: '▸', label: '会话', cls: 'session' },
  assistant_call: { icon: '●', label: '子智能体', cls: 'subagent' },
  tool_start: { icon: '·', label: '工具', cls: 'tool' },
  task_result: { icon: '■', label: '结果', cls: 'result' },
  error: { icon: '✕', label: '错误', cls: 'error' },
}

const style = computed(
  () => STYLES[props.node.event] || { icon: '?', label: props.node.event, cls: 'unknown' }
)

/** 单行摘要：把该事件最值得看的那一两个字段挑出来 */
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

/** 参数预览：tool_start 的 args / assistant_call 的任务描述 */
const argPreview = computed(() => {
  const d = props.node.data || {}
  if (props.node.event === 'assistant_call') return d.args || ''
  if (props.node.event === 'tool_start' && d.args) {
    try {
      const s = JSON.stringify(d.args)
      return s.length > 160 ? s.slice(0, 160) + '…' : s
    } catch {
      return String(d.args)
    }
  }
  return ''
})

/** 结果正文（可能很长，默认折叠） */
const showResult = ref(false)
const resultText = computed(() => {
  const r = props.node.data?.result
  return typeof r === 'string' ? r : ''
})

const time = computed(() => {
  const t = props.node.timestamp
  if (!t) return ''
  // ISO8601 只取时分秒，日期对一次会话没意义
  const m = t.match(/T(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : t
})

const hasChildren = computed(() => (props.node.children?.length || 0) > 0)
</script>

<template>
  <div class="node" :class="`d${Math.min(depth, 4)}`">
    <div class="row" :class="style.cls">
      <span class="icon">{{ style.icon }}</span>
      <span class="label">{{ style.label }}</span>
      <span class="message">{{ node.message }}</span>
      <span v-if="detail" class="detail">{{ detail }}</span>
      <span class="spacer"></span>
      <span v-if="time" class="time">{{ time }}</span>
    </div>

    <div v-if="argPreview" class="args">{{ argPreview }}</div>

    <div v-if="resultText" class="result">
      <button class="toggle" @click="showResult = !showResult">
        {{ showResult ? '收起结果' : `展开结果（${resultText.length} 字符）` }}
      </button>
      <pre v-if="showResult" class="result-body">{{ resultText }}</pre>
    </div>

    <!-- 递归：子节点用同一个组件渲染 -->
    <div v-if="hasChildren" class="children">
      <EventNode v-for="child in node.children" :key="child.id"
                 :node="child" :depth="depth + 1" />
    </div>
  </div>
</template>

<style scoped>
.node { margin-bottom: 4px; }

.row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 5px;
  font-size: 13px;
  background: #f9fafb;
}

.icon { width: 12px; text-align: center; font-size: 12px; }
.label {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  background: #e5e7eb;
  color: #4b5563;
  white-space: nowrap;
}
.message { color: #374151; }
.detail { color: #2563eb; font-size: 12px; }
.spacer { flex: 1; }
.time { color: #9ca3af; font-size: 11px; font-variant-numeric: tabular-nums; }

/* 各事件类型的配色 */
.main .label { background: #1f2937; color: #fff; }
.main .message { font-weight: 600; }
.subagent .label { background: #dbeafe; color: #1e40af; }
.subagent { background: #f0f7ff; }
.tool .label { background: #f3f4f6; color: #6b7280; }
.session .label { background: #dcfce7; color: #166534; }
.result .label { background: #dbeafe; color: #1e40af; }
.result .message { font-weight: 600; }
.error .label { background: #fee2e2; color: #991b1b; }
.error { background: #fef2f2; }

.args {
  margin: 3px 0 0 34px;
  font-size: 12px;
  color: #6b7280;
  font-family: Consolas, Monaco, monospace;
  word-break: break-all;
}

.result { margin: 4px 0 4px 34px; }
.toggle {
  font-size: 12px;
  padding: 2px 8px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  background: #fff;
  color: #4b5563;
  cursor: pointer;
}
.toggle:hover { background: #f3f4f6; }
.result-body {
  margin: 6px 0 0;
  padding: 10px;
  max-height: 320px;
  overflow: auto;
  background: #1f2937;
  color: #e5e7eb;
  border-radius: 5px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 缩进 + 左侧竖线，把层级画出来 */
.children {
  margin-left: 15px;
  padding-left: 12px;
  border-left: 1px solid #e5e7eb;
  margin-top: 4px;
}
</style>
