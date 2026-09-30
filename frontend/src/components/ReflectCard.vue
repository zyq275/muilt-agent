<script setup>
/**
 * 反思校验卡片（H1–H4）
 *
 * 【数据来源必须说清】后端每轮都跑 hard_check()，但**结果只写 logger，不发事件**，
 * 所以这里的四项指标不是后端推来的，是前端用同一套算法（src/reflect.js）从最终
 * 报告正文**事后复算**的。两者口径可能不一致，卡片刻意标注了这一点。
 *
 * 【轮次也拿不到】后端不推送轮次边界，"最多 3 轮纠错"的过程无法还原。这里退而
 * 求其次，给出一个**可观测的代理量**：报告生成次数（每轮都要重新产出报告）。
 * 它不等于轮次，所以标注为"约"。
 */
import { computed } from 'vue'
import { InfoFilled } from '@element-plus/icons-vue'
import { MIN_WORD_COUNT } from '../reflect'

const props = defineProps({
  checks: { type: Array, default: () => [] },
  wordCount: { type: Number, default: 0 },
  /** generate_markdown 的调用次数，作为反思轮次的粗略代理 */
  reportGenerations: { type: Number, default: 0 },
  /** 是否已经有报告正文可校验 */
  hasReport: { type: Boolean, default: false },
})

const failed = computed(() => props.checks.filter((c) => !c.ok))

/**
 * 是否处于"还没报告可校验"的待办态。
 *
 * 关键：任务未开始时不能把 H1/H2 标成橙色"不达标"。没有报告时"字数 0/1000"
 * 和"报告内容为空"是**尚未开始**，不是**校验失败**——橙色是给真正跑过一轮、
 * 结果不合格的情况留的。否则用户一打开页面就看到一片橙色告警。
 */
const pending = computed(() => !props.hasReport)

/** 字数进度：相对 1000 字要求的百分比，封顶 100 */
const wordPercent = computed(() =>
  Math.min(100, Math.round((props.wordCount / MIN_WORD_COUNT) * 100))
)
</script>

<template>
  <div class="reflect ty-card">
    <div class="head">
      <span class="title ty-title">反思校验</span>
      <el-tag v-if="hasReport" :type="failed.length ? 'warning' : 'success'" size="small" effect="light">
        {{ failed.length ? `${failed.length} 项未达标` : '全部达标' }}
      </el-tag>
      <el-tag v-else type="info" size="small" effect="light">待报告</el-tag>

      <span class="spacer"></span>
      <el-tooltip placement="top" :show-after="200">
        <template #content>
          <div class="tipbox">
            后端 hard_check() 的结果只写日志、不推事件，<br />
            因此这四项由前端用同一套算法从最终报告复算，<br />
            属于<b>事后复算</b>而非后端真实判定。校验结果不参与后端纠错循环。
          </div>
        </template>
        <el-icon class="info"><InfoFilled /></el-icon>
      </el-tooltip>
    </div>

    <!-- 四项硬校验 -->
    <div class="checks">
      <div
        v-for="c in checks"
        :key="c.id"
        class="check"
        :class="pending ? 'pending' : c.ok ? 'ok' : 'bad'"
      >
        <span class="id">{{ c.id }}</span>
        <span class="label">{{ c.label }}</span>
        <span class="detail">{{ c.detail }}</span>
      </div>
      <div v-if="!checks.length" class="empty">任务结束后显示校验结果</div>
    </div>

    <!-- 报告字数（需求要求实时可见） -->
    <div class="words">
      <div class="words-head">
        <span>报告字数</span>
        <span class="num" :class="{ bad: hasReport && wordCount < MIN_WORD_COUNT }">
          {{ wordCount }} / {{ MIN_WORD_COUNT }}
        </span>
      </div>
      <!-- 待报告时用默认蓝色而非橙色：0 字是"还没开始"，不是"字数不足" -->
      <el-progress
        :percentage="wordPercent"
        :stroke-width="6"
        :status="!hasReport ? undefined : wordCount >= MIN_WORD_COUNT ? 'success' : 'warning'"
        :show-text="false"
      />
      <div class="rounds">
        报告生成次数约 {{ reportGenerations }} 次
        <span class="dim">（每轮反思都要重新产出报告，可粗略反映轮次；后端不推送轮次事件，无法给出准确轮数）</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reflect {
  padding: 11px 13px;
  margin-bottom: 10px;
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
/* 用全局的渐变竖条标题，和其他面板标题统一 */
.title { font-size: 13px; font-weight: 700; letter-spacing: 0.02em; }
.spacer { flex: 1; }
.info { color: var(--ty-text-faint); cursor: help; transition: color 0.18s; }
.info:hover { color: var(--ty-primary); }
.tipbox { line-height: 1.6; font-size: 12px; }

/* ── 四项校验 ── */
.checks {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 7px;
}
.check {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  overflow: hidden;
  border-radius: var(--ty-radius-sm);
  border: 1px solid var(--ty-border);
  background: var(--ty-surface-soft);
  font-size: 12px;
  transition: transform 0.18s, border-color 0.18s, box-shadow 0.18s;
}
.check:hover { transform: translateY(-1px); box-shadow: var(--ty-shadow-1); }
/* 左侧色条指示三态：绿=达标、橙=不达标、灰=待报告 */
.check::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--ty-text-faint);
  opacity: 0.5;
}
.check.ok::before { background: var(--ty-success); opacity: 1; }
.check.bad::before { background: var(--ty-warning); opacity: 1; }

/* 不达标项橙色高亮（需求二.3） */
.check.bad {
  border-color: color-mix(in srgb, var(--ty-warning) 55%, transparent);
  background: color-mix(in srgb, var(--ty-warning) 12%, transparent);
}
.check .id {
  font-weight: 800;
  font-size: 11px;
  color: var(--ty-text-faint);
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
}
.check.ok .id { color: var(--ty-success); }
.check.bad .id { color: var(--ty-warning); }
.check .label { font-weight: 600; }
.check .detail {
  margin-left: auto;
  color: var(--ty-text-soft);
  font-size: 11px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.check.bad .detail { color: var(--ty-warning); }

/* 待办态：中性灰，不告警。任务没跑过就没有"不达标"可言。 */
.check.pending { opacity: 0.72; }
.check.pending .id,
.check.pending .detail { color: var(--ty-text-faint); }

.empty {
  grid-column: 1 / -1;
  padding: 10px;
  text-align: center;
  font-size: 12px;
  color: var(--ty-text-faint);
}

/* ── 字数 ── */
.words {
  margin-top: 11px;
  padding-top: 10px;
  border-top: 1px dashed var(--ty-border);
}
.words-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 5px;
}
.words-head .num {
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
}
.words-head .num.bad { color: var(--ty-warning); }

.rounds {
  margin-top: 7px;
  font-size: 11px;
  color: var(--ty-text-soft);
}
.rounds .dim { color: var(--ty-text-faint); }
</style>
