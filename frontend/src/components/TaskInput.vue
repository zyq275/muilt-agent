<script setup>
/**
 * 任务输入栏（底部固定）
 *
 * 多行自适应（最高 6 行）、示例任务下拉、强制 PDF 开关、提交按钮。
 *
 * 【「强制生成 PDF 报告」为什么要动 query 文本】
 * POST /api/task 的请求体只有 {query, thread_id} 两个字段，**没有 require_pdf 这类
 * 开关**。后端判定是否要出 PDF 的方式是 agent/main_agent.py 里的
 * infer_require_pdf()，它只做一件事：看 query 里有没有 "pdf" 这个词。
 *
 * 所以这个勾选框唯一能生效的做法就是往 query 末尾补一句要求生成 PDF 的话。这不是
 * "调用了某个接口"，是**顺着后端的既有判定口径说话**。副作用是：即便后端将来改成
 * 别的关键词，这里仍会发这段文字——它本身也是一句合法需求描述，不会产生坏数据。
 * 撤销勾选时按标记原样移除，不会重复叠加。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { Promotion, VideoPlay } from '@element-plus/icons-vue'

const props = defineProps({
  /** 提交中（禁掉重复提交） */
  loading: { type: Boolean, default: false },
  /** 当前会话标识，用于提示这次是"继续"还是"新建" */
  threadId: { type: String, default: '' },
})

const emit = defineEmits(['submit'])

const text = ref('')
const requirePdf = ref(false)
const textareaRef = ref(null)

/** 示例任务（三.1 要求下拉可选） */
const EXAMPLES = [
  '查询数据库里 2024 年销售额最高的 10 个药品，并检索网络上的对应适应症资料，生成一份分析报告。',
  '结合内部知识库与网络公开资料，综述 GLP-1 受体激动剂的市场格局与主要玩家。',
  '统计数据库中各治疗领域的药品数量分布，并说明数据来源与口径。',
  '这个药有什么副作用？',
]

/** 注入标记：撤销时靠它精确删除，不会误伤用户自己写的 "pdf" */
const PDF_MARK = '\n\n请生成 PDF 格式的报告文件。'

/** 真正发给后端的文本 */
const finalQuery = computed(() =>
  requirePdf.value ? `${text.value.trim()}${PDF_MARK}` : text.value.trim()
)

const canSubmit = computed(() => !props.loading && finalQuery.value.length > 0)

/**
 * 自适应高度：先归零再按 scrollHeight 撑开，超过 6 行就交给 max-height 出滚动条。
 * 先设 auto 是为了让高度能**回落**——不重置的话删字后框不会缩。
 */
function autoGrow() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 6 * 22 + 20)}px`
}

watch(text, () => nextTick(autoGrow))

function pickExample(v) {
  text.value = v
  nextTick(autoGrow)
  textareaRef.value?.focus()
}

function submit() {
  if (!canSubmit.value) return
  emit('submit', finalQuery.value)
}

/** Ctrl/Cmd + Enter 提交；单独 Enter 保留换行 */
function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    submit()
  }
}

defineExpose({
  /** 成功后由外层调用，清掉输入框 */
  clear() {
    text.value = ''
    requirePdf.value = false
    nextTick(autoGrow)
  },
  /** 把示例塞进去（侧边栏点击模板时用） */
  fill(v) {
    pickExample(v)
  },
})
</script>

<template>
  <div class="task-input">
    <div class="row">
      <el-select
        class="examples"
        placeholder="示例任务"
        size="small"
        :model-value="''"
        @change="pickExample"
      >
        <el-option
          v-for="(e, i) in EXAMPLES"
          :key="i"
          :label="e.length > 34 ? `${e.slice(0, 34)}…` : e"
          :value="e"
        />
        <template #prefix>
          <el-icon><Promotion /></el-icon>
        </template>
      </el-select>

      <el-checkbox v-model="requirePdf" size="small" class="pdf-check">
        强制生成 PDF 报告
      </el-checkbox>

      <el-tooltip placement="top" :show-after="300">
        <template #content>
          <div class="tipbox">
            后端 POST /api/task 只接受 query 和 thread_id，没有 PDF 开关。<br />
            勾选后会往任务描述末尾追加一句「请生成 PDF 格式的报告文件。」，<br />
            因为后端就是靠 <code>infer_require_pdf()</code> 检查 query 里有没有
            "pdf" 这个词来决定出不出 PDF 的。
          </div>
        </template>
        <el-icon class="info">?</el-icon>
      </el-tooltip>

      <span class="spacer"></span>

      <!--
        说明这次写入哪个会话。会话标识由前端生成并保持不变，所以同一会话里可以
        先上传文件再提交任务，两者落在同一个 upload/session_{tid}/ 上。
      -->
      <span class="target" :title="threadId">
        {{ threadId ? `写入会话 ${threadId}` : '正在创建会话…' }}
      </span>
    </div>

    <div class="row main">
      <el-input
        ref="textareaRef"
        v-model="text"
        type="textarea"
        :rows="2"
        resize="none"
        class="box"
        placeholder="描述你的研究任务…（Ctrl / ⌘ + Enter 提交）"
        @keydown="onKeydown"
        @input="autoGrow"
      />

      <el-button
        type="primary"
        class="send"
        :loading="loading"
        :disabled="!canSubmit"
        :icon="VideoPlay"
        @click="submit"
      >
        {{ loading ? '执行中' : '提交任务' }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.task-input {
  position: relative;
  flex: 0 0 auto;
  padding: 9px 16px 12px;
  background: var(--ty-surface);
  backdrop-filter: var(--ty-glass);
  -webkit-backdrop-filter: var(--ty-glass);
  border-top: 1px solid var(--ty-border);
}
/* 顶边一道渐变发丝线，把输入区和上面内容分开 */
.task-input::before {
  content: '';
  position: absolute;
  inset: -1px 0 auto;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    color-mix(in srgb, var(--ty-primary) 65%, transparent) 25%,
    color-mix(in srgb, var(--ty-primary-3) 65%, transparent) 70%,
    transparent
  );
  opacity: 0.7;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row.main { margin-top: 8px; align-items: flex-end; }

.examples { width: 240px; flex: 0 0 auto; }
.pdf-check { flex: 0 0 auto; }
.info {
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid var(--ty-border);
  color: var(--ty-text-faint);
  cursor: help;
  font-size: 11px;
  transition: color 0.18s, border-color 0.18s;
}
.info:hover { color: var(--ty-primary); border-color: var(--ty-primary); }
.tipbox { line-height: 1.6; font-size: 12px; max-width: 340px; }
.tipbox code {
  background: rgba(255, 255, 255, 0.15);
  padding: 0 3px;
  border-radius: 3px;
}
.spacer { flex: 1; }
/* 目标会话做成等宽胶囊，和顶栏的 threadId 视觉统一 */
.target {
  font-size: 11px;
  color: var(--ty-text-soft);
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 240px;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--ty-surface-soft);
  border: 1px solid var(--ty-border);
}

.box { flex: 1; }
/* textarea 内部才是真正滚动的元素，max-height 加在它上面 */
.box :deep(.el-textarea__inner) {
  max-height: 152px; /* 6 行 × 22 + 内边距 */
  overflow-y: auto;
  line-height: 22px;
  font-size: 13px;
  padding: 9px 12px;
  border-radius: var(--ty-radius);
  background: var(--ty-surface-soft);
  border-color: var(--ty-border);
  box-shadow: none;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
}
.box :deep(.el-textarea__inner:hover) { border-color: color-mix(in srgb, var(--ty-primary) 40%, transparent); }
.box :deep(.el-textarea__inner:focus) {
  background: var(--ty-surface);
  border-color: var(--ty-primary);
  box-shadow: 0 0 0 3px var(--ty-primary-ring);
}

/* ── 主 CTA ──
   整个页面最重要的按钮：渐变 + 光晕 + 悬停抬起，与普通按钮拉开层级 */
.send {
  height: 44px;
  flex: 0 0 auto;
  padding: 0 22px;
  margin-left: 2px;
  border-radius: var(--ty-radius);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.04em;
  background-image: var(--ty-grad-primary);
  border: none;
  color: #fff;
  box-shadow: 0 6px 20px -6px var(--ty-glow), inset 0 1px 0 rgba(255, 255, 255, 0.25);
}
.send:hover:not(.is-disabled) {
  transform: translateY(-1px);
  box-shadow: 0 10px 26px -8px var(--ty-glow), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}
.send:active:not(.is-disabled) { transform: translateY(0) scale(0.985); }
/* 选择器带上 .el-button 是必要的：theme.css 里那条主按钮失效态规则权重更高，
   只写 .send.is-disabled 会被它压过去，禁用时仍然是亮的渐变。 */
.el-button.send.is-disabled {
  background-image: none;
  background: var(--ty-surface-soft);
  color: var(--ty-text-faint);
  box-shadow: none;
  border: 1px solid var(--ty-border);
}
</style>
