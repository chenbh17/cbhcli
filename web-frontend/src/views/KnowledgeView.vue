<script setup>
import { ref, onMounted } from 'vue'
import api from '../api.js'

const agents = ref([])
const selectedAgent = ref('')
const files = ref([])
const loading = ref(false)
const showAdd = ref(false)
const selectedFile = ref(null)
const uploading = ref(false)

onMounted(async () => {
  try {
    const data = await api.getAgents()
    agents.value = data.agents || []
    selectedAgent.value = data.active_agent || (agents.value[0]?.name || '')
    if (selectedAgent.value) await loadFiles()
  } catch (e) { console.error(e) }
})

async function loadFiles() {
  if (!selectedAgent.value) return
  loading.value = true
  try {
    const data = await api.getKnowledge(selectedAgent.value)
    files.value = data.files || []
  } catch (e) { console.error(e) }
  loading.value = false
}

async function onAgentChange() {
  await loadFiles()
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function onFileSelect(e) {
  selectedFile.value = e.target.files[0] || null
}

async function addFile() {
  if (!selectedFile.value) { alert('请先选择文件'); return }
  uploading.value = true
  try {
    await api.uploadKnowledgeFile(selectedAgent.value, selectedFile.value)
    showAdd.value = false
    selectedFile.value = null
    await loadFiles()
  } catch (e) { alert(e.message) }
  uploading.value = false
}

async function removeFile(name) {
  if (!confirm(`Remove "${name}" from knowledge base?`)) return
  try {
    await api.removeKnowledgeFile(selectedAgent.value, name)
    await loadFiles()
  } catch (e) { alert(e.message) }
}

async function reindex() {
  try {
    const result = await api.reindexKnowledge(selectedAgent.value)
    alert(result.message || 'Reindex completed')
  } catch (e) { alert(e.message) }
}
</script>

<template>
  <div class="main-header">
    <h2>Knowledge Base</h2>
    <select v-model="selectedAgent" @change="onAgentChange" style="min-width: 130px;">
      <option value="" disabled>Select Agent</option>
      <option v-for="a in agents" :key="a.name" :value="a.name">{{ a.name }}</option>
    </select>
    <button class="btn btn-primary btn-sm" @click="showAdd = true">+ Add File</button>
    <button class="btn btn-secondary btn-sm" @click="reindex" :disabled="!selectedAgent || files.length === 0">Reindex</button>
  </div>
  <div class="main-content">
    <div v-if="!selectedAgent" class="empty-state">
      <div class="icon">...</div>
      <div>Select an Agent to manage knowledge base</div>
    </div>

    <div v-else-if="files.length === 0 && !loading" class="empty-state">
      <div class="icon">...</div>
      <div>Knowledge base is empty</div>
      <div class="text-sm text-dim mt-2">Add files to enhance the agent's knowledge</div>
    </div>

    <div class="card" v-for="f in files" :key="f.name">
      <div class="card-header">
        <div>
          <h3>{{ f.name }}</h3>
          <div class="text-sm text-dim">{{ formatSize(f.size) }} | {{ f.path }}</div>
        </div>
        <div>
          <button class="btn btn-danger btn-sm" @click="removeFile(f.name)">Remove</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Add File Modal -->
  <div v-if="showAdd" class="modal-overlay" @click.self="showAdd = false">
    <div class="modal">
      <h3>Add File to Knowledge Base</h3>
      <div class="form-group">
        <label>选择文件</label>
        <input type="file" @change="onFileSelect" accept=".md,.txt,.py,.js,.json,.yaml,.yml,.ts,.tsx,.jsx,.html,.css,.csv,.xml,.rst,.log,.cfg,.ini,.toml" />
      </div>
      <div v-if="selectedFile" class="text-sm" style="margin-bottom:8px;">
        已选择: {{ selectedFile.name }} ({{ formatSize(selectedFile.size) }})
      </div>
      <div class="text-sm text-dim">支持: .md, .txt, .py, .js, .json, .yaml, .yml 等文本文件</div>
      <div class="modal-actions">
        <button class="btn btn-secondary" @click="showAdd = false">Cancel</button>
        <button class="btn btn-primary" @click="addFile" :disabled="!selectedFile || uploading">{{ uploading ? 'Uploading...' : 'Add' }}</button>
      </div>
    </div>
  </div>
</template>
