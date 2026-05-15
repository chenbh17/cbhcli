<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import api from './api.js'

const router = useRouter()
const route = useRoute()
const info = ref({})

const navItems = [
  { path: '/chat', icon: '>', label: 'Chat' },
  { path: '/agents', icon: '@', label: 'Agents' },
  { path: '/models', icon: '#', label: 'Models' },
  { path: '/tools', icon: '!', label: 'Tools' },
  { path: '/skills', icon: '*', label: 'Skills' },
  { path: '/mcp', icon: '~', label: 'MCP' },
  { path: '/knowledge', icon: '&', label: 'Knowledge' },
  { path: '/history', icon: '=', label: 'History' },
  { path: '/settings', icon: '%', label: 'Settings' },
]

onMounted(async () => {
  try { info.value = await api.getInfo() } catch (e) { console.error(e) }
})
</script>

<template>
  <div class="sidebar">
    <div class="sidebar-header">
      CBHCLI <span class="version">v{{ info.version || '...' }}</span>
    </div>
    <nav class="sidebar-nav">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: route.path === item.path }"
      >
        <span class="icon">{{ item.icon }}</span>
        {{ item.label }}
      </router-link>
    </nav>
    <div style="padding: 12px; border-top: 1px solid var(--border);">
      <div class="text-sm text-dim">Agent: {{ info.active_agent || '-' }}</div>
      <div class="text-sm text-dim">Model: {{ info.last_model || '-' }}</div>
    </div>
  </div>
  <div class="main-area">
    <router-view v-slot="{ Component }">
      <keep-alive include="ChatView">
        <component :is="Component" />
      </keep-alive>
    </router-view>
  </div>
</template>
