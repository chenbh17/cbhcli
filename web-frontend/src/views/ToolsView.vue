<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api.js'

const tools = ref([])
const loading = ref(false)
const agentName = ref('')
const collapsed = ref({})

const categories = computed(() => {
  const map = {}
  for (const t of tools.value) {
    if (!map[t.category]) map[t.category] = []
    map[t.category].push(t)
  }
  return map
})

const enabledCount = computed(() => tools.value.filter(t => t.enabled).length)
const disabledCount = computed(() => tools.value.filter(t => !t.enabled).length)

function toggleCollapse(cat) {
  collapsed.value[cat] = !collapsed.value[cat]
}

async function loadTools() {
  loading.value = true
  try {
    const info = await api.getInfo()
    agentName.value = info.active_agent || 'main'
    const data = await api.getTools(agentName.value)
    tools.value = data.tools || []
    // 数据科学默认折叠
    for (const cat in categories.value) {
      if (cat === '数据科学') collapsed.value[cat] = true
    }
  } catch (e) {
    console.error('Failed to load tools:', e)
  } finally {
    loading.value = false
  }
}

async function toggleTool(tool) {
  try {
    await api.toggleTool(agentName.value, tool.name, !tool.enabled)
    tool.enabled = !tool.enabled
  } catch (e) {
    alert('操作失败: ' + e.message)
  }
}

async function enableAll() {
  for (const t of tools.value) {
    if (!t.enabled) {
      await api.toggleTool(agentName.value, t.name, true)
      t.enabled = true
    }
  }
}

async function toggleCategory(catName) {
  const catTools = categories.value[catName] || []
  const allOn = catTools.every(t => t.enabled)
  for (const t of catTools) {
    await api.toggleTool(agentName.value, t.name, !allOn)
    t.enabled = !allOn
  }
}

onMounted(loadTools)
</script>

<template>
  <div class="tools-page">
    <div class="toolbar">
      <div class="toolbar-left">
        <span class="title">🔧 工具管理</span>
        <span class="badge on">{{ enabledCount }} 启用</span>
        <span class="badge off">{{ disabledCount }} 禁用</span>
      </div>
      <button class="btn-all" @click="enableAll">全部启用</button>
    </div>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else class="cat-list">
      <div v-for="(catTools, catName) in categories" :key="catName" class="cat">
        <div class="cat-head" @click="toggleCollapse(catName)">
          <span class="arrow" :class="{ open: !collapsed[catName] }">▶</span>
          <span class="cat-name">{{ catName }}</span>
          <span class="cat-count">{{ catTools.filter(t=>t.enabled).length }}/{{ catTools.length }}</span>
          <button class="cat-btn" @click.stop="toggleCategory(catName)">
            {{ catTools.every(t=>t.enabled) ? '全关' : '全开' }}
          </button>
        </div>

        <div v-show="!collapsed[catName]" class="grid">
          <div
            v-for="tool in catTools"
            :key="tool.name"
            class="cell"
            :class="{ off: !tool.enabled }"
            @click="toggleTool(tool)"
          >
            <div class="cell-head">
              <span class="name">{{ tool.name }}</span>
              <span class="dot" :class="{ on: tool.enabled }"></span>
            </div>
            <div class="desc">{{ tool.description }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tools-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.toolbar-left { display: flex; align-items: center; gap: 10px; }
.title { font-size: 15px; font-weight: 600; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }
.badge.on { background: rgba(34,197,94,0.15); color: #22c55e; }
.badge.off { background: rgba(239,68,68,0.15); color: #ef4444; }
.btn-all {
  padding: 4px 12px; border-radius: 6px; border: 1px solid var(--border);
  background: transparent; color: var(--text); cursor: pointer; font-size: 12px;
}
.btn-all:hover { background: var(--hover); }

.loading { text-align: center; padding: 60px; color: var(--text-dim); }

.cat-list { flex: 1; overflow-y: auto; padding: 8px 12px; }

.cat { margin-bottom: 6px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.cat-head {
  display: flex; align-items: center; gap: 8px; padding: 10px 14px;
  cursor: pointer; background: rgba(255,255,255,0.02); user-select: none;
}
.cat-head:hover { background: rgba(255,255,255,0.04); }
.arrow {
  font-size: 10px; color: var(--text-dim); transition: transform 0.15s;
  display: inline-block; width: 14px; text-align: center;
}
.arrow.open { transform: rotate(90deg); }
.cat-name { font-size: 13px; font-weight: 600; flex: 1; }
.cat-count { font-size: 11px; color: var(--text-dim); margin-right: 4px; }
.cat-btn {
  padding: 2px 8px; border-radius: 4px; border: 1px solid var(--border);
  background: transparent; color: var(--text-dim); cursor: pointer; font-size: 11px;
}
.cat-btn:hover { background: var(--hover); color: var(--text); }

/* 多列网格 */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1px;
  background: var(--border);
  border-top: 1px solid var(--border);
}

.cell {
  padding: 10px 14px;
  background: var(--bg, #1a1a1a);
  cursor: pointer;
  transition: background 0.1s;
}
.cell:hover { background: rgba(255,255,255,0.04); }
.cell.off { opacity: 0.4; }

.cell-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

/* 单个圆点指示器 */
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #555;
  flex-shrink: 0;
  transition: background 0.2s;
}
.dot.on { background: #22c55e; }

.name {
  font-size: 12px; font-weight: 500; font-family: monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.desc {
  font-size: 11px; color: var(--text-dim); margin-top: 3px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
</style>
