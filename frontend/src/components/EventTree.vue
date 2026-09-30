<script setup>
/**
 * 智能体执行树
 *
 * 【层级是怎么建的，以及为什么长这样】
 * TDD §8.3 设想的是「主智能体 → 子智能体 → 工具调用」三级。实测（见阶段 10 的
 * 结论）**第三级拿不到**：agent/main_agent.py 的 astream 用 stream_mode="updates"
 * 且未开 subgraphs=True，子图内部的消息进不了 chunk，所以子智能体自己调用的
 * internet_search / execute_sql_query / search_knowledge_base **一条都不推送**。
 * 事件流里只有：主智能体调度子智能体（assistant_call）和主智能体自己的工具
 * （tool_start）。
 *
 * 因此这里建成：
 *   主智能体（虚拟根）
 *     ├─ 子智能体分组（网络检索 / 数据库 / 知识库）→ 该子智能体的调度记录
 *     ├─ 主智能体工具调用（平级，因为它们确实发生在主智能体这一层）
 *     └─ 会话 / 结果 / 错误
 * 子智能体分组**没有工具子节点**，不是渲染漏了，是后端没推——分组标题上写明了。
 *
 * 【分组会打乱时序】同一子智能体在不同轮次的调度被聚到一起，与其它事件严格
 * 的时间顺序就不再生效。选择分组是因为需求明确要"独立分组"，且同样的配色能让
 * 跨轮次的重复调度一眼看出是同一个数据源。每条事件仍保留自己的时间戳。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { Delete } from '@element-plus/icons-vue'
import EventNode from './EventNode.vue'
import { SUBAGENT_TO_SOURCE } from '../reflect'

const props = defineProps({
  events: { type: Array, default: () => [] },
  threadId: { type: String, default: '' },
  // idle | connecting | running | done | error | cancelled
  status: { type: String, default: 'idle' },
})

const emit = defineEmits(['clear'])

/** 反转子智能体名 → 数据源标识，用于分组的配色与说明 */
const SUBAGENT_SOURCE_LABEL = {
  网络检索助手: '网络检索',
  数据库查询助手: '数据库',
  内部知识库助手: '内部知识库',
}

/** 日志筛选：all | subagent | tool | other */
const filter = ref('all')

const FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'subagent', label: '仅子智能体' },
  { value: 'tool', label: '仅工具' },
  { value: 'other', label: '仅会话/结果' },
]

function matchFilter(e) {
  if (filter.value === 'all') return true
  if (filter.value === 'subagent') return e.event === 'assistant_call'
  if (filter.value === 'tool') return e.event === 'tool_start'
  return e.event === 'session_created' || e.event === 'task_result' || e.event === 'error'
}

const visible = computed(() => props.events.filter(matchFilter))

/** 统计（始终按全量算，不受筛选影响，否则数字会随筛选跳动让人困惑） */
const stats = computed(() => {
  let subagents = 0
  let tools = 0
  for (const e of props.events) {
    if (e.event === 'assistant_call') subagents += 1
    else if (e.event === 'tool_start') tools += 1
  }
  return { subagents, tools }
})

/**
 * 给每条事件算"到下一条事件的间隔"。
 *
 * 后端事件里没有耗时字段，也没有工具结束事件，所以只能用相邻事件的时间差作为
 * 一个**可观测的代理**，并在 UI 上标明口径。最后一条没有下一条，留空（显示为空
 * 而不是 0，0 会被误读成瞬间完成）。
 */
function withElapsed(list) {
  return list.map((e, i) => {
    let elapsed = null
    if (i < list.length - 1) {
      const a = Date.parse(e.timestamp)
      const b = Date.parse(list[i + 1].timestamp)
      if (!Number.isNaN(a) && !Number.isNaN(b) && b >= a) elapsed = b - a
    }
    // fresh：只有最新一条做淡入动画，否则整棵树会反复重放动画
    return { ...e, elapsed, fresh: i === list.length - 1 }
  })
}

/**
 * 建树：按子智能体分组，其余事件保持原序穿插。
 *
 * 做法是先按"首次出现的位置"给每个子智能体分组定序，再把非分组事件按各自的
 * 原始下标插入，得到一个既分了组、又大致保持先来后到的序列。
 */
const tree = computed(() => {
  const list = withElapsed(visible.value)
  if (!list.length) return []

  // 1. 收集分组：子智能体名 → 该子智能体的全部调度事件
  const groups = new Map()
  const plain = [] // { item, index }
  list.forEach((e, i) => {
    if (e.event === 'assistant_call' && e.data?.assistant_name) {
      const name = e.data.assistant_name
      if (!groups.has(name)) groups.set(name, { name, firstIndex: i, items: [] })
      groups.get(name).items.push(e)
    } else {
      plain.push({ item: e, index: i })
    }
  })

  // 2. 合并成有序块：块用首次出现下标排序，普通事件用自身下标
  const blocks = [
    ...[...groups.values()].map((g) => ({ kind: 'group', index: g.firstIndex, group: g })),
    ...plain.map((p) => ({ kind: 'event', index: p.index, item: p.item })),
  ].sort((a, b) => a.index - b.index)

  // 3. 组装节点
  const children = blocks.map((b, bi) => {
    if (b.kind === 'event') return { ...b.item, id: `e${b.index}`, children: [] }

    const g = b.group
    const label = SUBAGENT_SOURCE_LABEL[g.name] || g.name
    return {
      id: `g${bi}-${g.name}`,
      kind: 'group',
      event: 'assistant_call',
      name: g.name,
      message: `${label}助手`,
      timestamp: g.items[0]?.timestamp || '',
      data: { assistant_name: g.name },
      // 分组下挂的就是它的调度记录；子智能体**内部**的工具调用后端不推送，
      // 所以这里不会再往下长出第三级
      children: g.items.map((it, ii) => ({ ...it, id: `g${bi}-${ii}`, children: [] })),
      subagentSource: SUBAGENT_TO_SOURCE[g.name] || '',
      dispatchCount: g.items.length,
    }
  })

  return [
    {
      id: 'root',
      kind: 'root',
      event: 'main_agent',
      message: '主智能体',
      timestamp: '',
      data: {},
      running: props.status === 'running' || props.status === 'connecting',
      children,
    },
  ]
})

/** 网络检索被调度的次数（≥3 时高亮提醒） */
const webCalls = computed(() =>
  props.events.filter(
    (e) => e.event === 'assistant_call' && e.data?.assistant_name === '网络检索助手'
  ).length
)

// ── 自动滚动 ──
const bodyRef = ref(null)
const autoScroll = ref(true)

function onScroll() {
  const el = bodyRef.value
  if (!el) return
  // 距底部 60px 内视为"跟随"，用户手动往上翻则暂停自动滚动，免得抢滚动条
  autoScroll.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

watch(
  () => props.events.length,
  async () => {
    if (!autoScroll.value) return
    await nextTick()
    const el = bodyRef.value
    if (el) el.scrollTop = el.scrollHeight
  }
)

const statusText = computed(
  () =>
    ({
      idle: '待提交',
      connecting: '连接中…',
      running: '执行中…',
      done: '已完成',
      error: '出错',
      cancelled: '已终止',
    })[props.status] || props.status
)

/** 顶部状态标签配色。已终止用灰，不报红——是用户主动停的 */
const statusType = computed(
  () =>
    ({
      error: 'danger',
      done: 'success',
      cancelled: 'info',
      idle: 'info',
    })[props.status] || 'warning'
)
</script>

<template>
  <div class="event-tree">
    <!-- ── 全局控制栏 ── -->
    <div class="control">
      <span class="title">智能体执行树</span>

      <span v-if="threadId" class="ty-mono tid">{{ threadId }}</span>

      <el-tag size="small" :type="statusType" effect="light">{{ statusText }}</el-tag>

      <span class="spacer"></span>

      <span class="stats">
        共 {{ events.length }} 条 · 子智能体 {{ stats.subagents }} · 工具 {{ stats.tools }}
      </span>

      <!--
        进度：后端没有进度事件，也没有百分比可算，因此用**不确定态**动画，
        只额外给出已收到的步骤数。不编造百分比。
      -->
      <div class="progress" title="后端未提供任务进度事件，此处仅为不确定态指示">
        <el-progress
          :percentage="100"
          :indeterminate="status === 'running' || status === 'connecting'"
          :duration="3"
          :show-text="false"
          :stroke-width="4"
          :status="status === 'error' ? 'exception' : undefined"
          class="bar"
        />
        <span class="steps">{{ events.length }} 步</span>
      </div>

      <el-select v-model="filter" size="small" class="filter" style="width: 108px">
        <el-option v-for="f in FILTERS" :key="f.value" :label="f.label" :value="f.value" />
      </el-select>

      <!--
        终止任务按钮已移除。后端 POST /api/cancel 仍保留且已测通（见 api/server.py），
        需要一个后端重启才会加载该路由；重启前点它只会 404，所以先从界面上取下。
      -->
      <el-button size="small" :icon="Delete" @click="emit('clear')">清空日志</el-button>
    </div>

    <!-- ── 网络检索调度提醒 ── -->
    <el-alert
      v-if="webCalls >= 3"
      type="warning"
      :closable="false"
      show-icon
      class="web-alert"
      title="网络检索助手已被调度 3 次及以上"
    >
      <template #default>
        <span class="alert-body">
          注意口径：这里的 3 次是<b>子智能体被调度</b>的次数。真正的"最多检索 3 次"规则写在
          网络检索助手的 system prompt 里（prompt/prompts.yaml），由模型自觉遵守，
          <b>既不由代码强制，也不产生事件</b>，所以前端看不到它内部实际检索了几次。
        </span>
      </template>
    </el-alert>

    <!-- ── 树主体 ── -->
    <div ref="bodyRef" class="body" @scroll="onScroll">
      <el-empty
        v-if="!tree.length"
        description="提交任务后，这里会实时展示执行过程"
        :image-size="80"
      />
      <EventNode v-for="node in tree" v-else :key="node.id" :node="node" :depth="0" />

      <!-- 分组说明：补一句为什么第三级是空的 -->
      <p v-if="tree.length && stats.subagents" class="footnote">
        子智能体分组下只有调度记录。子智能体<b>内部</b>的工具调用（internet_search /
        execute_sql_query / search_knowledge_base）后端不推送，因此没有工具子节点。
      </p>
    </div>
  </div>
</template>

<style scoped>
/* 独立玻璃面板：圆角 + 模糊，让外层极光底纹透进来 */
.event-tree {
  flex: 1;
  margin: 12px 16px 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--ty-surface);
  backdrop-filter: var(--ty-glass);
  -webkit-backdrop-filter: var(--ty-glass);
  border: 1px solid var(--ty-border);
  border-radius: var(--ty-radius);
  box-shadow: var(--ty-shadow-1);
  overflow: hidden;
}

/* ── 控制栏 ── */
.control {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--ty-border);
  flex-wrap: wrap;
  /* 极淡的顶光，和卡片顶部那道发丝线呼应 */
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--ty-primary) 6%, transparent),
    transparent
  );
}
.title { font-weight: 700; font-size: 13px; white-space: nowrap; letter-spacing: 0.02em; }
.tid {
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.spacer { flex: 1; }
.stats { font-size: 11px; color: var(--ty-text-soft); white-space: nowrap; }

.progress { display: flex; align-items: center; gap: 6px; }
.bar { width: 96px; }
.steps { font-size: 11px; color: var(--ty-text-faint); white-space: nowrap; }

.filter { flex: 0 0 auto; }

.web-alert { margin: 8px 14px 0; }
.alert-body { font-size: 12px; line-height: 1.6; }
.tipbox { font-size: 12px; line-height: 1.6; max-width: 320px; }

/* ── 树主体 ── */
.body {
  flex: 1;
  overflow: auto;
  padding: 12px 14px 24px;
}

.footnote {
  margin: 14px 0 0;
  padding: 8px 10px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--ty-text-soft);
  background: var(--ty-surface-soft);
  border: 1px dashed var(--ty-border);
  border-radius: var(--ty-radius);
}
</style>
