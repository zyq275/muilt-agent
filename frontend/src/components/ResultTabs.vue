<script setup>
/**
 * 结果预览 Tab
 *
 * Tab1 纯问答文本 / Tab2 Markdown 报告预览+复制 / Tab3 PDF 在线预览。
 *
 * 【Tab3 的显示条件】需求三.4 要求"纯问答任务不展示 PDF 成果入口"，所以这个
 * Tab 只在**当前会话确实产出了 .pdf 文件**时才出现。判定依据是文件列表接口返回
 * 的真实文件，不是猜的。
 *
 * 【PDF 预览走下载接口】后端没有单独的预览接口，但 GET /api/download 返回的是
 * FileResponse，Content-Disposition 为 attachment。浏览器内嵌 iframe 时，部分
 * 浏览器对 attachment 仍会用内置 PDF 阅读器渲染（Chrome/Edge 可以）。若所在
 * 浏览器直接触发下载而不渲染，退化为"下载后本地打开"——旁边保留了下载按钮。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { CopyDocument, Download, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { downloadUrl } from '../api'

const props = defineProps({
  /** 报告正文（来自 task_result 的 data.result） */
  report: { type: String, default: '' },
  /** 当前会话产出的 .pdf 绝对路径，无则 Tab3 不显示 */
  pdfPath: { type: String, default: '' },
  /** 当前会话产出的 .md 绝对路径 */
  mdPath: { type: String, default: '' },
})

const active = ref('text')

/** PDF 预览缩放（需求要求可缩放） */
const zoom = ref(100)

const hasPdf = computed(() => Boolean(props.pdfPath))

/** 有 PDF 时默认停在 Markdown 页（PDF 体积极大，不自动拉取） */
watch(
  () => props.report,
  (v) => {
    if (v) active.value = hasPdf.value ? 'markdown' : 'text'
  }
)

// ── PDF 预览 ──────────────────────────────────────────
//
// 【为什么不能把 /api/download 的地址直接给 iframe】
// 后端的 FileResponse 带 Content-Disposition: attachment、Content-Type 是
// application/octet-stream，而 iframe 加载 URL 属于**导航**，浏览器看到 attachment
// 就直接下载。再叠加 Element Plus 的 el-tab-pane 默认**预渲染全部面板**，
// 结果就是：页面一刷新，那个隐藏的 iframe 立刻发请求 → 浏览器自动下载 PDF。
//
// 做法：用 fetch 把文件读成 ArrayBuffer，重新包成**显式标注 application/pdf**
// 的 Blob，再指向它的 blob: URL。blob: 导航不受响应头影响，内置阅读器能正常渲染。
//
// 同时给面板加 lazy，双保险——不在 PDF 页时连 fetch 都不会发生。
const pdfUrl = ref('')
const pdfLoading = ref(false)
const pdfError = ref('')

async function loadPdf() {
  // 已有就不再拉；正在拉也不重复拉
  if (!props.pdfPath || pdfUrl.value || pdfLoading.value) return

  pdfLoading.value = true
  pdfError.value = ''
  try {
    const resp = await fetch(downloadUrl(props.pdfPath))
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const buf = await resp.arrayBuffer()
    pdfUrl.value = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }))
  } catch (e) {
    pdfError.value = `加载 PDF 失败：${e.message}`
  } finally {
    pdfLoading.value = false
  }
}

/** 释放 blob URL，避免占着内存不放 */
function releasePdf() {
  if (pdfUrl.value) {
    URL.revokeObjectURL(pdfUrl.value)
    pdfUrl.value = ''
  }
}

/** 换会话/换产物就作废旧的 blob */
watch(() => props.pdfPath, () => {
  releasePdf()
  pdfError.value = ''
  if (active.value === 'pdf') loadPdf()
})

/** 切到 PDF 页时才真正去读文件 */
watch(active, (v) => {
  if (v === 'pdf') loadPdf()
})

onBeforeUnmount(releasePdf)

/**
 * 轻量 Markdown → HTML。
 *
 * 刻意不引第三方渲染库：
 *   1. 报告是模型生成的文本，直接 innerHTML 有 XSS 风险，这里**先整体转义**
 *      再做少量替换，转义后任何标签都不可能被还原成可执行内容；
 *   2. 需要的只是标题/加粗/行内码/列表/表格/分割线，自己几十行足够，
 *      不值得为它多一个依赖。
 * 因此这是"够看"级别的渲染，不是完整 CommonMark 实现。
 */
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function mdToHtml(md) {
  if (!md) return ''
  const lines = escapeHtml(md).split('\n')
  const out = []
  let inList = false
  let inTable = false

  const closeList = () => {
    if (inList) {
      out.push('</ul>')
      inList = false
    }
  }
  const closeTable = () => {
    if (inTable) {
      out.push('</tbody></table>')
      inTable = false
    }
  }

  for (const raw of lines) {
    const line = raw.trimEnd()

    // 表格：以 | 开头且以 | 结尾
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line
        .trim()
        .replace(/^\||\|$/g, '')
        .split('|')
        .map((c) => c.trim())
      // 分隔行 |---|---| 跳过
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue
      closeList()
      if (!inTable) {
        out.push('<table><tbody>')
        inTable = true
        out.push(`<tr>${cells.map((c) => `<th>${inline(c)}</th>`).join('')}</tr>`)
      } else {
        out.push(`<tr>${cells.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
      }
      continue
    }
    closeTable()

    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      closeList()
      out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`)
      continue
    }

    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      if (!inList) {
        out.push('<ul>')
        inList = true
      }
      out.push(`<li>${inline(line.replace(/^\s*([-*+]|\d+\.)\s+/, ''))}</li>`)
      continue
    }
    closeList()

    if (/^\s*([-*_]\s*){3,}$/.test(line)) {
      out.push('<hr>')
      continue
    }

    if (!line.trim()) continue
    out.push(`<p>${inline(line)}</p>`)
  }
  closeList()
  closeTable()
  return out.join('\n')
}

/** 行内元素：加粗、行内代码 */
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}

const renderedHtml = computed(() => mdToHtml(props.report))

async function copyReport() {
  try {
    await navigator.clipboard.writeText(props.report)
    ElMessage.success('报告已复制到剪贴板')
  } catch {
    // 剪贴板 API 在非 HTTPS 或用户拒绝授权时不可用
    ElMessage.warning('复制失败：浏览器未授权剪贴板访问')
  }
}

function zoomBy(delta) {
  zoom.value = Math.min(200, Math.max(50, zoom.value + delta))
}
</script>

<template>
  <div class="result-tabs ty-card">
    <el-tabs v-model="active" class="tabs">
      <!-- Tab1 纯问答文本 -->
      <el-tab-pane label="文本" name="text">
        <div class="pane">
          <pre v-if="report" class="plain">{{ report }}</pre>
          <el-empty v-else description="任务完成后显示结果" :image-size="70" />
        </div>
      </el-tab-pane>

      <!-- Tab2 Markdown 预览 + 复制 -->
      <el-tab-pane label="Markdown 报告" name="markdown">
        <div class="pane">
          <div class="toolbar">
            <el-button size="small" :icon="CopyDocument" :disabled="!report" @click="copyReport">
              复制全文
            </el-button>
            <el-button
              v-if="mdPath"
              size="small"
              :icon="Download"
              tag="a"
              :href="downloadUrl(mdPath)"
            >
              下载 .md
            </el-button>
            <span class="dim">轻量渲染，不含完整 Markdown 语法</span>
          </div>
          <div v-if="report" class="md ty-md" v-html="renderedHtml"></div>
          <el-empty v-else description="任务完成后显示报告" :image-size="70" />
        </div>
      </el-tab-pane>

      <!--
        Tab3 PDF 在线预览（仅在确有 PDF 产出时出现，见需求三.4）

        lazy 是关键：不加的话 el-tab-pane 会**预渲染**本面板，页面一刷新
        这个隐藏的 iframe 就去请求文件；配合后端的 Content-Disposition: attachment，
        浏览器会直接弹下载。lazy 保证只有真正切到这一页才渲染。
      -->
      <el-tab-pane v-if="hasPdf" label="PDF 预览" name="pdf" lazy>
        <div class="pane">
          <div class="toolbar">
            <el-button size="small" :icon="ZoomOut" @click="zoomBy(-10)">缩小</el-button>
            <span class="zoom">{{ zoom }}%</span>
            <el-button size="small" :icon="ZoomIn" @click="zoomBy(10)">放大</el-button>
            <el-button size="small" :icon="Download" tag="a" :href="downloadUrl(pdfPath)">
              下载 PDF
            </el-button>
          </div>

          <div v-loading="pdfLoading" class="pdf-wrap">
            <p v-if="pdfError" class="warn">{{ pdfError }}</p>
            <el-empty
              v-else-if="!pdfUrl && !pdfLoading"
              description="切换到本页时加载 PDF"
              :image-size="60"
            />
            <iframe
              v-else-if="pdfUrl"
              class="pdf"
              :src="pdfUrl"
              :style="{ transform: `scale(${zoom / 100})` }"
              title="PDF 预览"
            ></iframe>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.result-tabs {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0 13px 9px;
}
.tabs { min-height: 0; }
:deep(.el-tabs__header) { margin-bottom: 8px; }
:deep(.el-tabs__content) { overflow: visible; }

/* Tab 栏：去掉默认底边，换成一道极淡的分隔；选中项用主色 + 渐变下划线 */
:deep(.el-tabs__nav-wrap::after) {
  height: 1px;
  background: var(--ty-border);
}
:deep(.el-tabs__item) {
  font-weight: 600;
  font-size: 13px;
  color: var(--ty-text-soft);
  transition: color 0.18s;
}
:deep(.el-tabs__item:hover) { color: var(--ty-primary); }
:deep(.el-tabs__item.is-active) { color: var(--ty-primary); }
:deep(.el-tabs__active-bar) {
  height: 2px;
  border-radius: 2px;
  background-image: var(--ty-grad-primary);
  box-shadow: 0 0 8px var(--ty-glow);
}

.pane {
  max-height: 300px;
  overflow: auto;
  padding-bottom: 6px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 6px 8px;
  flex-wrap: wrap;
  border-radius: var(--ty-radius-sm);
  background: var(--ty-surface-soft);
  border: 1px solid var(--ty-border);
}
.zoom {
  font-size: 12px;
  color: var(--ty-text-soft);
  font-variant-numeric: tabular-nums;
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
  min-width: 42px;
  text-align: center;
}
.dim { font-size: 11px; color: var(--ty-text-faint); }

.warn {
  margin: 8px 0;
  font-size: 12px;
  color: var(--ty-warning);
}

.plain {
  margin: 0;
  padding: 11px 12px;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ty-text);
  background: var(--ty-surface-soft);
  border: 1px solid var(--ty-border);
  border-radius: var(--ty-radius-sm);
}

/* PDF 缩放用 transform，容器裁剪溢出即可 */
.pdf-wrap {
  height: 280px;
  overflow: auto;
  border: 1px solid var(--ty-border);
  border-radius: var(--ty-radius-sm);
  background: var(--ty-surface-sunken);
}
.pdf {
  width: 100%;
  height: 100%;
  border: 0;
  transform-origin: top left;
  transition: transform 0.18s ease;
}
</style>
