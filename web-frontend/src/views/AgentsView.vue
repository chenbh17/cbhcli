<script setup>
import { ref, onMounted } from 'vue'
import api from '../api.js'

const agents = ref([])
const activeAgent = ref('')
const showCreate = ref(false)
const showDetail = ref(false)
const form = ref({ name: '', description: '', primary_model: '' })
const detail = ref(null)
const editingFile = ref(null)
const editContent = ref('')
const models = ref([])

onMounted(load)

async function load() {
  try {
    const [ad, md] = await Promise.all([api.getAgents(), api.getModels()])
    agents.value = ad.agents || []
    activeAgent.value = ad.active_agent || ''
    models.value = md.models || []
  } catch (e) { console.error(e) }
}

async function create() {
  if (!form.value.name.trim()) return
  try {
    await api.createAgent(form.value)
    showCreate.value = false
    form.value = { name: '', description: '', primary_model: '' }
    await load()
  } catch (e) { alert(e.message) }
}

async function remove(name) {
  if (!confirm(`确认删除 Agent "${name}"？`)) return
  try { await api.deleteAgent(name); await load() } catch (e) { alert(e.message) }
}

async function select(name) {
  try { await api.selectAgent(name); activeAgent.value = name } catch (e) { alert(e.message) }
}

async function openDetail(name) {
  try {
    detail.value = await api.getAgent(name)
    showDetail.value = true
    editingFile.value = null
  } catch (e) { alert(e.message) }
}

function startEdit(fname) {
  editingFile.value = fname
  editContent.value = detail.value.files[fname] || ''
}

async function saveFile() {
  if (!editingFile.value || !detail.value) return
  try {
    await api.updateAgentFile(detail.value.config.name, editingFile.value, editContent.value)
    detail.value.files[editingFile.value] = editContent.value
    editingFile.value = null
    alert('已保存')
  } catch (e) { alert(e.message) }
}
</script>

<template>
  <div class="main-header">
    <h2>🤖 Agent 管理</h2>
    <button class="btn btn-primary btn-sm" @click="showCreate = true">+ 创建</button>
  </div>
  <div class="main-content">
    <div v-if="agents.length === 0" class="empty-state">
      <div class="icon">🤖</div>
      <div>还没有 Agent，点击上方创建</div>
    </div>

    <div class="card" v-for="a in agents" :key="a.name">
      <div class="card-header">
        <div>
          <h3>{{ a.name }} <span v-if="a.name === activeAgent" class="badge badge-active">当前</span></h3>
          <div class="text-sm text-dim">{{ a.description || '无描述' }}</div>
        </div>
        <div class="flex items-center" style="gap: 8px;">
          <button class="btn btn-secondary btn-sm" @click="openDetail(a.name)">详情</button>
          <button v-if="a.name !== activeAgent" class="btn btn-primary btn-sm" @click="select(a.name)">切换</button>
          <button class="btn btn-danger btn-sm" @click="remove(a.name)">删除</button>
        </div>
      </div>
      <div class="text-sm text-dim">
        模型: {{ a.primary_model || '未指定' }} | 创建于: {{ (a.created_at || '').slice(0, 10) }}
      </div>
    </div>
  </div>

  <!-- 创建对话框 -->
  <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
    <div class="modal">
      <h3>创建 Agent</h3>
      <div class="form-group">
        <label>名称</label>
        <input v-model="form.name" placeholder="如: coding-helper" />
      </div>
      <div class="form-group">
        <label>描述</label>
        <input v-model="form.description" placeholder="可选描述" />
      </div>
      <div class="form-group">
        <label>默认模型</label>
        <select v-model="form.primary_model">
          <option value="">不指定</option>
          <option v-for="m in models" :key="m.name" :value="m.name">{{ m.name }}</option>
        </select>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" @click="showCreate = false">取消</button>
        <button class="btn btn-primary" @click="create">创建</button>
      </div>
    </div>
  </div>

  <!-- 详情对话框 -->
  <div v-if="showDetail && detail" class="modal-overlay" @click.self="showDetail = false">
    <div class="modal" style="min-width: 600px; max-width: 800px;">
      <h3>{{ detail.config.name }} 详情</h3>

      <div v-if="!editingFile">
        <div class="card" v-for="fname in ['soul.md', 'tools.md', 'memory.md', 'usage.md']" :key="fname">
          <div class="card-header">
            <h3>{{ fname }}</h3>
            <button class="btn btn-secondary btn-sm" @click="startEdit(fname)">编辑</button>
          </div>
          <pre style="max-height: 120px; overflow: auto; font-size: 12px; white-space: pre-wrap; color: var(--text-dim);">{{ (detail.files[fname] || '').slice(0, 500) }}{{ (detail.files[fname] || '').length > 500 ? '...' : '' }}</pre>
        </div>

        <div v-if="detail.skills && detail.skills.length" class="card">
          <h3 class="mb-2">技能 ({{ detail.skills.length }})</h3>
          <div v-for="s in detail.skills" :key="s.name" class="list-item">
            <span class="item-name">{{ s.name }}</span>
          </div>
        </div>
      </div>

      <div v-else>
        <h3 class="mb-2">编辑 {{ editingFile }}</h3>
        <textarea v-model="editContent" rows="20" style="width: 100%; font-family: monospace; font-size: 13px;"></textarea>
        <div class="modal-actions">
          <button class="btn btn-secondary" @click="editingFile = null">取消</button>
          <button class="btn btn-primary" @click="saveFile">保存</button>
        </div>
      </div>

      <div v-if="!editingFile" class="modal-actions">
        <button class="btn btn-secondary" @click="showDetail = false">关闭</button>
      </div>
    </div>
  </div>
</template>
