<script setup>
import { ref, onMounted, watch } from 'vue'
import { marked } from 'marked'
import api from '../api.js'

const agents = ref([])
const selectedAgent = ref('')
const sessions = ref([])
const detail = ref(null)
const showDetail = ref(false)

onMounted(async () => {
  try {
    const data = await api.getAgents()
    agents.value = data.agents || []
    selectedAgent.value = data.active_agent || (agents.value[0]?.name || '')
  } catch (e) { console.error(e) }
})

watch(selectedAgent, loadHistory, { immediate: false })
onMounted(() => { if (selectedAgent.value) loadHistory() })

async function loadHistory() {
  if (!selectedAgent.value) return
  try {
    const data = await api.getHistory(selectedAgent.value)
    sessions.value = data.sessions || []
  } catch (e) { console.error(e) }
}

async function openDetail(filename) {
  try {
    const data = await api.getHistoryDetail(selectedAgent.value, filename)
    detail.value = data.messages || []
    showDetail.value = true
  } catch (e) { alert(e.message) }
}

async function remove(filename) {
  if (!confirm('确认删除此会话？')) return
  try { await api.deleteHistory(selectedAgent.value, filename); await loadHistory() } catch (e) { alert(e.message) }
}

function renderMd(text) {
  if (!text) return '<span class="text-dim">(空)</span>'
  return marked.parse(String(text), { breaks: true })
}
</script>

<template>
  <div class="main-header">
    <h2>📝 历史会话</h2>
    <select v-model="selectedAgent" @change="loadHistory" style="min-width: 130px;">
      <option v-for="a in agents" :key="a.name" :value="a.name">{{ a.name }}</option>
    </select>
  </div>
  <div class="main-content">
    <div v-if="sessions.length === 0" class="empty-state">
      <div class="icon">📝</div>
      <div>暂无历史会话</div>
    </div>

    <div class="card" v-for="s in sessions" :key="s.filename">
      <div class="card-header">
        <div>
          <h3>{{ s.title || '无标题' }}</h3>
          <div class="text-sm text-dim">
            {{ (s.created_at || '').slice(0, 16).replace('T', ' ') }} | {{ s.message_count }} 条消息
          </div>
        </div>
        <div class="flex items-center" style="gap: 8px;">
          <button class="btn btn-secondary btn-sm" @click="openDetail(s.filename)">查看</button>
          <button class="btn btn-danger btn-sm" @click="remove(s.filename)">删除</button>
        </div>
      </div>
    </div>
  </div>

  <!-- 会话详情 -->
  <div v-if="showDetail && detail" class="modal-overlay" @click.self="showDetail = false">
    <div class="modal" style="min-width: 700px; max-width: 900px; max-height: 85vh;">
      <h3 class="mb-2">会话详情 ({{ detail.length }} 条消息)</h3>
      <div style="max-height: 65vh; overflow-y: auto;">
        <div v-for="(msg, i) in detail" :key="i" style="margin-bottom: 12px;">
          <div class="text-sm" style="margin-bottom: 4px;">
            <span class="badge" :class="msg.role === 'user' ? 'badge-active' : 'badge-inactive'">{{ msg.role }}</span>
          </div>
          <div v-if="msg.role === 'assistant' || msg.role === 'user'" class="card" style="margin-bottom: 0; padding: 12px;">
            <div v-html="renderMd(msg.content)"></div>
          </div>
          <div v-else-if="msg.role === 'tool'" class="card" style="margin-bottom: 0; padding: 12px; font-size: 12px;">
            <pre style="white-space: pre-wrap; max-height: 100px; overflow: auto;">{{ (msg.content || '').slice(0, 500) }}</pre>
          </div>
          <div v-else class="text-sm text-dim" style="padding: 4px 12px;">{{ (msg.content || '').slice(0, 200) }}</div>
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" @click="showDetail = false">关闭</button>
      </div>
    </div>
  </div>
</template>
