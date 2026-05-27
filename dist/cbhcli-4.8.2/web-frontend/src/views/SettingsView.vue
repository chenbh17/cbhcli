<script setup>
import { ref, onMounted } from 'vue'
import api from '../api.js'

const settings = ref({})
const configDir = ref('')
const info = ref({})

onMounted(async () => {
  try {
    const [sd, id] = await Promise.all([api.getSettings(), api.getInfo()])
    settings.value = sd.settings || {}
    configDir.value = sd.config_dir || ''
    info.value = id
  } catch (e) { console.error(e) }
})

async function save() {
  try {
    await api.updateSettings({
      auto_compress: settings.value.auto_compress,
      compression_ratio: settings.value.compression_ratio,
    })
    alert('设置已保存')
  } catch (e) { alert(e.message) }
}
</script>

<template>
  <div class="main-header">
    <h2>⚙️ 设置</h2>
  </div>
  <div class="main-content">
    <div class="card">
      <h3 class="mb-2">系统信息</h3>
      <div class="text-sm" style="display: grid; grid-template-columns: 140px 1fr; gap: 8px;">
        <span class="text-dim">版本:</span> <span>v{{ info.version }}</span>
        <span class="text-dim">配置目录:</span> <span>{{ configDir }}</span>
        <span class="text-dim">Agent 数量:</span> <span>{{ info.agents_count }}</span>
        <span class="text-dim">模型数量:</span> <span>{{ info.models_count }}</span>
        <span class="text-dim">当前 Agent:</span> <span>{{ info.active_agent || '-' }}</span>
        <span class="text-dim">当前模型:</span> <span>{{ info.last_model || '-' }}</span>
      </div>
    </div>

    <div class="card">
      <h3 class="mb-2">上下文管理</h3>
      <div class="form-group">
        <label>
          <input type="checkbox" v-model="settings.auto_compress" style="width: auto; margin-right: 6px;" />
          自动压缩上下文
        </label>
      </div>
      <div class="form-group">
        <label>压缩触发阈值</label>
        <div class="flex items-center" style="gap: 10px;">
          <input type="range" v-model.number="settings.compression_ratio" min="0.5" max="0.95" step="0.05"
            style="flex: 1; accent-color: var(--primary);" />
          <span style="min-width: 50px; text-align: right;">{{ ((settings.compression_ratio || 0.8) * 100).toFixed(0) }}%</span>
        </div>
      </div>
      <button class="btn btn-primary" @click="save">保存设置</button>
    </div>

    <div class="card">
      <h3 class="mb-2">使用说明</h3>
      <div class="text-sm text-dim">
        <p>Web 管理界面可以管理 Agent、模型、历史会话和进行 AI 对话。</p>
        <p class="mt-2">终端命令: <code style="background: var(--bg); padding: 2px 6px; border-radius: 4px; font-size: 12px;">cbhcli web -p 18888</code></p>
        <p class="mt-2">MCP 服务器、技能创建等高级操作请使用终端 CLI。</p>
      </div>
    </div>
  </div>
</template>
