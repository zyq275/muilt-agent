<script setup>
/**
 * 左侧会话栏（220px）
 *
 * LOGO + 统计卡片 + 会话列表 + 快捷模板。
 *
 * 【重要边界】这份会话列表来自浏览器 localStorage，不是服务端会话总账 ——
 * 后端没有"列出所有会话"的接口。因此：
 *   - 只显示**本浏览器提交过**的任务；
 *   - "删除"只删本地索引，**不会删除服务端产物**（后端无删除接口）；
 *   - "重跑"只能重新提交同一个任务描述并生成**新的 thread_id**，做不到在
 *     原会话上续跑（后端每次 POST /api/task 都是一次独立执行）。
 * 这三条都直接写在界面上，不能让用户以为这里有它没有的能力。
 */
import { computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { STATUS, STATUS_LABEL } from '../sessionStore'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentThreadId: { type: String, default: '' },
})

const emit = defineEmits(['select', 'rerun', 'remove', 'template'])

/** 统计卡片。数值由 App 传入，避免这里重复遍历一遍列表。 */
const stats = computed(() => {
  const s = { running: 0, done: 0, error: 0 }
  for (const item of props.sessions) {
    if (item.status in s) s[item.status] += 1
  }
  return s
})

/** 一键填充的快捷模板（只填进输入框，不直接提交） */
const TEMPLATES = [
  { label: '药品市场分析', text: '分析当前药品市场的整体情况，生成一份市场分析报告（不少于1000字），并生成PDF' },
  { label: '销售周报', text: '根据数据库中的销售数据，生成本周销售周报，包含销量排行与环比变化，并生成PDF' },
  { label: '库存盘点', text: '盘点当前药品库存情况，列出库存偏低与临近有效期的品种，生成PDF报告' },
  { label: '药品清单查询', text: '查询数据库中全部药品清单，按类别整理成表格' },
]

/** 状态标签配色。已终止用 info 而非 danger——是用户主动停的，不是失败。 */
const STATUS_TYPE = {
  [STATUS.RUNNING]: 'warning',
  [STATUS.DONE]: 'success',
  [STATUS.ERROR]: 'danger',
  [STATUS.CANCELLED]: 'info',
}

function shortTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function onSelect(item) {
  if (item.threadId === props.currentThreadId) return
  emit('select', item)
}

/**
 * 重跑：确认后把原任务描述交回上层重新提交。
 *
 * 明确告知会开一个新会话——用户点"重跑"时很可能以为是在原会话上继续。
 */
async function onRerun(item) {
  try {
    await ElMessageBox.confirm(
      `将用新的会话标识重新提交这条任务。原会话 ${item.threadId} 的产物不会被覆盖。`,
      '重跑任务',
      { confirmButtonText: '重跑', cancelButtonText: '取消', type: 'info' }
    )
  } catch {
    return // 用户取消
  }
  emit('rerun', item)
}

/**
 * 删除：只删本地记录。
 *
 * 这是最容易被误解的操作，所以文案必须说清"文件还在"。
 */
async function onRemove(item) {
  try {
    await ElMessageBox.confirm(
      '仅从本机列表中移除这条记录，服务端已生成的报告文件不会被删除（后端没有删除接口）。',
      '移除会话记录',
      { confirmButtonText: '移除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  emit('remove', item)
  ElMessage.success('已从本机列表移除')
}
</script>

<template>
  <aside class="sidebar">
    <!-- LOGO -->
    <div class="logo">
      <span class="mark">探</span>
      <div class="words">
        <div class="name">探渊</div>
        <div class="sub">多智能体数据分析系统</div>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="stats">
      <div class="stat running">
        <div class="num">{{ stats.running }}</div>
        <div class="cap">运行中</div>
      </div>
      <div class="stat done">
        <div class="num">{{ stats.done }}</div>
        <div class="cap">已完成</div>
      </div>
      <div class="stat error">
        <div class="num">{{ stats.error }}</div>
        <div class="cap">失败</div>
      </div>
    </div>

    <!-- 会话列表 -->
    <div class="list-head">
      <span>会话</span>
      <span class="count">{{ sessions.length }}</span>
    </div>

    <div class="list">
      <!-- 空状态兜底 -->
      <el-empty
        v-if="!sessions.length"
        description="还没有会话，提交一个任务试试"
        :image-size="60"
      />

      <div
        v-for="item in sessions"
        :key="item.threadId"
        class="session ty-card"
        :class="{ active: item.threadId === currentThreadId }"
        @click="onSelect(item)"
      >
        <div class="row">
          <span class="title" :title="item.query">{{ item.title }}</span>
          <el-tag :type="STATUS_TYPE[item.status]" size="small" effect="light">
            {{ STATUS_LABEL[item.status] || item.status }}
          </el-tag>
        </div>

        <div class="meta">
          <span class="time">{{ shortTime(item.createdAt) }}</span>
          <span class="tid" :title="item.threadId">{{ item.threadId }}</span>
        </div>

        <div class="ops" @click.stop>
          <el-button link size="small" :icon="VideoPlay" @click="onSelect(item)">切换</el-button>
          <el-button link size="small" :icon="RefreshRight" @click="onRerun(item)">重跑</el-button>
          <el-button link size="small" type="danger" :icon="Delete" @click="onRemove(item)">
            删除
          </el-button>
        </div>
      </div>
    </div>

    <!-- 快捷模板 -->
    <div class="templates">
      <div class="list-head"><span>快捷模板</span></div>
      <div class="chips">
        <el-button
          v-for="t in TEMPLATES"
          :key="t.label"
          size="small"
          class="chip"
          @click="emit('template', t.text)"
        >
          {{ t.label }}
        </el-button>
      </div>
      <p class="note">会话列表仅存于本机浏览器；删除不移除服务端文件。</p>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--ty-sidebar-w);
  flex: 0 0 var(--ty-sidebar-w);
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--ty-surface);
  backdrop-filter: var(--ty-glass);
  -webkit-backdrop-filter: var(--ty-glass);
  border-right: 1px solid var(--ty-border);
}

/* ── LOGO ── */
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 15px 14px 13px;
  border-bottom: 1px solid var(--ty-border);
}
.mark {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background-image: var(--ty-grad-primary);
  color: #fff;
  font-size: 17px;
  font-weight: 800;
  box-shadow: 0 3px 14px -3px var(--ty-glow), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}
.name { font-size: 15px; font-weight: 800; line-height: 1.2; letter-spacing: 0.02em; }
.sub { font-size: 10px; color: var(--ty-text-faint); letter-spacing: 0.03em; }

/* ── 统计卡片 ──
   每张卡顶部一条语义色，数字用同色，一眼分辨三态 */
.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 7px;
  padding: 12px;
  border-bottom: 1px solid var(--ty-border);
}
.stat {
  position: relative;
  text-align: center;
  padding: 9px 2px 7px;
  border-radius: var(--ty-radius-sm);
  background: var(--ty-surface-soft);
  border: 1px solid var(--ty-border);
  overflow: hidden;
  transition: transform 0.18s, box-shadow 0.18s;
}
.stat:hover { transform: translateY(-2px); box-shadow: var(--ty-shadow-2); }
.stat::before {
  content: '';
  position: absolute;
  inset: 0 0 auto;
  height: 2px;
  background: var(--ty-text-faint);
  opacity: 0.9;
}
.stat.running::before { background-image: var(--ty-grad-primary); }
.stat.done::before { background-image: var(--ty-grad-success); }
.stat.error::before { background-image: var(--ty-grad-danger); }

.stat .num {
  font-size: 19px;
  font-weight: 800;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.stat .cap { font-size: 10px; color: var(--ty-text-faint); margin-top: 3px; }
.stat.running .num { color: var(--ty-primary); }
.stat.done .num { color: var(--ty-success); }
.stat.error .num { color: var(--ty-error); }

/* ── 列表 ── */
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 7px;
  font-size: 11px;
  font-weight: 700;
  color: var(--ty-text-soft);
  letter-spacing: 0.04em;
}
.count {
  color: var(--ty-text-faint);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 10px 8px;
}

/* 会话卡：左侧一条状态色竖条，选中时整条点亮 */
.session {
  position: relative;
  padding: 9px 10px 9px 13px;
  margin-bottom: 7px;
  cursor: pointer;
  overflow: hidden;
  border-radius: var(--ty-radius-sm);
}
/* 列表项不再叠加 ty-card 的顶部发丝线——几十张卡片各来一道会显得很碎 */
.session::after { display: none; }
.session::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--ty-border);
  transition: background 0.2s, box-shadow 0.2s;
}
.session:hover::before { background: color-mix(in srgb, var(--ty-primary) 55%, transparent); }
.session.active {
  border-color: color-mix(in srgb, var(--ty-primary) 50%, transparent);
  background: var(--ty-primary-weak);
  box-shadow: 0 0 0 3px var(--ty-primary-ring);
}
.session.active::before {
  background-image: var(--ty-grad-primary);
  box-shadow: 0 0 10px var(--ty-glow);
}

.session .row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 6px;
}
.session .title {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  word-break: break-word;
}

.session .meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: 5px;
  font-size: 10px;
  color: var(--ty-text-faint);
}
.session .tid {
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 108px;
}

.session .ops {
  display: flex;
  gap: 2px;
  margin-top: 5px;
  padding-top: 5px;
  border-top: 1px dashed var(--ty-border);
}

/* ── 快捷模板 ── */
.templates {
  border-top: 1px solid var(--ty-border);
  padding-bottom: 12px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 12px;
}
/* 模板做成"胶囊芯片"，而不是标准按钮——区隔于操作按钮 */
.chip {
  margin: 0 !important;
  border-radius: 999px !important;
  font-weight: 600;
  background: var(--ty-surface-soft);
}
.chip:hover {
  border-color: var(--ty-primary) !important;
  color: var(--ty-primary) !important;
  background: var(--ty-primary-weak) !important;
  box-shadow: 0 0 0 3px var(--ty-primary-ring);
}
.note {
  margin: 10px 12px 0;
  font-size: 10px;
  line-height: 1.55;
  color: var(--ty-text-faint);
}
</style>
