<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const runs = ref([])
const runId = ref(null)
const rows = ref([])
const err = ref('')

async function load() {
  err.value = ''
  try {
    const query = runId.value != null ? `?run_id=${runId.value}` : ''
    rows.value = await getJSON(`/api/conflicts${query}`)
  } catch (e) {
    err.value = String(e.message || e)
  }
}

onMounted(async () => {
  try {
    runs.value = await getJSON('/api/runs')
    const q = Number(route.query.run_id)
    if (q && runs.value.some((r) => r.id === q)) runId.value = q
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(runId, load)
</script>
<template>
  <div class="page">
    <h1>冲突队列</h1>
    <p class="muted">同业务键、非键字段（title / price / category）分歧 → quarantine；不得静默覆盖 Canonical，冲突行不会出现在 Canonical 列表。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <label>
        Run
        <select v-model.number="runId">
          <option :value="null">全部 Run</option>
          <option v-for="r in runs" :key="r.id" :value="r.id">
            #{{ r.id }} · {{ r.source_name }} · {{ r.mode }}
          </option>
        </select>
      </label>
      <span class="muted" style="margin-left:0.75rem">
        共 {{ rows.length }} 条<span v-if="runId != null">（本次 Run 新增）</span>
      </span>
    </div>
    <div class="panel" v-for="c in rows" :key="c.id">
      <p>
        #{{ c.id }} · run {{ c.run_id }} ·
        <span class="mono">{{ c.business_key }}</span> ·
        <span class="tag danger">{{ c.reason }}</span>
        <span class="muted"> · {{ c.created_at }}</span>
      </p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem">
        <div>
          <div class="muted">existing (canonical)</div>
          <pre class="mono">{{ JSON.stringify(c.existing, null, 2) }}</pre>
        </div>
        <div>
          <div class="muted">incoming (quarantined)</div>
          <pre class="mono">{{ JSON.stringify(c.incoming, null, 2) }}</pre>
        </div>
      </div>
    </div>
    <p v-if="!rows.length" class="muted">暂无冲突。</p>
  </div>
</template>
<style scoped>
pre {
  background: #0a1422;
  padding: 0.75rem;
  border-radius: 8px;
  overflow: auto;
  max-height: 240px;
}
</style>
