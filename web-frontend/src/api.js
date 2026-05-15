const BASE = '/api'

async function request(path, options = {}) {
  const url = `${BASE}${path}`
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.message || JSON.stringify(err))
  }
  return res.json()
}

export default {
  // 系统
  getInfo: () => request('/info'),

  // 模型
  getModels: () => request('/models'),
  addModel: (data) => request('/models', { method: 'POST', body: JSON.stringify(data) }),
  updateModel: (name, data) => request(`/models/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteModel: (name) => request(`/models/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  selectModel: (name) => request(`/models/${encodeURIComponent(name)}/select`, { method: 'POST' }),

  // Agent
  getAgents: () => request('/agents'),
  createAgent: (data) => request('/agents', { method: 'POST', body: JSON.stringify(data) }),
  getAgent: (name) => request(`/agents/${encodeURIComponent(name)}`),
  updateAgent: (name, data) => request(`/agents/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteAgent: (name) => request(`/agents/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  selectAgent: (name) => request(`/agents/${encodeURIComponent(name)}/select`, { method: 'POST' }),
  updateAgentFile: (name, filename, content) =>
    request(`/agents/${encodeURIComponent(name)}/files/${filename}`, { method: 'PUT', body: JSON.stringify({ content }) }),

  // 历史
  getHistory: (agent, limit = 20) => request(`/agents/${encodeURIComponent(agent)}/history?limit=${limit}`),
  getHistoryDetail: (agent, filename) => request(`/agents/${encodeURIComponent(agent)}/history/${encodeURIComponent(filename)}`),
  deleteHistory: (agent, filename) => request(`/agents/${encodeURIComponent(agent)}/history/${encodeURIComponent(filename)}`, { method: 'DELETE' }),

  // 设置
  getSettings: () => request('/settings'),
  updateSettings: (data) => request('/settings', { method: 'PUT', body: JSON.stringify(data) }),

  // 对话 (SSE)
  chatStream: (message, agentName, modelName) => {
    return fetch(`${BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, agent_name: agentName, model_name: modelName }),
    })
  },
  resetChat: (agentName, modelName) =>
    request('/chat/reset', { method: 'POST', body: JSON.stringify({ agent_name: agentName, model_name: modelName }) }),
  chatRespond: (agentName, modelName, response) =>
    request('/chat/respond', { method: 'POST', body: JSON.stringify({ agent_name: agentName, model_name: modelName, response }) }),
  chatStatus: (agentName, modelName) =>
    request(`/chat/status?agent_name=${encodeURIComponent(agentName)}&model_name=${encodeURIComponent(modelName)}`),
  chatAbort: (agentName, modelName) =>
    request('/chat/abort', { method: 'POST', body: JSON.stringify({ agent_name: agentName, model_name: modelName }) }),
  chatLoad: (agentName, modelName, filename) =>
    request('/chat/load', { method: 'POST', body: JSON.stringify({ agent_name: agentName, model_name: modelName, filename }) }),
  chatUpload: (file, agentName, modelName) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('agent_name', agentName)
    fd.append('model_name', modelName)
    return fetch(`${BASE}/chat/upload`, { method: 'POST', body: fd })
      .then(res => {
        if (!res.ok) return res.json().then(e => { throw new Error(e.detail || 'Upload failed') })
        return res.json()
      })
  },

  // 嵌入模型
  updateEmbeddingModel: (data) => request('/models/embedding', { method: 'PUT', body: JSON.stringify(data) }),
  deleteEmbeddingModel: () => request('/models/embedding', { method: 'DELETE' }),

  // 重排序模型
  updateRerankModel: (data) => request('/models/rerank', { method: 'PUT', body: JSON.stringify(data) }),
  deleteRerankModel: () => request('/models/rerank', { method: 'DELETE' }),

  // 技能
  getSkills: (agentName) => request(`/agents/${encodeURIComponent(agentName)}/skills`),
  activateSkills: (agentName, names) =>
    request(`/agents/${encodeURIComponent(agentName)}/skills/activate`, { method: 'POST', body: JSON.stringify({ names }) }),
  deactivateSkill: (agentName, name) =>
    request(`/agents/${encodeURIComponent(agentName)}/skills/${encodeURIComponent(name)}/deactivate`, { method: 'POST' }),
  deleteSkill: (agentName, name) =>
    request(`/agents/${encodeURIComponent(agentName)}/skills/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  // MCP
  getMCPServers: (agentName) => request(`/agents/${encodeURIComponent(agentName)}/mcp`),
  addMCPServer: (agentName, data) =>
    request(`/agents/${encodeURIComponent(agentName)}/mcp`, { method: 'POST', body: JSON.stringify(data) }),
  removeMCPServer: (agentName, name) =>
    request(`/agents/${encodeURIComponent(agentName)}/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  refreshMCPServer: (agentName, name) =>
    request(`/agents/${encodeURIComponent(agentName)}/mcp/${encodeURIComponent(name)}/refresh`, { method: 'POST' }),
  toggleMCPTool: (agentName, serverName, toolName, enable) =>
    request(`/agents/${encodeURIComponent(agentName)}/mcp/${encodeURIComponent(serverName)}/tools/${encodeURIComponent(toolName)}`, {
      method: 'PUT', body: JSON.stringify({ enable }),
    }),

  // 工具管理
  getTools: (agentName) => request(`/agents/${encodeURIComponent(agentName)}/tools`),
  toggleTool: (agentName, toolName, enable) =>
    request(`/agents/${encodeURIComponent(agentName)}/tools/${encodeURIComponent(toolName)}`, {
      method: 'PUT', body: JSON.stringify({ enable }),
    }),

  // 知识库
  getKnowledge: (agentName) => request(`/agents/${encodeURIComponent(agentName)}/knowledge`),
  addKnowledgeFile: (agentName, filePath) =>
    request(`/agents/${encodeURIComponent(agentName)}/knowledge`, { method: 'POST', body: JSON.stringify({ file_path: filePath }) }),
  uploadKnowledgeFile: (agentName, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`${BASE}/agents/${encodeURIComponent(agentName)}/knowledge/upload`, { method: 'POST', body: fd })
      .then(res => {
        if (!res.ok) return res.json().then(e => { throw new Error(e.detail || 'Upload failed') })
        return res.json()
      })
  },
  removeKnowledgeFile: (agentName, fileName) =>
    request(`/agents/${encodeURIComponent(agentName)}/knowledge/${encodeURIComponent(fileName)}`, { method: 'DELETE' }),
  reindexKnowledge: (agentName) =>
    request(`/agents/${encodeURIComponent(agentName)}/knowledge/reindex`, { method: 'POST' }),
}
