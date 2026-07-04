<script setup>
import { ref, onMounted } from 'vue'
import api from '../api.js'

const agents = ref([])
const selectedAgent = ref('')
const servers = ref([])
const loading = ref(false)
const showAdd = ref(false)
const addForm = ref({ name: '', url: '', headers: '' })

onMounted(async () => {
  try {
    const data = await api.getAgents()
    agents.value = data.agents || []
    selectedAgent.value = data.active_agent || (agents.value[0]?.name || '')
    if (selectedAgent.value) await loadServers()
  } catch (e) { console.error(e) }
})

async function loadServers() {
  if (!selectedAgent.value) return
  loading.value = true
  try {
    const data = await api.getMCPServers(selectedAgent.value)
    servers.value = data.servers || []
  } catch (e) { console.error(e) }
  loading.value = false
}

async function onAgentChange() {
  await loadServers()
}

function openAdd() {
  addForm.value = { name: '', url: '', headers: '' }
  showAdd.value = true
}

async function addServer() {
  if (!addForm.value.name || !addForm.value.url) { alert('Name and URL are required'); return }
  let headers = null
  if (addForm.value.headers.trim()) {
    try {
      headers = JSON.parse(addForm.value.headers)
    } catch (e) { alert('Headers must be valid JSON'); return }
  }
  try {
    await api.addMCPServer(selectedAgent.value, {
      name: addForm.value.name,
      url: addForm.value.url,
      headers: headers,
    })
    showAdd.value = false
    await loadServers()
  } catch (e) { alert(e.message) }
}

async function removeServer(name) {
  if (!confirm(`Remove MCP server "${name}"?`)) return
  try {
    await api.removeMCPServer(selectedAgent.value, name)
    await loadServers()
  } catch (e) { alert(e.message) }
}

async function refreshServer(name) {
  try {
    const result = await api.refreshMCPServer(selectedAgent.value, name)
    alert(result.message)
  } catch (e) { alert(e.message) }
}
</script>

<template>
  <div class="main-header">
    <h2>MCP Servers</h2>
    <select v-model="selectedAgent" @change="onAgentChange" style="min-width: 130px;">
      <option value="" disabled>Select Agent</option>
      <option v-for="a in agents" :key="a.name" :value="a.name">{{ a.name }}</option>
    </select>
    <button class="btn btn-primary btn-sm" @click="openAdd">+ Add Server</button>
  </div>
  <div class="main-content">
    <div v-if="!selectedAgent" class="empty-state">
      <div class="icon">...</div>
      <div>Select an Agent to manage MCP servers</div>
    </div>

    <div v-else-if="servers.length === 0 && !loading" class="empty-state">
      <div class="icon">...</div>
      <div>No MCP servers configured</div>
      <div class="text-sm text-dim mt-2">Click "+ Add Server" to connect an MCP server</div>
    </div>

    <div class="card" v-for="s in servers" :key="s.name">
      <div class="card-header">
        <div>
          <h3>{{ s.name }}</h3>
          <div class="text-sm text-dim">{{ s.url }}</div>
        </div>
        <div class="flex items-center" style="gap: 8px;">
          <button class="btn btn-secondary btn-sm" @click="refreshServer(s.name)">Refresh</button>
          <button class="btn btn-danger btn-sm" @click="removeServer(s.name)">Remove</button>
        </div>
      </div>
      <div v-if="s.enabled_tools" class="mt-2">
        <div class="text-sm text-dim">Enabled tools: {{ s.enabled_tools.length }}</div>
        <div class="text-sm" style="margin-left: 12px;" v-for="t in s.enabled_tools" :key="t">- {{ t }}</div>
      </div>
      <div v-else class="text-sm text-dim mt-2">All tools enabled</div>
      <div v-if="s.headers && Object.keys(s.headers).length" class="text-sm text-dim mt-2">
        Custom headers: {{ Object.keys(s.headers).join(', ') }}
      </div>
    </div>
  </div>

  <!-- Add Server Modal -->
  <div v-if="showAdd" class="modal-overlay" @click.self="showAdd = false">
    <div class="modal">
      <h3>Add MCP Server</h3>
      <div class="form-group">
        <label>Server Name</label>
        <input v-model="addForm.name" placeholder="my-mcp-server" />
      </div>
      <div class="form-group">
        <label>Server URL</label>
        <input v-model="addForm.url" placeholder="http://localhost:3000/sse" />
      </div>
      <div class="form-group">
        <label>Custom Headers (JSON, optional)</label>
        <textarea v-model="addForm.headers" rows="3" placeholder='{"Authorization": "Bearer xxx"}'></textarea>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" @click="showAdd = false">Cancel</button>
        <button class="btn btn-primary" @click="addServer">Add</button>
      </div>
    </div>
  </div>
</template>
