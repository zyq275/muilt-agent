<script setup>
/**
 * 事件树容器 — TDD §8.3
 *
 * 关于层级：TDD §8.3 写的是「assistant_call 为父节点、其下 tool_start 为子节点」，
 * 但实测事件流不支持这个嵌套，因此这里改为平级渲染。原因：
 *
 *   agent/main_agent.py 用 astream(stream_mode="updates") 且未开 subgraphs，
 *   子图（子智能体）内部的消息不会进入 chunk。实测一次完整任务，事件流里
 *   只有 assistant_call（主智能体调度子智能体）和 tool_start（主智能体自己
 *   调用的 generate_markdown / convert_md_to_pdf），**没有任何一条**子智能体
 *   内部工具（internet_search、execute_sql_query、search_knowledge_base）。
 *
 * 硬按 §8.3 嵌套，会把主智能体的工具错误地挂到最后一个子智能体名下，
 * 得出「内部知识库助手调用了 convert_md_to_pdf」这种与事实不符的结构。
 * 与其画一棵看起来有层次但归属错误的树，不如如实平铺。
 *
 * 若日后在 astream 上开启 subgraphs=True 让子图事件也推送，把 buildTree
 * 改回按 assistant_call 分组即可，EventNode 的递归渲染无需改动。
 */
import { computed } from 'vue'
import EventNode from './EventNode.vue'

const props = defineProps({
  events: { type: Array, default: () => [] },
})

/**
 * 把平铺事件转成节点。
 *
 * 目前所有事件都是主智能体的直接产物，故一律作为「主智能体」的子节点，
 * 彼此平级。保留 children 字段是为了兼容将来出现真正的嵌套。
 */
function buildTree(events) {
  return events.map((e, i) => ({ ...e, id: `n${i}`, children: [] }))
}

/**
 * 外面再套一个「主智能体」虚拟根。
 *
 * 后端不会为主智能体本身发事件（它就是编排者），但验收要求看到
 * 「主智能体 → 子智能体 → 工具」这个完整层级，补一个虚拟根才立得住。
 */
const tree = computed(() => {
  if (!props.events.length) return []
  return [{
    id: 'root',
    event: 'main_agent',
    message: '主智能体',
    timestamp: '',
    data: {},
    children: buildTree(props.events),
  }]
})

const stats = computed(() => {
  const subagents = props.events.filter((e) => e.event === 'assistant_call').length
  const tools = props.events.filter((e) => e.event === 'tool_start').length
  return { subagents, tools }
})
</script>

<template>
  <div class="event-tree">
    <div class="head">
      <span class="title">执行事件流</span>
      <span v-if="events.length" class="stats">
        共 {{ events.length }} 条 · 子智能体 {{ stats.subagents }} · 工具 {{ stats.tools }}
      </span>
    </div>

    <div v-if="!events.length" class="empty">
      提交任务后，这里会实时展示「主智能体 → 子智能体 → 工具」的执行层级。
    </div>

    <div v-else class="body">
      <EventNode v-for="node in tree" :key="node.id" :node="node" :depth="0" />
    </div>
  </div>
</template>

<style scoped>
.event-tree { flex: 1; display: flex; flex-direction: column; min-height: 0; background: #fff; }

.head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid #e5e7eb;
}
.title { font-weight: 600; font-size: 14px; }
.stats { font-size: 12px; color: #6b7280; }

.empty { padding: 40px 20px; color: #9ca3af; font-size: 13px; text-align: center; }

.body { flex: 1; overflow: auto; padding: 12px 20px 24px; }
</style>
