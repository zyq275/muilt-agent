<script setup>
/**
 * 文件面板 — TDD §8.3
 *
 * 三件事：上传（落到当前会话目录）、浏览结果目录、下载产物。
 *
 * 路径约定（后端 api/server.py 的强约束）：/api/files 与 /api/download 收的
 * 是**绝对路径**，且必须落在后端的结果目录内，否则 403。所以这里所有导航都
 * 直接回传后端返回的 entry.path，绝不用前端字符串自己拼路径——拼出来的既可能
 * 越界被拒，也可能因斜杠/盘符差异对不上。
 */
import { onMounted, ref, watch } from 'vue'
import { downloadUrl, listFiles, uploadFiles } from '../api'

const props = defineProps({
  threadId: { type: String, default: '' },
})

// 当前浏览的绝对路径；空串表示结果目录根（后端约定：path 为空即根）
const currentPath = ref('')
const entries = ref([])
const error = ref('')
const uploading = ref(false)
const loaded = ref(false)
const fileInput = ref(null)

async function load(path = '') {
  try {
    error.value = ''
    entries.value = await listFiles(path)
    currentPath.value = path
    loaded.value = true
  } catch (e) {
    error.value = `读取目录失败：${e.message}`
    entries.value = []
  }
}

/** 上一级目录：从绝对路径尾部去掉一段。根目录（空串）没有上一级。 */
const parentPath = ref('')
function computeParent(p) {
  if (!p) return ''
  const norm = p.replace(/[\\/]+$/, '')
  const idx = Math.max(norm.lastIndexOf('\\'), norm.lastIndexOf('/'))
  // 已到盘符层（如 C:\）就停在原地，避免把越界路径发给后端换回 403
  if (idx <= 0) return ''
  const parent = norm.slice(0, idx)
  return /^[A-Za-z]:$/.test(parent) ? '' : parent
}

function goUp() {
  load(parentPath.value)
}

function goRoot() {
  load('')
}

const uploadedNames = ref([])

async function onUpload(event) {
  const files = Array.from(event.target.files || [])
  if (!files.length) return
  if (!props.threadId) {
    error.value = '请先提交一个任务，得到会话标识后再上传'
    return
  }

  uploading.value = true
  error.value = ''
  try {
    const res = await uploadFiles(props.threadId, files)
    uploadedNames.value = res.files || []
    await load(currentPath.value)
  } catch (e) {
    error.value = `上传失败：${e.message}`
  } finally {
    uploading.value = false
    // 清空 input，否则连续选同一个文件不会触发 change
    if (fileInput.value) fileInput.value.value = ''
  }
}

function formatSize(bytes) {
  if (!bytes) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

function shortTime(iso) {
  const m = String(iso || '').match(/T(\d{2}:\d{2})/)
  return m ? m[1] : iso || ''
}

// 会话变了（提交新任务）就回到根目录重新列一遍——
// 新会话的产物在 output/session_{新id}/ 下，停在旧目录会看不到东西
watch(() => props.threadId, () => load(''))

onMounted(() => load(''))

watch(currentPath, (p) => { parentPath.value = computeParent(p) })
</script>

<template>
  <div class="file-panel">
    <div class="section">
      <h3>上传文件</h3>
      <input ref="fileInput" type="file" multiple :disabled="uploading" @change="onUpload" />
      <p class="tip">
        <template v-if="threadId">将保存到会话 <code>{{ threadId }}</code> 的上传目录</template>
        <template v-else>提交任务后可上传文件</template>
      </p>
      <ul v-if="uploadedNames.length" class="uploaded">
        <li v-for="n in uploadedNames" :key="n">已上传：{{ n }}</li>
      </ul>
    </div>

    <div class="section grow">
      <div class="head">
        <h3>结果文件</h3>
        <div class="actions">
          <button :disabled="!currentPath" @click="goUp">上一级</button>
          <button :disabled="!currentPath" @click="goRoot">根目录</button>
          <button @click="load(currentPath)">刷新</button>
        </div>
      </div>

      <p v-if="error" class="error">{{ error }}</p>

      <p v-if="loaded && !entries.length && !error" class="tip">目录为空</p>

      <ul v-else class="list">
        <li v-for="e in entries" :key="e.path" :class="e.type">
          <template v-if="e.type === 'dir'">
            <button class="name" @click="load(e.path)">📁 {{ e.name }}</button>
          </template>
          <template v-else>
            <a class="name" :href="downloadUrl(e.path)" :download="e.name">📄 {{ e.name }}</a>
          </template>
          <span class="meta">{{ formatSize(e.size) }} · {{ shortTime(e.mtime) }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.file-panel { display: flex; flex-direction: column; height: 100%; }
.section { padding: 14px 16px; border-bottom: 1px solid #e5e7eb; }
.grow { flex: 1; display: flex; flex-direction: column; min-height: 0; border-bottom: none; }

h3 { margin: 0 0 8px; font-size: 13px; color: #374151; }

input[type='file'] { width: 100%; font-size: 12px; }

.tip { margin: 8px 0 0; font-size: 12px; color: #9ca3af; }
.tip code { background: #f3f4f6; padding: 1px 4px; border-radius: 3px; }

.error { margin: 8px 0; font-size: 12px; color: #b91c1c; background: #fef2f2; padding: 6px 8px; border-radius: 4px; }

.uploaded { margin: 8px 0 0; padding-left: 16px; font-size: 12px; color: #166534; }

.head { display: flex; align-items: center; justify-content: space-between; }
.actions { display: flex; gap: 4px; }
.actions button {
  font-size: 11px;
  padding: 2px 7px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  background: #fff;
  color: #4b5563;
  cursor: pointer;
}
.actions button:hover:not(:disabled) { background: #f3f4f6; }
.actions button:disabled { opacity: 0.4; cursor: not-allowed; }

.list { list-style: none; margin: 8px 0 0; padding: 0; overflow: auto; flex: 1; }
.list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 6px;
  border-radius: 4px;
  font-size: 12px;
}
.list li:hover { background: #f9fafb; }

.name {
  flex: 1;
  text-align: left;
  background: none;
  border: none;
  padding: 0;
  color: #2563eb;
  cursor: pointer;
  font-size: 12px;
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.name:hover { text-decoration: underline; }

.meta { color: #9ca3af; font-size: 11px; white-space: nowrap; }
</style>
