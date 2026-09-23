<script setup>
/**
 * 任务提交框 — TDD §8.3
 *
 * 只负责收集输入并向上抛 submit 事件，不碰网络——
 * 提交时机要配合 WebSocket 连接，那个协调逻辑在 App.vue 里。
 */
import { ref } from 'vue'

const props = defineProps({
  // 任务执行期间禁用，避免用户连点提交出多个会话
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['submit'])

const query = ref('')

// 给几个能覆盖三路检索的示例，方便一试就跑出层级事件流
const EXAMPLES = [
  '查询药品库存情况并生成分析报告',
  '检索阿莫西林的适应症与禁忌，整理成报告',
  '结合内部知识库和数据库，分析近三个月的处方合规情况',
]

function onExample(text) {
  query.value = text
}

function onSubmit() {
  const text = query.value.trim()
  if (!text || props.disabled) return
  emit('submit', text)
}
</script>

<template>
  <div class="task-input">
    <textarea
      v-model="query"
      :disabled="disabled"
      placeholder="描述你的数据分析任务，例如：查询药品库存并生成分析报告"
      rows="3"
      @keydown.ctrl.enter="onSubmit"
    ></textarea>

    <div class="row">
      <div class="examples">
        <span class="label">示例：</span>
        <button
          v-for="(ex, i) in EXAMPLES"
          :key="i"
          class="example"
          :disabled="disabled"
          :title="ex"
          @click="onExample(ex)"
        >
          {{ ex.slice(0, 12) }}…
        </button>
      </div>
      <button class="submit" :disabled="disabled || !query.trim()" @click="onSubmit">
        {{ disabled ? '执行中…' : '提交任务' }}
      </button>
    </div>
    <p class="hint">Ctrl + Enter 快速提交</p>
  </div>
</template>

<style scoped>
.task-input { padding: 16px 20px; background: #fff; border-bottom: 1px solid #e5e7eb; }

textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
  font-family: inherit;
  resize: vertical;
  outline: none;
}
textarea:focus { border-color: #3b82f6; }
textarea:disabled { background: #f9fafb; color: #9ca3af; }

.row { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; gap: 12px; }
.examples { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; min-width: 0; }
.label { font-size: 12px; color: #6b7280; }

.example {
  font-size: 12px;
  padding: 3px 8px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f9fafb;
  color: #4b5563;
  cursor: pointer;
}
.example:hover:not(:disabled) { background: #eef2ff; border-color: #c7d2fe; }
.example:disabled { opacity: 0.5; cursor: not-allowed; }

.submit {
  padding: 8px 22px;
  border: none;
  border-radius: 6px;
  background: #2563eb;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
}
.submit:hover:not(:disabled) { background: #1d4ed8; }
.submit:disabled { background: #9ca3af; cursor: not-allowed; }

.hint { margin: 8px 0 0; font-size: 12px; color: #9ca3af; }
</style>
