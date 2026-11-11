<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const runs = ref([])
const runId = ref(null)
const rows = ref([])
const err = ref('')

const counts = computed(() => {
  const out = {}
  for (const r of rows.value) out[r.disposition] = (out[r.disposition] || 0) + 1
  return out
})
const total = computed(() => rows.value.length)

async function load() {
  err.value = ''
  try {
    const query = runId.value != null ? `?run_id=${runId.value}` : ''
    rows.value = await getJSON(`/api/staging${query}`)
  } catch (e) {
    err.value = String(e.message || e)
  }
}

onMounted(async () => {
  try {
    runs.value = await getJSON('/api/runs')
    const q = Number(route.query.run_id)
    if (q && runs.value.some((r) => r.id === q)) {
      runId.value = q
    } else if (runs.value.length) {
      runId.value = runs.value[0].id
    }
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(runId, load)
</script>
<template>
  <div class="page">
    <h1>Staging</h1>
    <p class="muted">每个 Run 的解析行与 disposition；统计应与运行台报告同源一致。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <label>
        Run
        <select v-model.number="runId">
          <option :value="null">全部（最近 200）</option>
          <option v-for="r in runs" :key="r.id" :value="r.id">
            #{{ r.id }} · {{ r.source_name }} · {{ r.mode }} · {{ r.status }}
          </option>
        </select>
      </label>
      <span class="muted" style="margin-left:0.75rem">共 {{ total }} 行</span>
    </div>
    <div class="panel" v-if="total">
      <h3>Disposition 统计</h3>
      <p>
        <span v-for="(n, d) in counts" :key="d" class="tag info" style="margin-right:0.4rem">
          {{ d }} {{ n }}
        </span>
      </p>
      <p class="muted">
        upserted = insert + upsert + lookback_* ／ conflict = quarantine ／ skipped = skip_stale + skipped_watermark
      </p>
    </div>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>ID</th><th>run</th><th>source</th><th>key</th><th>event_ts</th><th>rev</th><th>disposition</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.run_id }}</td>
            <td>{{ r.source_id }}</td>
            <td class="mono">{{ r.business_key }}</td>
            <td class="mono">{{ r.event_ts }}</td>
            <td>{{ r.source_revision }}</td>
            <td>
              <span
                class="tag"
                :class="r.disposition === 'quarantine' ? 'danger' : (r.disposition.startsWith('skip') || r.disposition.includes('skip') ? 'warn' : (r.disposition.startsWith('lookback') ? 'info' : 'ok'))"
              >{{ r.disposition }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
