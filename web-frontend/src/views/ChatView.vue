<script>
export default { name: 'ChatView' }
</script>

<script setup>
import { ref, reactive, nextTick, onMounted, onUnmounted, onActivated, watch } from 'vue'
import { marked } from 'marked'
import api from '../api.js'

const agents = ref([])
const models = ref([])
const selectedAgent = ref('')
const selectedModel = ref('')
const messages = ref([])
const input = ref('')
const loading = ref(false)
const messagesEl = ref(null)
const fileInputEl = ref(null)

// Status bar
const cwd = ref('')
const ctxPercentage = ref(0)
const tokenEstimate = ref(0)
const modelLimit = ref(0)
let statusTimer = null

// Pending tool confirm / ask_user / password
const pendingAction = ref(null)
const userInput = ref('')

// Uploaded files for next message
const attachedFiles = ref([]) // [{filename, path, is_image, base64, size}]

// Abort controller for stopping stream
let currentAbortController = null

// Last assistant message index for retry
const lastAssistantIdx = ref(-1)

// History sidebar
const historySessions = ref([])
const historyLoading = ref(false)
const showHistory = ref(true)

// Track previous agent to detect real changes
let _prevAgent = ''

onMounted(async () => {
  try {
    const [agentData, modelData] = await Promise.all([api.getAgents(), api.getModels()])
    agents.value = agentData.agents || []
    models.value = modelData.models || []
    selectedAgent.value = agentData.active_agent || (agents.value[0]?.name || '')
    selectedModel.value = modelData.last_selected || (models.value[0]?.name || '')
    _prevAgent = selectedAgent.value
    refreshStatus()
    loadHistory()
  } catch (e) { console.error(e) }

  statusTimer = setInterval(refreshStatus, 3000)
})

onUnmounted(() => {
  if (statusTimer) clearInterval(statusTimer)
})

// When re-activated from keep-alive, just scroll to bottom
onActivated(() => {
  scrollBottom()
})

// Watch agent change: only reset chat when user actually switches agent
watch(selectedAgent, (newVal, oldVal) => {
  if (oldVal && newVal && oldVal !== newVal) {
    _prevAgent = newVal
    resetChat()
    loadHistory()
  }
})

async function refreshStatus() {
  if (!selectedAgent.value || !selectedModel.value) return
  try {
    const s = await api.chatStatus(selectedAgent.value, selectedModel.value)
    cwd.value = s.cwd || ''
    ctxPercentage.value = s.ctx_percentage || 0
    tokenEstimate.value = s.token_estimate || 0
    modelLimit.value = s.model_limit || 0
  } catch (e) { /* ignore */ }
}

function scrollBottom() {
  nextTick(() => {
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  })
}

function renderMd(text) {
  if (!text) return ''
  return marked.parse(text, { breaks: true })
}

function ctxColor() {
  if (ctxPercentage.value >= 80) return 'var(--danger)'
  if (ctxPercentage.value >= 50) return 'var(--warning)'
  return 'var(--success)'
}

// ===== File Upload =====
function triggerFileUpload() {
  if (fileInputEl.value) fileInputEl.value.click()
}

async function handleFileSelect(event) {
  const files = event.target.files
  if (!files || files.length === 0) return
  if (!selectedAgent.value || !selectedModel.value) {
    alert('Please select Agent and Model first')
    return
  }

  for (const file of files) {
    try {
      const result = await api.chatUpload(file, selectedAgent.value, selectedModel.value)
      attachedFiles.value.push(result)
    } catch (e) {
      alert(`Upload failed: ${e.message}`)
    }
  }
  // Reset input
  event.target.value = ''
}

function removeAttachment(idx) {
  attachedFiles.value.splice(idx, 1)
}

// ===== Helpers for interleaved blocks =====
// Each assistant message has a `blocks` array:
//   { type: 'reasoning', content: '...' }
//   { type: 'content', content: '...' }
//   { type: 'tool_call', info, name, status, tool_id, result_preview }

function getOrCreateBlock(assistantMsg, type) {
  const blocks = assistantMsg.blocks
  const last = blocks.length > 0 ? blocks[blocks.length - 1] : null
  if (last && last.type === type) return last
  const block = { type, content: '' }
  blocks.push(block)
  return block
}

function findToolBlock(assistantMsg, tool_id) {
  return assistantMsg.blocks.find(b => b.type === 'tool_call' && b.tool_id === tool_id)
}

// ===== Send Message =====
async function send() {
  const msg = input.value.trim()
  if ((!msg && attachedFiles.value.length === 0) || loading.value) return
  if (!selectedAgent.value || !selectedModel.value) {
    alert('Please select Agent and Model first')
    return
  }

  // Build user message with file info
  let userContent = msg
  const fileInfos = []
  for (const f of attachedFiles.value) {
    if (f.is_image && f.base64) {
      fileInfos.push(`[Image: ${f.filename}]`)
    } else {
      fileInfos.push(`[File: ${f.filename} (${f.path})]`)
    }
  }
  if (fileInfos.length > 0) {
    userContent = fileInfos.join('\n') + (msg ? '\n' + msg : '')
  }

  messages.value.push({ role: 'user', content: userContent, attachments: [...attachedFiles.value] })
  input.value = ''
  attachedFiles.value = []
  scrollBottom()

  await doStream(userContent)
}

async function doStream(userMessage) {
  loading.value = true
  const assistantMsg = reactive({ role: 'assistant', blocks: [] })
  messages.value.push(assistantMsg)
  lastAssistantIdx.value = messages.value.length - 1

  const abortCtrl = new AbortController()
  currentAbortController = abortCtrl

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: userMessage, agent_name: selectedAgent.value, model_name: selectedModel.value }),
      signal: abortCtrl.signal,
    })
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const jsonStr = line.slice(6)
        try {
          const data = JSON.parse(jsonStr)
          if (data.type === 'content') {
            const block = getOrCreateBlock(assistantMsg, 'content')
            block.content += data.content
            scrollBottom()
          } else if (data.type === 'reasoning') {
            const block = getOrCreateBlock(assistantMsg, 'reasoning')
            block.content += data.content
            scrollBottom()
          } else if (data.type === 'tool_confirm') {
            const toolName = data.tool_name || 'unknown'
            const toolArgs = data.tool_args || ''
            assistantMsg.blocks.push({
              type: 'tool_call',
              info: `${toolName}: ${toolArgs}`,
              name: toolName,
              status: 'pending',
              tool_id: data.tool_id,
              result_preview: '',
            })
            pendingAction.value = { type: 'tool_confirm', data: { tool_name: toolName, tool_args: toolArgs } }
            scrollBottom()
          } else if (data.type === 'tool_auto_confirmed') {
            const tb = findToolBlock(assistantMsg, data.tool_id)
            if (tb) tb.status = 'confirmed'
            pendingAction.value = null
            scrollBottom()
          } else if (data.type === 'tool_executing') {
            const tb = findToolBlock(assistantMsg, data.tool_id)
            if (tb) tb.status = 'executing'
            pendingAction.value = null
            scrollBottom()
          } else if (data.type === 'tool_rejected') {
            const tb = findToolBlock(assistantMsg, data.tool_id)
            if (tb) tb.status = 'rejected'
            pendingAction.value = null
            scrollBottom()
          } else if (data.type === 'tool_result') {
            const tb = findToolBlock(assistantMsg, data.tool_id)
            if (tb) {
              tb.status = data.success ? 'confirmed' : 'failed'
              tb.result_preview = data.preview || ''
            }
            scrollBottom()
          } else if (data.type === 'ask_user') {
            pendingAction.value = { type: 'ask_user', data: data }
            assistantMsg.blocks.push({
              type: 'tool_call',
              info: `ask_user: ${data.question || ''}`,
              name: 'ask_user',
              status: 'pending',
              tool_id: data.tool_id,
              result_preview: '',
            })
            scrollBottom()
          } else if (data.type === 'password') {
            pendingAction.value = { type: 'password', data: data }
          } else if (data.type === 'aborted') {
            const block = getOrCreateBlock(assistantMsg, 'content')
            block.content += '\n\n*[Stopped]*'
            scrollBottom()
          } else if (data.type === 'error') {
            const block = getOrCreateBlock(assistantMsg, 'content')
            block.content += `\n\n**Error:** ${data.content}`
            scrollBottom()
          } else if (data.type === 'done') {
            // Stream complete
          }
        } catch (e) { /* skip parse errors */ }
      }
    }
  } catch (e) {
    const block = getOrCreateBlock(assistantMsg, 'content')
    if (e.name !== 'AbortError') {
      block.content += `\n\n**Request failed:** ${e.message}`
    } else {
      block.content += '\n\n*[Stopped]*'
    }
  }

  loading.value = false
  if (pendingAction.value && pendingAction.value.type !== 'tool_confirm') {
    pendingAction.value = null
  }
  currentAbortController = null
  scrollBottom()
  refreshStatus()
}

// ===== Stop =====
async function stopStream() {
  if (currentAbortController) {
    currentAbortController.abort()
    currentAbortController = null
  }
  try {
    await api.chatAbort(selectedAgent.value, selectedModel.value)
  } catch (_) {}
}

// ===== Retry =====
async function retryLast() {
  if (loading.value) return
  if (lastAssistantIdx.value < 0 || lastAssistantIdx.value >= messages.value.length) return

  // Find the user message before the last assistant message
  let userMsgIdx = lastAssistantIdx.value - 1
  while (userMsgIdx >= 0 && messages.value[userMsgIdx].role !== 'user') {
    userMsgIdx--
  }
  if (userMsgIdx < 0) return

  const userMsg = messages.value[userMsgIdx].content

  // Remove the old assistant message
  messages.value.splice(lastAssistantIdx.value, 1)
  scrollBottom()

  await doStream(userMsg)
}

// ===== Tool confirm respond =====
async function respondAction(response) {
  if (!pendingAction.value) return
  try {
    await api.chatRespond(selectedAgent.value, selectedModel.value, response)
  } catch (e) { console.error(e) }
  // Mark last pending tool_call block based on response
  if (messages.value.length > 0) {
    const last = messages.value[messages.value.length - 1]
    if (last.blocks) {
      const pendingTc = [...last.blocks].reverse().find(b => b.type === 'tool_call' && b.status === 'pending')
      if (pendingTc) {
        pendingTc.status = response === 'n' ? 'rejected' : 'confirmed'
      }
    }
  }
  pendingAction.value = null
  userInput.value = ''
}

async function submitUserInput() {
  const val = userInput.value.trim()
  if (!val) return
  await respondAction(val)
}

function selectAskOption(optText) {
  respondAction(optText)
}

// ===== Reset =====
async function resetChat() {
  if (!selectedAgent.value || !selectedModel.value) return
  try {
    await api.resetChat(selectedAgent.value, selectedModel.value)
    messages.value = []
    lastAssistantIdx.value = -1
    refreshStatus()
    loadHistory()
  } catch (e) { console.error(e) }
}

// ===== History =====
async function loadHistory() {
  if (!selectedAgent.value) return
  historyLoading.value = true
  try {
    const data = await api.getHistory(selectedAgent.value, 30)
    historySessions.value = data.sessions || []
  } catch (e) { console.error(e) }
  historyLoading.value = false
}

async function loadHistorySession(session) {
  if (loading.value) return
  if (!selectedAgent.value || !selectedModel.value) return

  try {
    const data = await api.getHistoryDetail(selectedAgent.value, session.filename)
    const histMsgs = data.messages || []
    if (histMsgs.length === 0) return

    await api.resetChat(selectedAgent.value, selectedModel.value)
    await api.chatLoad(selectedAgent.value, selectedModel.value, session.filename)

    messages.value = []
    for (const msg of histMsgs) {
      if (msg.role === 'system') continue
      if (msg.role === 'tool') continue
      if (msg.role === 'assistant') {
        // Convert old format to blocks
        const blocks = []
        if (msg.reasoning_content) blocks.push({ type: 'reasoning', content: msg.reasoning_content })
        if (msg.content) blocks.push({ type: 'content', content: msg.content })
        messages.value.push({ role: 'assistant', blocks })
      } else {
        messages.value.push({ role: msg.role, content: msg.content || '' })
      }
    }
    lastAssistantIdx.value = messages.value.length > 0 ? messages.value.length - 1 : -1
    scrollBottom()
    refreshStatus()
  } catch (e) {
    console.error('Failed to load history session:', e)
  }
}

function formatHistoryDate(dateStr) {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    const now = new Date()
    const today = now.toDateString()
    const dStr = d.toDateString()
    if (dStr === today) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch (e) { return dateStr }
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="main-header">
    <h2>Chat</h2>
    <select v-model="selectedAgent" style="min-width: 130px;">
      <option value="" disabled>Select Agent</option>
      <option v-for="a in agents" :key="a.name" :value="a.name">{{ a.name }}</option>
    </select>
    <select v-model="selectedModel" style="min-width: 160px;">
      <option value="" disabled>Select Model</option>
      <option v-for="m in models" :key="m.name" :value="m.name">{{ m.name }}</option>
    </select>
    <button class="btn btn-secondary btn-sm" @click="resetChat">New Chat</button>
    <div style="flex:1"></div>
    <!-- Status: cwd + ctx -->
    <div class="status-bar" v-if="cwd">
      <span class="status-cwd" :title="cwd">{{ cwd.length > 40 ? '...' + cwd.slice(-37) : cwd }}</span>
      <span class="status-ctx" :style="{ color: ctxColor() }">
        ctx {{ ctxPercentage }}% ({{ tokenEstimate.toLocaleString() }}/{{ modelLimit.toLocaleString() }})
      </span>
    </div>
  </div>

  <div class="chat-layout">
    <!-- History sidebar -->
    <div class="chat-history-sidebar" v-if="showHistory">
      <div class="history-sidebar-header">
        <span>History</span>
        <button class="btn btn-secondary btn-sm" style="padding:2px 6px;font-size:11px;" @click="loadHistory">Refresh</button>
      </div>
      <div class="history-sidebar-list">
        <div v-if="historySessions.length === 0" class="text-dim text-sm" style="padding:12px;text-align:center;">
          No history
        </div>
        <div
          v-for="s in historySessions" :key="s.filename"
          class="history-sidebar-item"
          @click="loadHistorySession(s)"
          :title="s.title"
        >
          <div class="history-item-title">{{ s.title || 'Untitled' }}</div>
          <div class="history-item-meta">
            <span>{{ s.message_count }} msgs</span>
            <span>{{ formatHistoryDate(s.created_at) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Main chat area -->
    <div class="chat-container" style="flex: 1; overflow: hidden;">
      <div class="chat-messages" ref="messagesEl">
        <div v-if="messages.length === 0" class="empty-state">
          <div style="font-size: 32px; margin-bottom: 12px; color: var(--text-dim);">CBHCLI</div>
          <div>Select an Agent and Model to start chatting</div>
        </div>

        <div v-for="(msg, i) in messages" :key="i" class="chat-msg" :class="msg.role">
          <!-- User message -->
          <template v-if="msg.role === 'user'">
            <div v-if="msg.attachments && msg.attachments.length" class="msg-attachments">
              <div v-for="(att, ai) in msg.attachments" :key="ai" class="attachment-chip">
                <span v-if="att.is_image" class="att-icon">[img]</span>
                <span v-else class="att-icon">[file]</span>
                {{ att.filename }}
              </div>
            </div>
            <div style="white-space: pre-wrap;">{{ msg.content }}</div>
          </template>

          <!-- Assistant message: interleaved blocks -->
          <template v-if="msg.role === 'assistant' && msg.blocks">
            <template v-for="(block, bi) in msg.blocks" :key="bi">
              <!-- Reasoning block -->
              <div v-if="block.type === 'reasoning'" class="reasoning">
                <div style="font-weight: 600; margin-bottom: 4px; font-size: 12px; text-transform: uppercase; letter-spacing: .5px;">Thinking</div>
                <div style="white-space: pre-wrap;">{{ block.content }}</div>
              </div>

              <!-- Tool call block -->
              <div v-if="block.type === 'tool_call'" class="tool-call-item" :class="['tc-' + (block.status || 'pending'), { 'tc-subagent': block.name === 'delegate_task' }]">
                <span class="tc-status-icon" v-if="block.status === 'confirmed'">[OK]</span>
                <span class="tc-status-icon" v-else-if="block.status === 'rejected'">[X]</span>
                <span class="tc-status-icon" v-else-if="block.status === 'executing'">[...]</span>
                <span class="tc-status-icon" v-else-if="block.status === 'failed'">[!]</span>
                <span class="tc-status-icon" v-else>[?]</span>
                <span class="tc-name">{{ block.name || 'tool' }}</span>
                <span class="tc-info">{{ block.info }}</span>
                <div v-if="block.result_preview && block.name === 'delegate_task'" class="tc-subagent-result">
                  <div class="tc-subagent-header" @click="block._expanded = !block._expanded">
                    <span>SubAgent Output</span>
                    <span class="tc-expand-icon">{{ block._expanded ? '[-]' : '[+]' }}</span>
                  </div>
                  <div v-if="block._expanded" class="tc-subagent-body" v-html="renderMd(block.result_preview)"></div>
                </div>
                <div v-else-if="block.result_preview" class="tc-result-preview">{{ block.result_preview }}</div>
              </div>

              <!-- Content block -->
              <div v-if="block.type === 'content' && block.content" v-html="renderMd(block.content)"></div>
            </template>
          </template>
        </div>

        <!-- Typing indicator -->
        <div v-if="loading && messages.length > 0 && (!messages[messages.length-1]?.blocks || messages[messages.length-1]?.blocks?.length === 0)" class="chat-msg assistant" style="opacity: .6;">
          <span class="typing-indicator">Thinking<span class="dots"><span>.</span><span>.</span><span>.</span></span></span>
        </div>
      </div>

      <!-- Action buttons: stop / retry (between messages and input) -->
      <div v-if="loading || lastAssistantIdx >= 0" class="chat-action-bar">
        <button v-if="loading" class="btn btn-danger btn-sm" @click="stopStream">Stop</button>
        <button v-if="!loading && lastAssistantIdx >= 0" class="btn btn-secondary btn-sm" @click="retryLast">Retry</button>
      </div>

      <!-- Tool confirm / ask_user / password overlay -->
      <div v-if="pendingAction" class="pending-action-bar">
        <div v-if="pendingAction.type === 'tool_confirm'" class="action-content">
          <span>Tool <b>{{ pendingAction.data.tool_name }}</b> requires confirmation</span>
          <div v-if="pendingAction.data.tool_args" class="tool-args-preview">{{ pendingAction.data.tool_args }}</div>
          <div class="action-btns">
            <button class="btn btn-primary btn-sm" @click="respondAction('y')">Yes (y)</button>
            <button class="btn btn-secondary btn-sm" @click="respondAction('all')">All</button>
            <button class="btn btn-danger btn-sm" @click="respondAction('n')">No (n)</button>
          </div>
        </div>
        <div v-else-if="pendingAction.type === 'ask_user'" class="action-content">
          <span>{{ pendingAction.data.question || 'AI needs your input:' }}</span>
          <!-- Options as clickable buttons -->
          <div v-if="pendingAction.data.options && pendingAction.data.options.length" class="ask-user-options">
            <button
              v-for="(opt, oi) in pendingAction.data.options"
              :key="oi"
              class="btn btn-secondary btn-sm ask-option-btn"
              @click="selectAskOption(opt)"
            >{{ oi + 1 }}. {{ opt }}</button>
          </div>
          <!-- Always show text input for custom answer -->
          <div class="action-input-row">
            <input v-model="userInput" placeholder="Or type custom answer..." @keydown.enter="submitUserInput" />
            <button class="btn btn-primary btn-sm" @click="submitUserInput">Submit</button>
          </div>
        </div>
        <div v-else-if="pendingAction.type === 'password'" class="action-content">
          <span>{{ pendingAction.data.prompt || 'Password required:' }}</span>
          <div class="action-input-row">
            <input v-model="userInput" type="password" placeholder="Enter password..." @keydown.enter="submitUserInput" />
            <button class="btn btn-primary btn-sm" @click="submitUserInput">Submit</button>
          </div>
        </div>
      </div>

      <!-- File attachment preview -->
      <div v-if="attachedFiles.length > 0" class="attached-files-bar">
        <div v-for="(f, fi) in attachedFiles" :key="fi" class="attachment-chip removable">
          <span v-if="f.is_image" class="att-icon">[img]</span>
          <span v-else class="att-icon">[file]</span>
          {{ f.filename }}
          <span class="att-remove" @click="removeAttachment(fi)">x</span>
        </div>
      </div>

      <div class="chat-input-area">
        <input type="file" ref="fileInputEl" @change="handleFileSelect" multiple style="display:none" accept="*/*" />
        <button class="btn btn-secondary btn-sm upload-btn" @click="triggerFileUpload" title="Upload file or image">
          +
        </button>
        <textarea
          v-model="input"
          placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
          @keydown="onKeydown"
          rows="1"
        ></textarea>
        <button class="btn btn-primary" @click="send" :disabled="loading">Send</button>
      </div>
    </div>
  </div>
</template>
