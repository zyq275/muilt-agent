<script setup>
/**
 * 文件面板（双 Tab）
 *
 * Tab1 上传文件：拖拽批量上传 + 文件卡片 + Excel/CSV 表格预览
 * Tab2 会话成果：浏览当前会话目录，MD/PDF 等可直接打开预览或下载；
 *                其它会话的目录置灰，点击拦截
 *
 * 路径约定（api/server.py 的强约束）：/api/files 与 /api/download 收的是**绝对
 * 路径**，且必须落在后端结果目录内，否则 403。所以这里所有导航都直接回传后端
 * 返回的 entry.path，绝不用前端字符串自己拼——拼出来的既可能越界被拒，也可能因
 * 斜杠/盘符差异对不上。会话隔离也因此是"从会话目录往下走"天然形成的：只在
 * session_{当前 tid}/ 这个子树里导航，走不出去。
 *
 * 【Excel 预览的两条边界】
 * 1. 上传区的预览用的是**用户刚选中的 File 对象**，在上传之前就解析完。已上传的
 *    文件没有接口能读回来（/api/files 只暴露结果目录，不含上传目录），所以刷新
 *    页面后旧文件无法再预览。
 * 2. 解析在浏览器本地做，不上传服务端。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  Document,
  Download,
  FolderOpened,
  Picture,
  Refresh,
  UploadFilled,
  View,
} from '@element-plus/icons-vue'
import * as XLSX from 'xlsx'
import { downloadUrl, listFiles, uploadFiles } from '../api'

const props = defineProps({
  threadId: { type: String, default: '' },
  /**
   * 任务是否正在执行。
   *
   * 用来决定要不要轮询目录。为什么非轮询不可：
   *   后端只有 session_created / tool_start / assistant_call / task_result 四种
   *   事件，**没有"文件已落盘"的通知**；而 tool_start 是在工具**开始执行前**发的，
   *   那一刻 .md/.pdf 还没写出来，拿它当刷新信号必然慢一拍。
   *   加上提交任务并不改变 threadId（同会话可多次提交），下面那个 threadId 的
   *   watcher 也不会触发。所以运行期只能主动重读目录。
   */
  running: { type: Boolean, default: false },
})

/** 把当前会话的 md/pdf 绝对路径抛给外层，供结果预览 Tab 使用 */
const emit = defineEmits(['artifacts'])

const activeTab = ref('upload')

// ── Tab1：上传 ────────────────────────────────────────
const pending = ref([]) // { uid, name, size, raw }
const uploading = ref(false)

/** 上传区的表格预览弹窗 */
const sheet = ref({ visible: false, name: '', rows: [], error: '' })
const sheetLoading = ref(false)

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

/** 后缀名（小写，不含点） */
function extOf(name) {
  const m = String(name || '').toLowerCase().match(/\.([a-z0-9]+)$/)
  return m ? m[1] : ''
}

/**
 * 按后缀判断预览方式。
 *
 *   text  —— 拉下来当文本显示（.md 报告就是走这条）
 *   pdf   —— 内嵌 iframe
 *   image —— 直接显示
 *   none  —— 不能预览，只能下载
 */
function kindOf(name) {
  const e = extOf(name)
  if (['md', 'txt', 'json', 'csv', 'log', 'yaml', 'yml', 'sql', 'xml'].includes(e)) return 'text'
  if (e === 'pdf') return 'pdf'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'].includes(e)) return 'image'
  return 'none'
}

/** 后缀 → 图标 */
function iconOf(name) {
  const e = extOf(name)
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'].includes(e)) return Picture
  return Document
}

const isSpreadsheet = (name) => ['xlsx', 'xls', 'csv'].includes(extOf(name))

/** el-upload 的 change：文件先进待传列表，用户确认后再一起发 */
function onChange(file, fileList) {
  pending.value = fileList
}
function onRemove(file, fileList) {
  pending.value = fileList
}
function clearPending() {
  pending.value = []
}

async function doUpload() {
  if (!props.threadId) {
    ElMessage.warning('请先提交一个任务，得到会话标识后再上传')
    return
  }
  const files = pending.value.map((f) => f.raw).filter(Boolean)
  if (!files.length) {
    ElMessage.warning('请先选择要上传的文件')
    return
  }

  uploading.value = true
  try {
    const res = await uploadFiles(props.threadId, files)
    ElMessage.success(`已上传 ${res.files?.length || files.length} 个文件到会话 ${props.threadId}`)
    clearPending()
    await refresh()
  } catch (e) {
    ElMessage.error(`上传失败：${e.message}`)
  } finally {
    uploading.value = false
  }
}

/**
 * 解析待传的表格文件用于预览（本地做，不上传）。
 *
 * 只取前 50 行 × 12 列——这是"简易预览"，整张表塞进 DOM 会让面板卡死。
 */
async function openSheet(file) {
  const name = file.name
  if (!isSpreadsheet(name)) return

  sheet.value = { visible: true, name, rows: [], error: '' }
  sheetLoading.value = true
  try {
    const buf = await file.raw.arrayBuffer()
    const wb = XLSX.read(buf, { type: 'array' })
    const ws = wb.Sheets[wb.SheetNames[0]]
    if (!ws) {
      sheet.value.error = '工作簿里没有可读的工作表'
      return
    }
    const rows = XLSX.utils.sheet_to_json(ws, { header: 1, blankrows: false })
    sheet.value.rows = rows.slice(0, 50).map((r) => r.slice(0, 12))
    if (rows.length > 50) sheet.value.error = `仅预览前 50 行（共 ${rows.length} 行）`
  } catch (e) {
    sheet.value.error = `解析失败：${e.message}`
  } finally {
    sheetLoading.value = false
  }
}

// ── Tab2：会话成果（可浏览）───────────────────────────
const sessionDir = ref(null) // 当前会话目录条目
const otherDirs = ref([]) // 其它会话目录（置灰拦截）
const cwd = ref('') // 正在浏览的绝对路径，始终在会话目录子树内
const crumbs = ref([]) // 面包屑 [{name, path}]
const entries = ref([])
const loading = ref(false)
const error = ref('')

/** 成果预览弹窗 */
const viewer = ref({ visible: false, name: '', kind: 'none', path: '', text: '', error: '' })
const viewerLoading = ref(false)
/** PDF 预览用的 blob URL（见 showViewer 的说明：不能直接用下载地址给 iframe） */
const viewerBlobUrl = ref('')

/** 目录在前、文件在后；同组按名称排序，便于在几十个文件里找 */
const sortedEntries = computed(() => {
  const arr = [...entries.value]
  arr.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
    return a.name.localeCompare(b.name, 'zh-Hans-CN')
  })
  return arr
})

/**
 * 请求序号。只有**最新一次**发起的请求才允许把结果写回界面。
 *
 * 为什么不用一个简单的"进行中"布尔闸：轮询那一拍还没回来时用户切了会话，
 * 布尔闸会把新会话的刷新直接挡掉，新目录要等到下一次轮询才加载（任务没在跑
 * 时甚至永远不加载）。序号闸则是"后来的赢"，旧请求发现序号变了就自己作废，
 * 既不会挡住新请求，也不会把旧会话的目录内容画到新会话上。
 */
let reqSeq = 0
/**
 * 最新一次请求是否还没落定。仅用于轮询跳过堆叠的请求。
 *
 * 名字带 refresh 前缀是必要的：上面 Tab1 已经有一个 `pending`（待上传文件列表），
 * 重名会让整个 SFC 编译不过。
 */
let refreshPending = false

/** 运行期轮询句柄 */
let pollTimer = null

/**
 * 运行期多久重读一次目录。
 *
 * 接口是本地 os.scandir，代价很低；2.5s 是"文件出来后多久能看到"与请求量之间的
 * 折中。任务动辄跑几分钟，这个频率下产物出现基本是即时的观感。
 */
const POLL_INTERVAL = 2500

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => {
    // 上一拍还没回来就跳过这一拍，避免慢响应时请求越堆越多
    if (!refreshPending) refresh({ silent: true })
  }, POLL_INTERVAL)
}

function stopPolling() {
  clearInterval(pollTimer)
  pollTimer = null
}

/**
 * 作废所有在途的 refresh。
 *
 * 用户手动点进子目录时要调一次：接下来列表由 enterDir / jumpTo 自己填，不能让
 * 一个更早发出、刚好此刻返回的轮询结果回头把它覆盖成上一层的目录列表。
 */
function invalidateRefresh() {
  reqSeq += 1
  refreshPending = false
}

/** 空目录时的提示语：跑的过程中和跑完之后，用户该有的预期不一样 */
const emptyText = computed(() =>
  props.running ? '任务正在执行，产物会陆续出现在这里' : '这个目录是空的'
)

/**
 * 拉取目录树与当前会话的产物清单。
 *
 * 每次都会重读会话根目录（而不是只读 cwd）：产物是任务运行过程中陆续落盘的，
 * 结果预览 Tab 的 PDF 门控依赖这份最新的清单。
 *
 * @param {{silent?: boolean}} [opts] silent=true 表示后台轮询触发：不动 loading，
 *        否则"刷新"按钮的转圈会每 2.5 秒闪一次，看着像卡住了。
 */
async function refresh({ silent = false } = {}) {
  // 把发起时的会话标识钉下来：下面有多个 await，期间用户可能已经切走会话。
  // 全程用这个快照，并把 threadId 一起 emit 出去，让外层按它的归属落库——
  // 否则 A 会话的读取结果回来时会被挂到 B 会话上。
  const tid = props.threadId
  const seq = ++reqSeq
  /** 期间又发起了新的刷新（切会话 / 手动点刷新）→ 这批结果已过时，丢弃 */
  const stale = () => seq !== reqSeq

  refreshPending = true
  if (!silent) loading.value = true
  try {
    error.value = ''

    const root = await listFiles('')
    const name = tid ? `session_${tid}` : ''
    const dir = root.find((e) => e.type === 'dir' && e.name === name) || null
    const others = root.filter((e) => e.type === 'dir' && e.name !== name)

    // 中途切走了就丢弃这批结果，别让它污染新会话的界面
    if (stale()) return

    sessionDir.value = dir
    otherDirs.value = others

    if (!dir) {
      entries.value = []
      cwd.value = ''
      crumbs.value = []
      emit('artifacts', { threadId: tid, mdPath: '', pdfPath: '', files: [] })
      return
    }

    // 会话根的文件清单 → 供外层判断有没有 .md / .pdf（PDF Tab 与 H3 都靠它）
    const rootFiles = await listFiles(dir.path)
    if (stale()) return

    const md = rootFiles.find((f) => f.type === 'file' && f.name.toLowerCase().endsWith('.md'))
    const pdf = rootFiles.find((f) => f.type === 'file' && f.name.toLowerCase().endsWith('.pdf'))
    emit('artifacts', {
      threadId: tid,
      mdPath: md?.path || '',
      pdfPath: pdf?.path || '',
      files: rootFiles,
    })

    // 会话切换后原浏览位置多半已失效，回到会话根，避免停在一个不属于当前会话的目录
    if (!cwd.value || !cwd.value.startsWith(dir.path)) {
      cwd.value = dir.path
      crumbs.value = [{ name: dir.name, path: dir.path }]
    }
    const list = await listFiles(cwd.value)
    if (stale()) return
    entries.value = list
  } catch (e) {
    if (!stale()) {
      error.value = `读取目录失败：${e.message}`
      entries.value = []
    }
  } finally {
    // 只有最新一次请求落定时才解开 pending：旧请求的 finally 不能替新请求作罢，
    // 否则轮询会在新请求还在飞的时候又叠一发上去
    if (!stale()) refreshPending = false
    if (!silent && !stale()) loading.value = false
  }
}

/** 进入子目录 */
async function enterDir(entry) {
  invalidateRefresh()
  loading.value = true
  try {
    error.value = ''
    cwd.value = entry.path
    crumbs.value = [...crumbs.value, { name: entry.name, path: entry.path }]
    entries.value = await listFiles(entry.path)
  } catch (e) {
    error.value = `进入目录失败：${e.message}`
  } finally {
    loading.value = false
  }
}

/** 面包屑跳转（截断到第 i 级） */
async function jumpTo(i) {
  const target = crumbs.value[i]
  if (!target || target.path === cwd.value) return
  invalidateRefresh()
  loading.value = true
  try {
    error.value = ''
    cwd.value = target.path
    crumbs.value = crumbs.value.slice(0, i + 1)
    entries.value = await listFiles(target.path)
  } catch (e) {
    error.value = `读取目录失败：${e.message}`
  } finally {
    loading.value = false
  }
}

/**
 * 打开一个条目。
 *
 * 目录 → 进入；能预览的文件 → 弹预览；其余 → 直接触发下载。
 * 双击和"打开"按钮都走这一个入口，行为一致。
 */
function open(entry) {
  if (entry.type === 'dir') {
    enterDir(entry)
    return
  }
  const kind = kindOf(entry.name)
  if (kind === 'none') {
    triggerDownload(entry)
    return
  }
  showViewer(entry, kind)
}

/** 释放 PDF 的 blob URL，避免占着内存不放 */
function releaseViewerBlob() {
  if (viewerBlobUrl.value) {
    URL.revokeObjectURL(viewerBlobUrl.value)
    viewerBlobUrl.value = ''
  }
}

/**
 * 预览文件。
 *
 * 三种处理方式，各有原因：
 *   - 文本：fetch 回来按纯文本渲染（不 innerHTML，避免把文件内容当 HTML 执行）；
 *   - 图片：直接给 <img src=下载地址>。<img> 是**子资源加载**而非导航，
 *     响应头里的 Content-Disposition: attachment 对它无效，所以不必绕；
 *   - PDF：**必须**绕。iframe 加载 URL 是导航行为，后端发的
 *     Content-Disposition: attachment + application/octet-stream 会让浏览器
 *     直接下载而不是渲染。所以读成 ArrayBuffer 重新包成显式标注
 *     application/pdf 的 Blob，用 blob: URL 交给内置阅读器。
 */
async function showViewer(entry, kind) {
  releaseViewerBlob()
  viewer.value = { visible: true, name: entry.name, kind, path: entry.path, text: '', error: '' }
  if (kind === 'image') return

  viewerLoading.value = true
  try {
    const resp = await fetch(downloadUrl(entry.path))
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

    if (kind === 'pdf') {
      const buf = await resp.arrayBuffer()
      viewerBlobUrl.value = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }))
    } else {
      const text = await resp.text()
      // 报告可能很长，截断只为不让弹窗卡顿；文件本身没被改动
      const LIMIT = 200000
      viewer.value.text =
        text.length > LIMIT ? `${text.slice(0, LIMIT)}\n\n…（已截断，仅预览前 ${LIMIT} 字符）` : text
    }
  } catch (e) {
    viewer.value.error = `读取失败：${e.message}`
  } finally {
    viewerLoading.value = false
  }
}

function triggerDownload(entry) {
  const a = document.createElement('a')
  a.href = downloadUrl(entry.path)
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  ElMessage.success(`已开始下载 ${entry.name}`)
}

/**
 * 点击其它会话的目录：不导航，只弹拦截提示。
 *
 * 需求三.2 要求"禁止访问非当前 thread_id 目录文件"。这里是**前端视觉拦截**；
 * 真正的防线在后端——/api/files 与 /api/download 都校验路径落在结果目录内，
 * 且只会暴露结果目录，越界一律 403。前端这层只是提前把话说清楚，不是安全边界。
 */
async function blockOther(dir) {
  await ElMessageBox.alert(
    `该目录属于会话 ${dir.name.replace(/^session_/, '')}，与当前会话 ${
      props.threadId || '（未选择）'
    } 不一致，已拦截访问。\n\n注意：这只是前端的视觉拦截，真正的隔离由后端保证。`,
    '越权访问已拦截',
    { type: 'warning', confirmButtonText: '知道了' }
  )
}

function shortTime(iso) {
  const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/)
  return m ? `${m[2]}-${m[3]} ${m[4]}` : iso || ''
}

/** 关闭预览弹窗就释放 blob，别让几 MB 的 PDF 一直挂在内存里 */
watch(() => viewer.value.visible, (v) => {
  if (!v) releaseViewerBlob()
})

watch(
  () => props.threadId,
  () => {
    clearPending()
    // 会话变了，浏览位置必须重置，否则会停在上一个会话的目录里
    cwd.value = ''
    crumbs.value = []
    viewer.value.visible = false
    refresh()
  }
)

/**
 * 任务开跑就轮询、跑完就停。
 *
 * 停下来时补一次刷新：收尾那个产物可能恰好在两拍之间落盘，最后一拍等不到它。
 */
watch(() => props.running, (v) => {
  if (v) {
    startPolling()
  } else {
    stopPolling()
    refresh({ silent: true })
  }
})

onMounted(() => {
  refresh()
  // 挂载时任务已经在跑（比如刷新页面后正好赶上一个长任务）就直接开轮询。
  // 这里不写成 watch 的 immediate：那样会和上面这行 refresh 同时发两个请求。
  if (props.running) startPolling()
})
onBeforeUnmount(() => {
  stopPolling()
  releaseViewerBlob()
})

defineExpose({ refresh })
</script>

<template>
  <div class="file-panel">
    <el-tabs v-model="activeTab" class="tabs">
      <!-- ══ Tab1 上传文件 ══ -->
      <el-tab-pane label="上传文件" name="upload">
        <div class="pane">
          <p v-if="!threadId" class="warn">请先提交一个任务，得到会话标识后再上传</p>

          <el-upload
            drag
            multiple
            :auto-upload="false"
            :file-list="pending"
            :on-change="onChange"
            :on-remove="onRemove"
            class="dropper"
          >
            <el-icon class="up-icon"><UploadFilled /></el-icon>
            <div class="up-text">把文件拖到这里，或<em>点击选择</em></div>
            <div class="up-hint">将保存到会话 {{ threadId || '（未选择）' }} 的上传目录</div>
          </el-upload>

          <div v-if="pending.length" class="cards">
            <div v-for="f in pending" :key="f.uid" class="file-card ty-card">
              <el-icon class="ficon"><component :is="iconOf(f.name)" /></el-icon>
              <div class="finfo">
                <div class="fname" :title="f.name">{{ f.name }}</div>
                <div class="fmeta">{{ formatSize(f.size) }}</div>
              </div>
              <el-button v-if="isSpreadsheet(f.name)" link size="small" @click="openSheet(f)">
                预览
              </el-button>
              <el-button
                link
                size="small"
                type="danger"
                :icon="Delete"
                @click="onRemove(f, pending.filter((x) => x.uid !== f.uid))"
              />
            </div>
          </div>

          <div class="ops">
            <el-button
              type="primary"
              size="small"
              :loading="uploading"
              :disabled="!pending.length || !threadId"
              @click="doUpload"
            >
              上传 {{ pending.length || '' }} 个文件
            </el-button>
            <el-button size="small" :disabled="!pending.length" @click="clearPending">清空</el-button>
          </div>

          <el-empty v-if="!pending.length" description="还没有待上传的文件" :image-size="60" />
        </div>
      </el-tab-pane>

      <!-- ══ Tab2 会话成果 ══ -->
      <el-tab-pane label="会话成果" name="artifacts">
        <div class="pane">
          <!-- 面包屑：只在当前会话目录子树内导航 -->
          <div class="crumbs">
            <template v-for="(c, i) in crumbs" :key="c.path">
              <span v-if="i" class="sep">/</span>
              <el-button
                link
                size="small"
                class="crumb"
                :class="{ last: i === crumbs.length - 1 }"
                @click="jumpTo(i)"
              >
                {{ i === 0 ? '当前会话' : c.name }}
              </el-button>
            </template>
            <span class="spacer"></span>
            <el-button size="small" :icon="Refresh" :loading="loading" @click="refresh()">刷新</el-button>
          </div>

          <p v-if="error" class="warn">{{ error }}</p>

          <!-- 没有产物目录。任务跑起来后后端就会建 output/session_{tid}/，
               正常只在"还没提交过任何任务"的会话里看到这条。 -->
          <el-empty
            v-if="!sessionDir"
            :description="
              running
                ? '任务正在启动，会话目录建好后这里会显示产物'
                : '当前会话还没有产出文件，提交任务后这里会出现报告'
            "
            :image-size="60"
          />

          <template v-else>
            <!-- 目录/文件列表：双击打开，或用右侧按钮 -->
            <div
              v-for="e in sortedEntries"
              :key="e.path"
              class="entry ty-card"
              :class="{ dir: e.type === 'dir' }"
              :title="e.type === 'dir' ? '双击进入' : '双击打开'"
              @dblclick="open(e)"
            >
              <el-icon class="eicon">
                <FolderOpened v-if="e.type === 'dir'" />
                <component v-else :is="iconOf(e.name)" />
              </el-icon>

              <div class="einfo">
                <div class="ename" :title="e.name">{{ e.name }}</div>
                <div class="emeta">
                  {{ e.type === 'dir' ? '目录' : formatSize(e.size) }} · {{ shortTime(e.mtime) }}
                </div>
              </div>

              <el-button
                v-if="e.type === 'file' && kindOf(e.name) !== 'none'"
                link
                size="small"
                :icon="View"
                @click.stop="open(e)"
              >
                预览
              </el-button>
              <el-button
                v-if="e.type === 'file'"
                link
                size="small"
                :icon="Download"
                @click.stop="triggerDownload(e)"
              >
                下载
              </el-button>
              <el-button v-if="e.type === 'dir'" link size="small" @click.stop="enterDir(e)">
                进入
              </el-button>
            </div>

            <el-empty
              v-if="!sortedEntries.length && !loading"
              :description="emptyText"
              :image-size="60"
            />

            <p class="note">双击条目即可打开；.md / .txt 等文本与 PDF 会在此面板内预览。</p>
          </template>

          <!-- 其它会话：置灰 + 点击拦截 -->
          <template v-if="otherDirs.length">
            <div class="group-head dim">
              <el-icon><FolderOpened /></el-icon>
              <span>其它会话（不可访问）</span>
            </div>
            <div v-for="d in otherDirs" :key="d.path" class="blocked ty-card" @click="blockOther(d)">
              <el-icon><FolderOpened /></el-icon>
              <span class="bname">{{ d.name }}</span>
              <el-tag size="small" type="info" effect="plain">已拦截</el-tag>
            </div>
            <p class="note">
              非当前会话目录在后端同样不可访问：/api/files 与 /api/download 都强制校验路径
              落在结果目录内，越界返回 403。此处置灰是前端提前拦截，不是唯一防线。
            </p>
          </template>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>

  <!-- ── 上传区的表格预览 ── -->
  <el-dialog v-model="sheet.visible" :title="`预览：${sheet.name}`" width="720px">
    <div v-loading="sheetLoading" class="preview-body">
      <p v-if="sheet.error" class="warn">{{ sheet.error }}</p>
      <table v-if="sheet.rows.length" class="preview-table">
        <tbody>
          <tr v-for="(row, ri) in sheet.rows" :key="ri">
            <td v-for="(cell, ci) in row" :key="ci" :class="{ head: ri === 0 }">{{ cell }}</td>
          </tr>
        </tbody>
      </table>
      <el-empty
        v-else-if="!sheetLoading && !sheet.error"
        description="没有可显示的数据"
        :image-size="60"
      />
    </div>
  </el-dialog>

  <!-- ── 成果预览 ── -->
  <el-dialog
    v-model="viewer.visible"
    :title="viewer.name"
    :width="viewer.kind === 'pdf' ? '900px' : '760px'"
    class="viewer-dialog"
    top="6vh"
  >
    <div v-loading="viewerLoading" class="viewer-body" :class="viewer.kind">
      <p v-if="viewer.error" class="warn">{{ viewer.error }}</p>

      <!-- 文本：转义后按纯文本渲染，避免把文件内容当 HTML 执行 -->
      <pre v-else-if="viewer.kind === 'text'" class="text-view">{{ viewer.text }}</pre>

      <!-- PDF：指向 blob URL（不是下载地址，原因见 showViewer） -->
      <iframe v-else-if="viewer.kind === 'pdf' && viewerBlobUrl" class="pdf-view" :src="viewerBlobUrl" />

      <!-- 图片 -->
      <img v-else-if="viewer.kind === 'image'" class="img-view" :src="downloadUrl(viewer.path)" :alt="viewer.name" />
    </div>

    <template #footer>
      <el-button size="small" :icon="Download" @click="triggerDownload({ name: viewer.name, path: viewer.path })">
        下载
      </el-button>
      <el-button size="small" @click="viewer.visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.file-panel { height: 100%; display: flex; flex-direction: column; }
.tabs { flex: 1; min-height: 0; display: flex; flex-direction: column; }
:deep(.el-tabs__header) { margin: 0 0 6px; padding: 7px 13px 0; }
:deep(.el-tabs__content) { flex: 1; min-height: 0; overflow: hidden; }
:deep(.el-tab-pane) { height: 100%; }

/* 与结果预览区一致的 Tab 样式：主色 + 渐变下划线 */
:deep(.el-tabs__nav-wrap::after) { height: 1px; background: var(--ty-border); }
:deep(.el-tabs__item) {
  font-weight: 600;
  font-size: 13px;
  color: var(--ty-text-soft);
  transition: color 0.18s;
}
:deep(.el-tabs__item:hover),
:deep(.el-tabs__item.is-active) { color: var(--ty-primary); }
:deep(.el-tabs__active-bar) {
  height: 2px;
  border-radius: 2px;
  background-image: var(--ty-grad-primary);
  box-shadow: 0 0 8px var(--ty-glow);
}

.pane { height: 100%; overflow: auto; padding: 6px 13px 18px; }

.warn {
  margin: 6px 0;
  padding: 7px 9px;
  font-size: 12px;
  border-radius: var(--ty-radius-sm);
  border: 1px solid color-mix(in srgb, var(--ty-warning) 35%, transparent);
  background: color-mix(in srgb, var(--ty-warning) 12%, transparent);
  color: var(--ty-warning);
}

/* ── 上传区 ── */
/* 拖拽区：虚线常态，悬停时描边点亮 + 淡淡的渐显光晕，给出"可以放进来"的反馈 */
.dropper :deep(.el-upload-dragger) {
  padding: 18px 10px;
  border-radius: var(--ty-radius);
  border: 1px dashed var(--ty-border-strong);
  background: var(--ty-surface-soft);
  transition: border-color 0.2s, background 0.2s, box-shadow 0.2s;
}
.dropper :deep(.el-upload-dragger:hover),
.dropper :deep(.el-upload-dragger.is-dragover) {
  border-color: var(--ty-primary);
  background: var(--ty-primary-weak);
  box-shadow: 0 0 0 3px var(--ty-primary-ring);
}
.up-icon { font-size: 32px; color: var(--ty-text-faint); transition: color 0.2s; }
.dropper :deep(.el-upload-dragger:hover) .up-icon { color: var(--ty-primary); }
.up-text { font-size: 12px; color: var(--ty-text-soft); margin-top: 7px; }
.up-text em { color: var(--ty-primary); font-style: normal; font-weight: 600; }
.up-hint { font-size: 10px; color: var(--ty-text-faint); margin-top: 5px; }

.cards { margin-top: 11px; display: flex; flex-direction: column; gap: 6px; }
/* 卡片：默认按钮的同款玻璃质感，左侧一条主色条 */
.file-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px 7px 12px;
  overflow: hidden;
  border-radius: var(--ty-radius-sm);
}
.file-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background-image: var(--ty-grad-primary);
}
/* 行内嵌套卡片不再叠一层模糊，也去掉 ty-card 的顶部发丝线——列表里会显得很碎 */
.entry::after,
.file-card::after,
.blocked::after { display: none; }
.entry,
.file-card,
.blocked { backdrop-filter: none; -webkit-backdrop-filter: none; }

.ficon { color: var(--ty-primary); }
.finfo { flex: 1; min-width: 0; }
.fname { font-size: 12px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fmeta { font-size: 10px; color: var(--ty-text-faint); }

.ops { display: flex; gap: 6px; margin: 11px 0; }

/* ── 成果区 ── */
.crumbs {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
  margin-bottom: 9px;
  padding-bottom: 7px;
  border-bottom: 1px dashed var(--ty-border);
}
.crumb { font-size: 11px; padding: 0 3px; }
.crumb.last { color: var(--ty-text-soft); font-weight: 700; }
.sep { font-size: 11px; color: var(--ty-text-faint); }
.crumbs .spacer { flex: 1; }

/* 条目：目录/文件统一行样式，悬停时描边点亮 + 左移一小格 */
.entry {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 9px;
  margin-bottom: 6px;
  cursor: pointer;
  user-select: none;
  border-radius: var(--ty-radius-sm);
  transition: box-shadow 0.18s, border-color 0.18s, transform 0.18s;
}
.entry:hover {
  box-shadow: var(--ty-shadow-2);
  border-color: color-mix(in srgb, var(--ty-primary) 45%, var(--ty-border));
  transform: translateY(-1px);
}
.eicon { color: var(--ty-primary); flex: 0 0 auto; }
.entry.dir .eicon { color: var(--ty-src-db); }
.einfo { flex: 1; min-width: 0; }
.ename {
  font-size: 12px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.emeta { font-size: 10px; color: var(--ty-text-faint); margin-top: 1px; }

.group-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 14px 0 7px;
  font-size: 12px;
  font-weight: 700;
  color: var(--ty-text-faint);
}

.blocked {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 9px;
  margin-bottom: 6px;
  cursor: not-allowed;
  border-radius: var(--ty-radius-sm);
  opacity: 0.55;
  filter: grayscale(1);
  transition: opacity 0.18s;
}
.blocked:hover { opacity: 0.8; }
.bname {
  flex: 1;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ty-text-faint);
}

.note {
  margin: 11px 0 0;
  padding: 8px 10px;
  font-size: 10px;
  line-height: 1.65;
  color: var(--ty-text-faint);
  background: var(--ty-surface-soft);
  border: 1px dashed var(--ty-border);
  border-radius: var(--ty-radius-sm);
}

/* ── 表格预览 ── */
.preview-body { max-height: 420px; overflow: auto; }
.preview-table { border-collapse: separate; border-spacing: 0; font-size: 12px; width: 100%; }
.preview-table td {
  border-bottom: 1px solid var(--ty-border);
  padding: 4px 8px;
  white-space: nowrap;
}
.preview-table td.head {
  background: var(--ty-surface-soft);
  font-weight: 700;
  position: sticky;
  top: 0;
}

/* ── 成果预览 ── */
.viewer-body { min-height: 200px; }
.viewer-body.text { max-height: 66vh; overflow: auto; }
.text-view {
  margin: 0;
  padding: 12px 13px;
  font-family: 'JetBrains Mono', Consolas, Monaco, 'Microsoft YaHei', monospace;
  font-size: 12.5px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ty-text);
  background: var(--ty-surface-soft);
  border: 1px solid var(--ty-border);
  border-radius: var(--ty-radius-sm);
}
.pdf-view {
  width: 100%;
  height: 68vh;
  border: 1px solid var(--ty-border);
  border-radius: var(--ty-radius-sm);
  background: var(--ty-surface-sunken);
}
.img-view {
  max-width: 100%;
  display: block;
  margin: 0 auto;
  border-radius: var(--ty-radius-sm);
  box-shadow: var(--ty-shadow-2);
}
</style>
