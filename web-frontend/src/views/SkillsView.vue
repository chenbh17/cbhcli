<script setup>
import { ref, onMounted, computed } from 'vue'
import api from '../api.js'

const agents = ref([])
const selectedAgent = ref('')
const skills = ref([])
const activeNames = ref([])
const loading = ref(false)

onMounted(async () => {
  try {
    const data = await api.getAgents()
    agents.value = data.agents || []
    selectedAgent.value = data.active_agent || (agents.value[0]?.name || '')
    if (selectedAgent.value) await loadSkills()
  } catch (e) { console.error(e) }
})

async function loadSkills() {
  if (!selectedAgent.value) return
  loading.value = true
  try {
    const data = await api.getSkills(selectedAgent.value)
    skills.value = data.skills || []
    activeNames.value = data.active || []
  } catch (e) { console.error(e) }
  loading.value = false
}

async function onAgentChange() {
  await loadSkills()
}

async function toggleSkill(skill) {
  try {
    if (skill.active) {
      await api.deactivateSkill(selectedAgent.value, skill.name)
    } else {
      await api.activateSkills(selectedAgent.value, [skill.name])
    }
    await loadSkills()
  } catch (e) { alert(e.message) }
}

async function removeSkill(name) {
  if (!confirm(`Delete skill "${name}"? This will remove all files.`)) return
  try {
    await api.deleteSkill(selectedAgent.value, name)
    await loadSkills()
  } catch (e) { alert(e.message) }
}
</script>

<template>
  <div class="main-header">
    <h2>Skills</h2>
    <select v-model="selectedAgent" @change="onAgentChange" style="min-width: 130px;">
      <option value="" disabled>Select Agent</option>
      <option v-for="a in agents" :key="a.name" :value="a.name">{{ a.name }}</option>
    </select>
  </div>
  <div class="main-content">
    <div v-if="!selectedAgent" class="empty-state">
      <div class="icon">...</div>
      <div>Select an Agent to view skills</div>
    </div>

    <div v-else-if="skills.length === 0 && !loading" class="empty-state">
      <div class="icon">...</div>
      <div>No skills found for this Agent</div>
      <div class="text-sm text-dim mt-2">Skills are stored in the agent's workspace/skills/ directory</div>
    </div>

    <div class="card" v-for="s in skills" :key="s.name">
      <div class="card-header">
        <div>
          <h3>
            {{ s.name }}
            <span class="badge" :class="s.active ? 'badge-active' : 'badge-inactive'">
              {{ s.active ? 'Active' : 'Inactive' }}
            </span>
          </h3>
          <div class="text-sm text-dim">
            {{ s.has_scripts ? `${s.scripts.length} script(s)` : 'No scripts' }}
          </div>
        </div>
        <div class="flex items-center" style="gap: 8px;">
          <div class="toggle-switch" :class="{ active: s.active }" @click="toggleSkill(s)"></div>
          <button class="btn btn-danger btn-sm" @click="removeSkill(s.name)">Delete</button>
        </div>
      </div>
      <div v-if="s.prompt_preview" class="text-sm text-dim" style="white-space: pre-wrap; max-height: 100px; overflow: hidden;">{{ s.prompt_preview }}</div>
      <div v-if="s.scripts && s.scripts.length" class="mt-2">
        <div class="text-sm text-dim">Scripts:</div>
        <div v-for="sc in s.scripts" :key="sc" class="text-sm" style="margin-left: 12px;">- {{ sc }}</div>
      </div>
    </div>
  </div>
</template>
