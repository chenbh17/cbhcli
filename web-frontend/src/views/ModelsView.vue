<script setup>
import { ref, onMounted } from 'vue'
import api from '../api.js'

const models = ref([])
const lastSelected = ref('')
const embeddingModel = ref(null)
const rerankModel = ref(null)
const showAdd = ref(false)
const showEdit = ref(false)
const showEmbedding = ref(false)
const showRerank = ref(false)
const form = ref({ name: '', apiKey: '', url: '', model: '', context_limit: 128000, vision: false })
const editName = ref('')
const embForm = ref({ name: '', apiKey: '', url: '', model: '' })
const rerankForm = ref({ name: '', apiKey: '', url: '', model: '', top_n: 5 })

onMounted(load)

async function load() {
  try {
    const data = await api.getModels()
    models.value = data.models || []
    lastSelected.value = data.last_selected || ''
    embeddingModel.value = data.embedding_model
    rerankModel.value = data.rerank_model
  } catch (e) { console.error(e) }
}

function openAdd() {
  form.value = { name: '', apiKey: '', url: '', model: '', context_limit: 128000, vision: false }
  showAdd.value = true
}

function openEdit(m) {
  editName.value = m.name
  form.value = { ...m }
  showEdit.value = true
}

function openEmbedding() {
  if (embeddingModel.value) {
    embForm.value = { ...embeddingModel.value }
  } else {
    embForm.value = { name: '', apiKey: '', url: '', model: '' }
  }
  showEmbedding.value = true
}

function openRerank() {
  if (rerankModel.value) {
    rerankForm.value = { ...rerankModel.value, top_n: rerankModel.value.top_n || 5 }
  } else {
    rerankForm.value = { name: '', apiKey: '', url: '', model: '', top_n: 5 }
  }
  showRerank.value = true
}

async function addModel() {
  if (!form.value.name || !form.value.url || !form.value.model) { alert('Please fill required fields'); return }
  try { await api.addModel(form.value); showAdd.value = false; await load() } catch (e) { alert(e.message) }
}

async function updateModel() {
  try { await api.updateModel(editName.value, form.value); showEdit.value = false; await load() } catch (e) { alert(e.message) }
}

async function remove(name) {
  if (!confirm(`Delete model "${name}"?`)) return
  try { await api.deleteModel(name); await load() } catch (e) { alert(e.message) }
}

async function select(name) {
  try { await api.selectModel(name); lastSelected.value = name } catch (e) { alert(e.message) }
}

async function saveEmbedding() {
  if (!embForm.value.url || !embForm.value.model) { alert('Please fill URL and Model'); return }
  try { await api.updateEmbeddingModel(embForm.value); showEmbedding.value = false; await load() } catch (e) { alert(e.message) }
}

async function deleteEmbedding() {
  if (!confirm('Delete embedding model config?')) return
  try { await api.deleteEmbeddingModel(); showEmbedding.value = false; await load() } catch (e) { alert(e.message) }
}

async function saveRerank() {
  if (!rerankForm.value.url || !rerankForm.value.model) { alert('Please fill URL and Model'); return }
  try { await api.updateRerankModel(rerankForm.value); showRerank.value = false; await load() } catch (e) { alert(e.message) }
}

async function deleteRerank() {
  if (!confirm('Delete rerank model config?')) return
  try { await api.deleteRerankModel(); showRerank.value = false; await load() } catch (e) { alert(e.message) }
}

function maskKey(key) {
  if (!key || key.length < 8) return '***'
  return key.slice(0, 4) + '...' + key.slice(-4)
}
</script>

<template>
  <div class="main-header">
    <h2>Models</h2>
    <button class="btn btn-primary btn-sm" @click="openAdd">+ Add Model</button>
  </div>
  <div class="main-content">
    <div v-if="models.length === 0" class="empty-state">
      <div class="icon">...</div>
      <div>No models yet, click above to add</div>
    </div>

    <div class="card" v-for="m in models" :key="m.name">
      <div class="card-header">
        <div>
          <h3>{{ m.name }} <span v-if="m.name === lastSelected" class="badge badge-active">Active</span></h3>
          <div class="text-sm text-dim">{{ m.model }} | {{ (m.context_limit || 128000).toLocaleString() }} tokens <span v-if="m.vision" class="badge" style="background:#10b981;">Vision</span></div>
        </div>
        <div class="flex items-center" style="gap: 8px;">
          <button class="btn btn-secondary btn-sm" @click="openEdit(m)">Edit</button>
          <button v-if="m.name !== lastSelected" class="btn btn-primary btn-sm" @click="select(m.name)">Use</button>
          <button class="btn btn-danger btn-sm" @click="remove(m.name)">Delete</button>
        </div>
      </div>
      <div class="text-sm text-dim">
        URL: {{ m.url }} | Key: {{ maskKey(m.apiKey) }}
      </div>
    </div>

    <!-- Embedding Model -->
    <div class="card mt-4">
      <div class="card-header">
        <h3>Embedding Model</h3>
        <button class="btn btn-secondary btn-sm" @click="openEmbedding">{{ embeddingModel ? 'Edit' : 'Configure' }}</button>
      </div>
      <div v-if="embeddingModel" class="text-sm">
        <div>Name: {{ embeddingModel.name || '-' }}</div>
        <div>Model: {{ embeddingModel.model }} | URL: {{ embeddingModel.url }}</div>
        <div>Key: {{ maskKey(embeddingModel.apiKey) }}</div>
      </div>
      <div v-else class="text-sm text-dim">Not configured</div>
    </div>

    <!-- Rerank Model -->
    <div class="card">
      <div class="card-header">
        <h3>Rerank Model</h3>
        <button class="btn btn-secondary btn-sm" @click="openRerank">{{ rerankModel ? 'Edit' : 'Configure' }}</button>
      </div>
      <div v-if="rerankModel" class="text-sm">
        <div>Name: {{ rerankModel.name || '-' }}</div>
        <div>Model: {{ rerankModel.model }} | URL: {{ rerankModel.url }}</div>
        <div>Key: {{ maskKey(rerankModel.apiKey) }} | Top N: {{ rerankModel.top_n || '-' }}</div>
      </div>
      <div v-else class="text-sm text-dim">Not configured</div>
    </div>
  </div>

  <!-- Chat Model Add/Edit Modal -->
  <div v-if="showAdd || showEdit" class="modal-overlay" @click.self="showAdd = false; showEdit = false">
    <div class="modal">
      <h3>{{ showEdit ? 'Edit Model' : 'Add Model' }}</h3>
      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="e.g. gpt4o" :disabled="showEdit" />
      </div>
      <div class="form-group">
        <label>API Key</label>
        <input v-model="form.apiKey" type="password" placeholder="sk-..." />
      </div>
      <div class="form-group">
        <label>API URL</label>
        <input v-model="form.url" placeholder="https://api.openai.com/v1" />
      </div>
      <div class="form-group">
        <label>Model Name</label>
        <input v-model="form.model" placeholder="gpt-4o" />
      </div>
      <div class="form-group">
        <label>Context Limit (tokens)</label>
        <input v-model.number="form.context_limit" type="number" />
      </div>
      <div class="form-group">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
          <input type="checkbox" v-model="form.vision" />
          <span>Support Vision (image input)</span>
        </label>
        <div class="text-sm text-dim">Enable if the model supports image/video understanding</div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" @click="showAdd = false; showEdit = false">Cancel</button>
        <button class="btn btn-primary" @click="showEdit ? updateModel() : addModel()">
          {{ showEdit ? 'Save' : 'Add' }}
        </button>
      </div>
    </div>
  </div>

  <!-- Embedding Model Modal -->
  <div v-if="showEmbedding" class="modal-overlay" @click.self="showEmbedding = false">
    <div class="modal">
      <h3>Embedding Model</h3>
      <div class="form-group">
        <label>Name</label>
        <input v-model="embForm.name" placeholder="embedding-name" />
      </div>
      <div class="form-group">
        <label>API Key</label>
        <input v-model="embForm.apiKey" type="password" placeholder="sk-..." />
      </div>
      <div class="form-group">
        <label>API URL</label>
        <input v-model="embForm.url" placeholder="https://api.openai.com/v1" />
      </div>
      <div class="form-group">
        <label>Model Name</label>
        <input v-model="embForm.model" placeholder="text-embedding-3-small" />
      </div>
      <div class="modal-actions">
        <button v-if="embeddingModel" class="btn btn-danger" @click="deleteEmbedding">Delete</button>
        <div style="flex:1"></div>
        <button class="btn btn-secondary" @click="showEmbedding = false">Cancel</button>
        <button class="btn btn-primary" @click="saveEmbedding">Save</button>
      </div>
    </div>
  </div>

  <!-- Rerank Model Modal -->
  <div v-if="showRerank" class="modal-overlay" @click.self="showRerank = false">
    <div class="modal">
      <h3>Rerank Model</h3>
      <div class="form-group">
        <label>Name</label>
        <input v-model="rerankForm.name" placeholder="rerank-name" />
      </div>
      <div class="form-group">
        <label>API Key</label>
        <input v-model="rerankForm.apiKey" type="password" placeholder="sk-..." />
      </div>
      <div class="form-group">
        <label>API URL</label>
        <input v-model="rerankForm.url" placeholder="https://api.jina.ai/v1" />
      </div>
      <div class="form-group">
        <label>Model Name</label>
        <input v-model="rerankForm.model" placeholder="jina-reranker-v2-base-multilingual" />
      </div>
      <div class="form-group">
        <label>Top N</label>
        <input v-model.number="rerankForm.top_n" type="number" placeholder="5" />
      </div>
      <div class="modal-actions">
        <button v-if="rerankModel" class="btn btn-danger" @click="deleteRerank">Delete</button>
        <div style="flex:1"></div>
        <button class="btn btn-secondary" @click="showRerank = false">Cancel</button>
        <button class="btn btn-primary" @click="saveRerank">Save</button>
      </div>
    </div>
  </div>
</template>
