import { createRouter, createWebHashHistory } from 'vue-router'
import ChatView from './views/ChatView.vue'
import AgentsView from './views/AgentsView.vue'
import ModelsView from './views/ModelsView.vue'
import HistoryView from './views/HistoryView.vue'
import SettingsView from './views/SettingsView.vue'
import SkillsView from './views/SkillsView.vue'
import MCPView from './views/MCPView.vue'
import KnowledgeView from './views/KnowledgeView.vue'

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', component: ChatView },
  { path: '/agents', component: AgentsView },
  { path: '/models', component: ModelsView },
  { path: '/skills', component: SkillsView },
  { path: '/mcp', component: MCPView },
  { path: '/knowledge', component: KnowledgeView },
  { path: '/history', component: HistoryView },
  { path: '/settings', component: SettingsView },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
