/* ===================================================================
   CBHCLI Web — 前端应用（原生 JS SPA，无构建依赖）
   =================================================================== */

"use strict";

/* ===================================================================
   1. API 层
   =================================================================== */

const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.message || JSON.stringify(err));
  }
  return res.json();
}

const api = {
  // 系统
  info: () => request("/info"),
  getSettings: () => request("/settings"),
  updateSettings: (d) => request("/settings", { method: "PUT", body: JSON.stringify(d) }),

  // 模型
  getModels: () => request("/models"),
  addModel: (d) => request("/models", { method: "POST", body: JSON.stringify(d) }),
  updateModel: (n, d) => request(`/models/${enc(n)}`, { method: "PUT", body: JSON.stringify(d) }),
  deleteModel: (n) => request(`/models/${enc(n)}`, { method: "DELETE" }),
  selectModel: (n) => request(`/models/${enc(n)}/select`, { method: "POST" }),
  setEmbedding: (d) => request("/models/embedding", { method: "PUT", body: JSON.stringify(d) }),
  delEmbedding: () => request("/models/embedding", { method: "DELETE" }),
  setRerank: (d) => request("/models/rerank", { method: "PUT", body: JSON.stringify(d) }),
  delRerank: () => request("/models/rerank", { method: "DELETE" }),

  // 备用模型
  getFallback: () => request("/fallback"),
  addFallback: (d) => request("/fallback", { method: "POST", body: JSON.stringify(d) }),
  removeFallback: (cat, n) => request(`/fallback/${cat}/${enc(n)}`, { method: "DELETE" }),
  clearFallback: (cat) => request(`/fallback/${cat}`, { method: "DELETE" }),
  reorderFallback: (cat, order) =>
    request(`/fallback/${cat}/reorder`, { method: "PUT", body: JSON.stringify({ order }) }),

  // Agent
  getAgents: () => request("/agents"),
  createAgent: (d) => request("/agents", { method: "POST", body: JSON.stringify(d) }),
  getAgent: (n) => request(`/agents/${enc(n)}`),
  updateAgent: (n, d) => request(`/agents/${enc(n)}`, { method: "PUT", body: JSON.stringify(d) }),
  deleteAgent: (n) => request(`/agents/${enc(n)}`, { method: "DELETE" }),
  selectAgent: (n) => request(`/agents/${enc(n)}/select`, { method: "POST" }),
  updateAgentFile: (n, f, content) =>
    request(`/agents/${enc(n)}/files/${f}`, { method: "PUT", body: JSON.stringify({ content }) }),

  // 技能
  getSkills: (a) => request(`/agents/${enc(a)}/skills`),
  activateSkills: (a, names) =>
    request(`/agents/${enc(a)}/skills/activate`, { method: "POST", body: JSON.stringify({ names }) }),
  deactivateSkill: (a, n) =>
    request(`/agents/${enc(a)}/skills/${enc(n)}/deactivate`, { method: "POST" }),
  deleteSkill: (a, n) => request(`/agents/${enc(a)}/skills/${enc(n)}`, { method: "DELETE" }),

  // MCP
  getMCP: (a) => request(`/agents/${enc(a)}/mcp`),
  addMCP: (a, d) => request(`/agents/${enc(a)}/mcp`, { method: "POST", body: JSON.stringify(d) }),
  removeMCP: (a, n) => request(`/agents/${enc(a)}/mcp/${enc(n)}`, { method: "DELETE" }),
  refreshMCP: (a, n) => request(`/agents/${enc(a)}/mcp/${enc(n)}/refresh`, { method: "POST" }),
  getMCPTools: (a, n) => request(`/agents/${enc(a)}/mcp/${enc(n)}/tools`),
  toggleMCPTool: (a, s, t, enable) =>
    request(`/agents/${enc(a)}/mcp/${enc(s)}/tools/${enc(t)}`, {
      method: "PUT", body: JSON.stringify({ enable }),
    }),

  // 工具
  getTools: (a) => request(`/agents/${enc(a)}/tools`),
  toggleTool: (a, t, enable) =>
    request(`/agents/${enc(a)}/tools/${enc(t)}`, { method: "PUT", body: JSON.stringify({ enable }) }),

  // 知识库
  getKnowledge: (a) => request(`/agents/${enc(a)}/knowledge`),
  addKnowledge: (a, file_path) =>
    request(`/agents/${enc(a)}/knowledge`, { method: "POST", body: JSON.stringify({ file_path }) }),
  uploadKnowledge: (a, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(`${BASE}/agents/${enc(a)}/knowledge/upload`, { method: "POST", body: fd })
      .then(handleUploadResp);
  },
  removeKnowledge: (a, n) =>
    request(`/agents/${enc(a)}/knowledge/${enc(n)}`, { method: "DELETE" }),
  reindexKnowledge: (a) => request(`/agents/${enc(a)}/knowledge/reindex`, { method: "POST" }),

  // 向量索引
  embeddingStatus: (a) => request(`/agents/${enc(a)}/embedding/status`),
  embeddingIndex: (a) => request(`/agents/${enc(a)}/embedding/index`, { method: "POST" }),
  embeddingClear: (a) => request(`/agents/${enc(a)}/embedding/clear`, { method: "POST" }),
  embeddingReindex: (a) => request(`/agents/${enc(a)}/embedding/reindex`, { method: "POST" }),

  // 历史
  getHistory: (a, limit = 50) => request(`/agents/${enc(a)}/history?limit=${limit}`),
  getHistoryDetail: (a, f) => request(`/agents/${enc(a)}/history/${enc(f)}`),
  deleteHistory: (a, f) => request(`/agents/${enc(a)}/history/${enc(f)}`, { method: "DELETE" }),

  // 对话
  chatStream: (message, agent_name, model_name, images) =>
    fetch(`${BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, agent_name, model_name, images: images || [] }),
    }),
  chatRespond: (agent_name, model_name, response) =>
    request("/chat/respond", { method: "POST", body: JSON.stringify({ agent_name, model_name, response }) }),
  chatReset: (agent_name, model_name) =>
    request("/chat/reset", { method: "POST", body: JSON.stringify({ agent_name, model_name }) }),
  chatAbort: (agent_name, model_name) =>
    request("/chat/abort", { method: "POST", body: JSON.stringify({ agent_name, model_name }) }),
  chatStatus: (a, m) => request(`/chat/status?agent_name=${enc(a)}&model_name=${enc(m)}`),
  chatMessages: (a, m) => request(`/chat/messages?agent_name=${enc(a)}&model_name=${enc(m)}`),
  chatLoad: (agent_name, model_name, filename) =>
    request("/chat/load", { method: "POST", body: JSON.stringify({ agent_name, model_name, filename }) }),
  chatCompress: (agent_name, model_name) =>
    request("/chat/compress", { method: "POST", body: JSON.stringify({ agent_name, model_name }) }),
  chatUpload: (file, a, m) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("agent_name", a);
    fd.append("model_name", m);
    return fetch(`${BASE}/chat/upload`, { method: "POST", body: fd }).then(handleUploadResp);
  },
};

function enc(s) { return encodeURIComponent(s); }

async function handleUploadResp(res) {
  if (!res.ok) {
    const e = await res.json().catch(() => ({ detail: "上传失败" }));
    throw new Error(e.detail || "上传失败");
  }
  return res.json();
}

/* ===================================================================
   2. 工具函数
   =================================================================== */

function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return [...root.querySelectorAll(sel)]; }

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtNum(n) {
  if (n === null || n === undefined) return "-";
  return Number(n).toLocaleString("en-US");
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

function toast(msg, type = "info", duration = 3000) {
  const root = $("#toast-root");
  const t = el("div", { class: `toast ${type}` }, msg);
  root.append(t);
  setTimeout(() => {
    t.classList.add("fade");
    setTimeout(() => t.remove(), 350);
  }, duration);
}

/* ---- Markdown 渲染 ---- */
if (window.marked) {
  marked.setOptions({ breaks: true, gfm: true });
}

function sanitizeHtml(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("script,iframe,object,embed,form,link,meta").forEach(n => n.remove());
  doc.querySelectorAll("*").forEach(node => {
    for (const attr of [...node.attributes]) {
      const name = attr.name.toLowerCase();
      const val = String(attr.value).trim().toLowerCase();
      if (name.startsWith("on")) node.removeAttribute(attr.name);
      else if ((name === "href" || name === "src") && val.startsWith("javascript:"))
        node.removeAttribute(attr.name);
    }
  });
  return doc.body.innerHTML;
}

function renderMd(text) {
  if (!text) return "";
  if (!window.marked) return escapeHtml(text);
  try {
    return sanitizeHtml(marked.parse(text));
  } catch {
    return escapeHtml(text);
  }
}

/** Markdown → 纯文本（用于卡片预览，去除格式符号） */
function plainText(md, maxLen = 120) {
  if (!md) return "";
  let t = String(md)
    .replace(/```[\s\S]*?```/g, " [代码] ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "[图片]")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/~~(.*?)~~/g, "$1")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/^>\s?/gm, "")
    .replace(/\|/g, " ")
    .replace(/\n{2,}/g, " ")
    .replace(/\n/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
  if (maxLen && t.length > maxLen) t = t.slice(0, maxLen) + "…";
  return t;
}

/* ===================================================================
   轻量语法高亮器（monokai 配色，离线零依赖）
   =================================================================== */

const HL_KEYWORDS = {
  python: ("and as assert async await break class continue def del elif else except " +
    "finally for from global if import in is lambda nonlocal not or pass raise return " +
    "try while with yield True False None self cls").split(" "),
  javascript: ("const let var function return if else for while do break continue switch " +
    "case default try catch finally throw new delete typeof instanceof in of class extends " +
    "super this null undefined true false async await yield import export from static get set").split(" "),
  bash: ("if then else elif fi for while until do done case esac function in select echo " +
    "cd ls pwd mkdir rm cp mv cat grep sed awk find chmod chown sudo apt pip pip3 npm node " +
    "python python3 git curl wget tar source export local declare read exit return kill").split(" "),
  sql: ("SELECT FROM WHERE INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE ALTER DROP " +
    "INDEX JOIN LEFT RIGHT INNER OUTER ON GROUP BY ORDER HAVING LIMIT OFFSET AS AND OR NOT " +
    "NULL IN EXISTS BETWEEN LIKE UNION ALL DISTINCT CASE WHEN THEN ELSE END").split(" "),
};
HL_KEYWORDS.python.push("print", "len", "range", "str", "int", "float", "list", "dict",
  "set", "tuple", "type", "isinstance", "enumerate", "zip", "map", "filter", "open", "super");
HL_KEYWORDS.javascript.push("console", "document", "window", "JSON", "Math", "Object",
  "Array", "String", "Number", "Promise", "fetch", "require", "module", "process");

const HL_BUILTINS = {
  python: new Set(["print", "len", "range", "str", "int", "float", "list", "dict", "set",
    "tuple", "type", "isinstance", "enumerate", "zip", "map", "filter", "open", "super"]),
  javascript: new Set(["console", "document", "window", "JSON", "Math", "Object", "Array",
    "String", "Number", "Promise", "fetch", "require", "module", "process"]),
};

/* 扩展名 → 语言 */
const EXT_LANG = {
  py: "python", pyw: "python",
  js: "javascript", mjs: "javascript", jsx: "javascript", ts: "javascript", tsx: "javascript",
  sh: "bash", bash: "bash", zsh: "bash",
  json: "json", sql: "sql", yaml: "yaml", yml: "yaml",
};

function guessLang(filePath) {
  if (!filePath) return null;
  const ext = String(filePath).split(".").pop().toLowerCase();
  return EXT_LANG[ext] || null;
}

/* 主 token 正则：注释 | 字符串 | 数字 | 变量 | 标识符 | 其他 */
const HL_MASTER = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/|#[^\n]*|--[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?|`(?:\\.|[^`\\])*`?)|(\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\$[A-Za-z_][\w$]*|\$\{[^}]*\})|([A-Za-z_][\w$]*)|(\s+|.)/g;

function highlightCode(code, lang) {
  if (!code) return "";
  lang = (lang || "").toLowerCase();
  if (lang === "py") lang = "python";
  if (["js", "ts", "jsx", "tsx", "node"].includes(lang)) lang = "javascript";
  if (["sh", "shell", "zsh"].includes(lang)) lang = "bash";
  if (lang === "shellsession" || lang === "console") lang = "bash";

  const kwSet = new Set(HL_KEYWORDS[lang] || []);
  const biSet = HL_BUILTINS[lang] || new Set();
  const isJson = lang === "json";
  const isYaml = lang === "yaml";
  const src = String(code);
  let html = "";
  let last = 0;

  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const span = (cls, s) => `<span class="${cls}">${esc(s)}</span>`;

  HL_MASTER.lastIndex = 0;
  let m;
  while ((m = HL_MASTER.exec(src)) !== null) {
    const [tok, com, str, num, variable, word] = m;
    if (com !== undefined) html += span("tok-com", tok);
    else if (str !== undefined) {
      // JSON 键："key": → 属性色
      if (isJson) {
        const rest = src.slice(HL_MASTER.lastIndex).match(/^\s*:/);
        html += span(rest ? "tok-attr" : "tok-str", tok);
      } else {
        html += span("tok-str", tok);
      }
    }
    else if (num !== undefined) html += span("tok-num", tok);
    else if (variable !== undefined) html += span("tok-var", tok);
    else if (word !== undefined) {
      if (isJson && /^(true|false|null)$/.test(tok)) html += span("tok-kw", tok);
      else if (isYaml) {
        const rest = src.slice(HL_MASTER.lastIndex).match(/^\s*:/);
        html += span(rest ? "tok-attr" : "tok-op", tok);
      }
      else if (kwSet.has(tok) || (lang === "sql" && kwSet.has(tok.toUpperCase())))
        html += span(biSet.has(tok) ? "tok-bi" : "tok-kw", tok);
      else if (biSet.has(tok)) html += span("tok-bi", tok);
      else if (src[HL_MASTER.lastIndex] === "(") html += span("tok-func", tok);
      else html += esc(tok);
    }
    else html += esc(tok);
    last = HL_MASTER.lastIndex;
    if (m[0] === "") HL_MASTER.lastIndex++;  // 防零宽死循环
  }
  html += esc(src.slice(last));
  return html;
}

/* 代码块构造（工具卡片用） */
function codeBlockEl(code, lang, labelText) {
  const wrap = el("div", { class: "tool-code" });
  if (labelText) wrap.append(el("div", { class: "tool-code-label" }, labelText));
  const pre = el("pre");
  pre.innerHTML = highlightCode(code || "", lang);
  wrap.append(pre);
  return wrap;
}

/* ANSI 转义序列清除（终端输出可能携带颜色码） */
function stripAnsi(s) {
  return String(s ?? "").replace(
    /\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|\([0-9A-B]|[=>#][0-9]?)/g, "");
}

/* 按工具类型渲染参数到容器（代码高亮 + diff，与 CLI 预览对齐） */
function renderToolArgs(container, name, args) {
  args = args || {};
  switch (name) {
    case "python":
      container.append(codeBlockEl(args.code || "", "python", "🐍 Python"));
      break;
    case "terminal":
      container.append(codeBlockEl(args.command || "", "bash", "$ 终端命令"));
      break;
    case "write": {
      const fp = args.file_path || "";
      container.append(codeBlockEl(args.content || "", guessLang(fp), `📝 ${fp}`));
      break;
    }
    case "edit": {
      const fp = args.file_path || "";
      container.append(diffBlockEl(args.old_str || "", args.new_str || "", guessLang(fp), `✏️ ${fp}`));
      break;
    }
    case "read": {
      const fp = args.file_path || "";
      let info = `📄 ${fp}`;
      if (args.start_line || args.end_line)
        info += `  (第 ${args.start_line || 1} - ${args.end_line || "末尾"} 行)`;
      container.append(el("div", { class: "tool-code" },
        el("div", { class: "tool-code-label" }, info)));
      break;
    }
    case "grep":
      container.append(codeBlockEl(`/${args.pattern || ""}/  in  ${args.path || "."}`, null, "🔍 正则搜索"));
      break;
    case "Todo":
      // Todo 不在卡片内展示参数，由专用面板直接呈现任务事项
      break;
    default:
      container.append(
        el("div", { class: "tool-section-label" }, "参数"),
        el("pre", { class: "tool-args" }, typeof args === "string" ? args : JSON.stringify(args, null, 2)));
  }
}

/* Todo 参数防御性解析（模型可能传 JSON 字符串/嵌套对象/非数组） */
function normalizeTodos(args) {
  let t = args && args.todos !== undefined ? args.todos : args;
  if (typeof t === "string") {
    try { t = JSON.parse(t); } catch { t = []; }
  }
  if (t && !Array.isArray(t) && Array.isArray(t.todos)) t = t.todos;
  if (!Array.isArray(t)) return [];
  return t
    .map(item => {
      if (typeof item === "string") return { content: item, status: "pending" };
      if (item && typeof item === "object")
        return { content: String(item.content ?? item.task ?? item.title ?? ""), status: item.status || "pending" };
      return null;
    })
    .filter(x => x && x.content);
}

/* Todo 任务面板（直接展示任务事项，无需展开） */
function todoPanelEl(todos) {
  const panel = el("div", { class: "todo-panel" });
  const done = todos.filter(t => t.status === "completed").length;
  panel.append(el("div", { class: "todo-panel-header" },
    el("span", null, "📋 任务计划"),
    el("span", { class: "todo-panel-count" }, `${done}/${todos.length}`)));
  for (const t of todos) {
    const cls = t.status === "completed" ? "done" : t.status === "in_progress" ? "doing" : "";
    const mark = t.status === "completed" ? "✓" : t.status === "in_progress" ? "◐" : "○";
    panel.append(el("div", { class: `todo-item ${cls}` },
      el("span", { class: "mark" }, mark),
      el("span", { class: "todo-text" }, t.content)));
  }
  return panel;
}

/* diff 块（edit 工具）：行内字符级对比，仅变更部分着色 */
function diffBlockEl(oldStr, newStr, lang, labelText) {
  const wrap = el("div");
  if (labelText) wrap.append(el("div", { class: "tool-code-label" }, labelText));
  const block = el("div", { class: "diff-block" });
  const oldLines = String(oldStr).split("\n");
  const newLines = String(newStr).split("\n");

  const escHl = (s) => highlightCode(s, lang);

  // 渲染一行：segs 为 [{text, hl}]，hl=true 的段加变更底色
  const renderRow = (sign, segs, cls) => {
    const row = el("div", { class: `diff-line ${cls}` });
    row.append(el("span", { class: "diff-sign" }, sign));
    for (const seg of segs) {
      if (!seg.text) continue;
      const span = el("span", seg.hl ? { class: cls === "del" ? "diff-hl-del" : "diff-hl-add" } : {});
      span.innerHTML = escHl(seg.text);
      row.append(span);
    }
    if (!segs.some(s => s.text)) row.insertAdjacentHTML("beforeend", "&nbsp;");
    return row;
  };

  // 行内对比：找公共前缀/后缀字符，仅中段差异着色
  const inlineDiff = (a, b) => {
    let pre = 0;
    const maxPre = Math.min(a.length, b.length);
    while (pre < maxPre && a[pre] === b[pre]) pre++;
    let sufA = a.length, sufB = b.length;
    while (sufA > pre && sufB > pre && a[sufA - 1] === b[sufB - 1]) { sufA--; sufB--; }
    return {
      aSegs: [
        { text: a.slice(0, pre), hl: false },
        { text: a.slice(pre, sufA), hl: true },
        { text: a.slice(sufA), hl: false },
      ],
      bSegs: [
        { text: b.slice(0, pre), hl: false },
        { text: b.slice(pre, sufB), hl: true },
        { text: b.slice(sufB), hl: false },
      ],
    };
  };

  // 1) 去掉完全相同的公共前缀行 / 后缀行（渲染为无底色上下文行）
  let preLines = 0;
  const maxPreLines = Math.min(oldLines.length, newLines.length);
  while (preLines < maxPreLines && oldLines[preLines] === newLines[preLines]) preLines++;
  let sufLines = 0;
  while (sufLines < maxPreLines - preLines &&
         oldLines[oldLines.length - 1 - sufLines] === newLines[newLines.length - 1 - sufLines]) sufLines++;

  // 公共前缀行（上下文，无着色）
  for (let i = 0; i < preLines; i++) {
    block.append(renderRow(" ", [{ text: oldLines[i], hl: false }], "ctx"));
  }

  const oldMid = oldLines.slice(preLines, oldLines.length - sufLines);
  const newMid = newLines.slice(preLines, newLines.length - sufLines);

  if (oldMid.length === newMid.length) {
    // 行数一致：逐行行内对比，仅差异段着色
    for (let i = 0; i < oldMid.length; i++) {
      const { aSegs, bSegs } = inlineDiff(oldMid[i], newMid[i]);
      block.append(renderRow("-", aSegs, "del"));
      block.append(renderRow("+", bSegs, "add"));
    }
  } else {
    // 行数不一致：整行着色（回退）
    for (const line of oldMid) block.append(renderRow("-", [{ text: line, hl: true }], "del"));
    for (const line of newMid) block.append(renderRow("+", [{ text: line, hl: true }], "add"));
  }

  // 公共后缀行（上下文，无着色）
  for (let i = oldLines.length - sufLines; i < oldLines.length; i++) {
    block.append(renderRow(" ", [{ text: oldLines[i], hl: false }], "ctx"));
  }

  wrap.append(block);
  return wrap;
}

/* 复制文本到剪贴板（兼容非安全上下文：远程 HTTP 访问时
   navigator.clipboard 不可用，回退 execCommand） */
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none;";
    document.body.append(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { /* ignore */ }
    ta.remove();
    ok ? resolve() : reject(new Error("复制失败"));
  });
}

/* 为渲染后的代码块加复制按钮 + 语法高亮 */
function enhanceCodeBlocks(container) {
  container.querySelectorAll("pre").forEach(pre => {
    const code = pre.querySelector("code");
    if (code && !code.dataset.hlDone) {
      code.dataset.hlDone = "1";
      const langMatch = (code.className || "").match(/language-([\w-]+)/);
      if (langMatch) {
        code.innerHTML = highlightCode(code.textContent, langMatch[1]);
      }
    }
    if (pre.querySelector(".code-copy-btn")) return;
    const btn = el("button", { class: "code-copy-btn" }, "复制");
    btn.addEventListener("click", () => {
      const codeText = pre.querySelector("code")?.innerText || pre.innerText;
      copyText(codeText).then(() => {
        btn.textContent = "已复制";
        setTimeout(() => (btn.textContent = "复制"), 1200);
      }).catch(() => {
        btn.textContent = "复制失败";
        setTimeout(() => (btn.textContent = "复制"), 1200);
      });
    });
    pre.append(btn);
  });
}

/* ---- 模态框 ---- */
function openModal({ title, body, footer, width }) {
  const root = $("#modal-root");
  const close = () => { mask.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };

  const modal = el("div", { class: "modal" });
  if (width) modal.style.width = width;
  modal.append(
    el("div", { class: "modal-header" },
      el("div", { class: "modal-title" }, title),
      el("button", { class: "modal-close", onclick: close }, "✕")),
    el("div", { class: "modal-body" }, body),
  );
  if (footer) {
    const footChildren = Array.isArray(footer) ? footer : [footer];
    modal.append(el("div", { class: "modal-footer" }, ...footChildren));
  }

  const mask = el("div", { class: "modal-mask" }, modal);
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
  root.append(mask);
  document.addEventListener("keydown", onKey);
  return { close, modal };
}

function confirmDialog(title, message, { danger = false, okText = "确认" } = {}) {
  return new Promise((resolve) => {
    const { close } = openModal({
      title,
      body: el("p", { style: "color:var(--text-1);font-size:13.5px;white-space:pre-wrap;" }, message),
      footer: [
        el("button", { class: "btn", onclick: () => { close(); resolve(false); } }, "取消"),
        el("button", {
          class: danger ? "btn btn-danger" : "btn btn-primary",
          onclick: () => { close(); resolve(true); },
        }, okText),
      ],
    });
  });
}

function promptDialog(title, fields, okText = "确定") {
  /** fields: [{key,label,value,placeholder,type:'text'|'textarea'|'password',hint}] */
  return new Promise((resolve) => {
    const inputs = {};
    const body = el("div");
    for (const f of fields) {
      let input;
      if (f.type === "textarea") {
        input = el("textarea", { class: "input", rows: f.rows || 4, placeholder: f.placeholder || "" });
        input.value = f.value || "";
      } else {
        input = el("input", {
          class: "input", type: f.type || "text",
          placeholder: f.placeholder || "", value: f.value ?? "",
        });
      }
      inputs[f.key] = input;
      body.append(el("div", { class: "form-row" },
        el("label", null, f.label),
        input,
        f.hint ? el("div", { class: "form-hint" }, f.hint) : null,
      ));
    }
    const { close } = openModal({
      title,
      body,
      footer: [
        el("button", { class: "btn", onclick: () => { close(); resolve(null); } }, "取消"),
        el("button", {
          class: "btn btn-primary",
          onclick: () => {
            const out = {};
            for (const [k, inp] of Object.entries(inputs)) out[k] = inp.value;
            close(); resolve(out);
          },
        }, okText),
      ],
    });
  });
}

/* ===================================================================
   3. 全局状态
   =================================================================== */

const state = {
  agents: [],
  models: [],
  activeAgent: "",
  selectedModel: "",
  streaming: false,
  attachments: [],
  currentView: "chat",
  statusTimer: null,
};

function currentAgent() { return state.activeAgent; }
function currentModel() { return state.selectedModel; }

/* ===================================================================
   4. 路由
   =================================================================== */

const VIEW_LOADERS = {
  chat: null,
  agents: loadAgentsView,
  models: loadModelsView,
  fallback: loadFallbackView,
  skills: loadSkillsView,
  mcp: loadMCPView,
  knowledge: loadKnowledgeView,
  tools: loadToolsView,
  embedding: loadEmbeddingView,
  history: loadHistoryView,
  settings: loadSettingsView,
};

function switchView(name) {
  if (!VIEW_LOADERS.hasOwnProperty(name)) name = "chat";
  state.currentView = name;
  $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === name));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
  if (location.hash !== `#/${name}`) location.hash = `#/${name}`;
  const loader = VIEW_LOADERS[name];
  if (loader) loader().catch(e => toast(e.message, "error"));
}

function initRouter() {
  $$(".nav-item").forEach(n =>
    n.addEventListener("click", () => switchView(n.dataset.view)));
  window.addEventListener("hashchange", () => {
    const name = location.hash.replace(/^#\//, "") || "chat";
    if (name !== state.currentView) switchView(name);
  });
  const initial = location.hash.replace(/^#\//, "") || "chat";
  switchView(initial);
}

/* ===================================================================
   5. 对话视图
   =================================================================== */

const TOOL_ICONS = {
  terminal: "💻", read: "📄", write: "📝", edit: "✏️", grep: "🔍", glob: "📁",
  python: "🐍", Todo: "📋", ask_user: "❓", memory_search: "🧠", knowledge_base: "📚",
  delegate_task: "🤖", skills_create: "⚡", image: "🖼️", process: "📊", kill_process: "⛔",
};
function toolIcon(name) {
  if (TOOL_ICONS[name]) return TOOL_ICONS[name];
  if (name.startsWith("mcp_")) return "🔌";
  if (name.startsWith("cbhpacks_")) return "📈";
  return "🔧";
}

const chatUI = {};

function initChatView() {
  chatUI.agentSelect = $("#chat-agent-select");
  chatUI.modelSelect = $("#chat-model-select");
  chatUI.messages = $("#chat-messages");
  chatUI.empty = $("#chat-empty");
  chatUI.input = $("#chat-input");
  chatUI.sendBtn = $("#btn-send");
  chatUI.abortBtn = $("#btn-abort");
  chatUI.attachBtn = $("#btn-attach");
  chatUI.fileInput = $("#file-input");
  chatUI.attachments = $("#attachments");
  chatUI.ctxFill = $("#ctx-meter-fill");
  chatUI.ctxText = $("#ctx-meter-text");
  chatUI.ctxMeter = $("#ctx-meter");
  chatUI.hint = $("#composer-hint");

  chatUI.agentSelect.addEventListener("change", onAgentChange);
  chatUI.modelSelect.addEventListener("change", onModelChange);
  chatUI.sendBtn.addEventListener("click", sendMessage);
  chatUI.abortBtn.addEventListener("click", abortStream);
  chatUI.attachBtn.addEventListener("click", () => chatUI.fileInput.click());
  chatUI.fileInput.addEventListener("change", handleFiles);
  $("#btn-new-session").addEventListener("click", newSession);
  $("#btn-compress").addEventListener("click", manualCompress);
  $("#btn-quick-tools").addEventListener("click", showQuickTools);
  $("#btn-quick-skills").addEventListener("click", showQuickSkills);
  $("#btn-quick-model").addEventListener("click", showQuickModel);

  chatUI.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.altKey && !e.isComposing) {
      // Alt+Enter 换行
      e.preventDefault();
      const ta = e.target;
      ta.setRangeText("\n", ta.selectionStart, ta.selectionEnd, "end");
      autoGrow();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendMessage();
    }
  });
  chatUI.input.addEventListener("input", autoGrow);

  // 粘贴图片
  chatUI.input.addEventListener("paste", handlePaste);

  state.statusTimer = setInterval(refreshStatus, 5000);
}

function autoGrow() {
  const ta = chatUI.input;
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
}

async function onAgentChange() {
  const name = chatUI.agentSelect.value;
  state.activeAgent = name;
  api.selectAgent(name).catch(() => {});
  clearMessages();
  await restoreMessages();
  refreshStatus();
}

async function onModelChange() {
  const name = chatUI.modelSelect.value;
  state.selectedModel = name;
  api.selectModel(name).catch(() => {});
  clearMessages();
  await restoreMessages();
  refreshStatus();
}

function refreshSelectors() {
  chatUI.agentSelect.innerHTML = "";
  for (const a of state.agents)
    chatUI.agentSelect.append(el("option", { value: a.name }, a.name));
  chatUI.agentSelect.value = state.activeAgent;

  chatUI.modelSelect.innerHTML = "";
  for (const m of state.models)
    chatUI.modelSelect.append(el("option", { value: m.name }, m.name + (m.vision ? " 👁" : "")));
  chatUI.modelSelect.value = state.selectedModel;
}

function clearMessages() {
  $$(".msg-column", chatUI.messages).forEach(n => n.remove());
  chatUI.empty.classList.remove("hidden");
}

function msgColumn() {
  let col = $(".msg-column", chatUI.messages);
  if (!col) {
    col = el("div", { class: "msg-column" });
    chatUI.messages.append(col);
  }
  chatUI.empty.classList.add("hidden");
  return col;
}

function scrollBottom() {
  chatUI.messages.scrollTop = chatUI.messages.scrollHeight;
}

/* ---- 状态栏 ---- */
async function refreshStatus() {
  const a = currentAgent(), m = currentModel();
  if (!a || !m) return;
  try {
    const s = await api.chatStatus(a, m);
    updateCtxMeter(s);
    const cwdBar = $("#cwd-bar");
    if (cwdBar) {
      $("#cwd-bar-text").textContent = s.cwd || "";
      cwdBar.title = s.cwd || "";
    }
  } catch { /* 忽略 */ }
}

function updateCtxMeter(s) {
  const pct = Math.min(100, s.ctx_percentage || 0);
  chatUI.ctxFill.style.width = pct + "%";
  chatUI.ctxFill.className = "ctx-meter-fill" + (pct >= 80 ? " danger" : pct >= 50 ? " warn" : "");
  chatUI.ctxText.textContent = pct.toFixed(1) + "%";
  const tokens = s.token_estimate ? ` · ${fmtNum(s.token_estimate)} tokens` : "";
  chatUI.ctxMeter.title = `上下文使用: ${pct.toFixed(1)}%${tokens} / ${fmtNum(s.model_limit)}`;
}

/* ---- 附件 ---- */
async function handleFiles(e) {
  const files = [...e.target.files];
  e.target.value = "";
  for (const f of files) await uploadAttachment(f);
}

async function handlePaste(e) {
  const items = [...(e.clipboardData?.items || [])];
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) await uploadAttachment(new File([file], `paste_${Date.now()}.png`, { type: file.type }));
    }
  }
}

async function uploadAttachment(file) {
  const a = currentAgent(), m = currentModel();
  if (!a || !m) { toast("请先选择 Agent 和模型", "warn"); return; }
  try {
    const info = await api.chatUpload(file, a, m);
    state.attachments.push(info);
    renderAttachments();
  } catch (e2) { toast(`上传失败: ${e2.message}`, "error"); }
}

function renderAttachments() {
  chatUI.attachments.innerHTML = "";
  state.attachments.forEach((att, i) => {
    const chip = el("div", { class: "attachment-chip" });
    if (att.is_image && att.base64) chip.append(el("img", { src: att.base64, alt: att.filename }));
    else chip.append(el("span", null, "📄"));
    chip.append(el("span", null, `${att.filename} (${fmtSize(att.size)})`));
    const rm = el("span", { class: "attachment-remove", title: "移除" }, "✕");
    rm.addEventListener("click", () => { state.attachments.splice(i, 1); renderAttachments(); });
    chip.append(rm);
    chatUI.attachments.append(chip);
  });
}

/* ---- 发送消息 ---- */
async function sendMessage() {
  const text = chatUI.input.value.trim();
  const a = currentAgent(), m = currentModel();
  if (!a || !m) { toast("请先选择 Agent 和模型", "warn"); return; }
  if (state.streaming) return;
  if (!text && state.attachments.length === 0) return;

  // 组装用户消息
  const fileInfos = [];
  const images = [];
  for (const f of state.attachments) {
    if (f.is_image && f.base64) {
      fileInfos.push(`[图片: ${f.filename}]`);
      images.push(f.base64.replace(/^data:image\/[^;]+;base64,/, ""));
    } else {
      fileInfos.push(`[文件: ${f.filename} (${f.path})]`);
    }
  }
  const userContent = fileInfos.length
    ? fileInfos.join("\n") + (text ? "\n" + text : "")
    : text;

  // 渲染用户气泡
  const col = msgColumn();
  const bubble = el("div", { class: "msg-user" },
    el("div", { class: "msg-user-bubble" },
      state.attachments.some(f => f.is_image)
        ? el("div", { class: "msg-user-images" },
            ...state.attachments.filter(f => f.is_image)
              .map(f => el("span", { class: "img-chip" }, "🖼 " + f.filename)))
        : null,
      userContent));
  col.append(bubble);

  chatUI.input.value = "";
  autoGrow();
  state.attachments = [];
  renderAttachments();
  scrollBottom();

  await runStream(userContent, images);
}

function setStreaming(on) {
  state.streaming = on;
  chatUI.sendBtn.classList.toggle("hidden", on);
  chatUI.abortBtn.classList.toggle("hidden", !on);
  chatUI.input.disabled = false;
  chatUI.hint.textContent = on ? "AI 正在响应… 点击 ■ 可中断" : "";
}

async function abortStream() {
  try {
    await api.chatAbort(currentAgent(), currentModel());
  } catch (e) { toast(e.message, "error"); }
}

/* ---- SSE 流处理 ---- */
async function runStream(userContent, images) {
  setStreaming(true);
  const col = msgColumn();

  // AI 消息容器
  const aiBody = el("div", { class: "msg-ai-body" });
  col.append(el("div", { class: "msg-ai" }, el("div", { class: "msg-ai-avatar" }, "❯"), aiBody));
  scrollBottom();

  // 当前块指针
  let curReasoning = null;   // {content, textEl, blockEl}
  let curContent = null;     // {raw, el}
  let lastToolCard = null;
  const toolCards = new Map();  // tool_id -> card record

  const closeReasoning = () => {
    if (curReasoning) {
      curReasoning.blockEl.querySelector(".thinking-live-dot")?.remove();
      // 思考结束后自动折叠，保持对话整洁（点击标题可重新展开）
      curReasoning.blockEl.classList.remove("open");
      curReasoning = null;
    }
  };
  const closeContent = () => { curContent = null; };

  function ensureReasoning() {
    if (curReasoning) return curReasoning;
    closeContent();
    const textEl = el("div", { class: "thinking-content" });
    const blockEl = el("div", { class: "thinking-block open" },
      el("div", { class: "thinking-header" },
        el("span", { class: "arrow" }, "▶"),
        el("span", null, "思考过程"),
        el("span", { class: "thinking-live-dot" })),
      textEl);
    blockEl.querySelector(".thinking-header").addEventListener("click", () =>
      blockEl.classList.toggle("open"));
    aiBody.append(blockEl);
    curReasoning = { content: "", textEl, blockEl };
    return curReasoning;
  }

  function ensureContent() {
    if (curContent) return curContent;
    closeReasoning();
    const mdEl = el("div", { class: "md-content" });
    aiBody.append(mdEl);
    curContent = { raw: "", el: mdEl };
    return curContent;
  }

  function addSysEvent(text, cls = "", icon = "ℹ️") {
    closeReasoning(); closeContent();
    aiBody.append(el("div", { class: `sys-event ${cls}` },
      el("span", { class: "icon" }, icon), el("span", null, text)));
    scrollBottom();
  }

  function ensureToolCard(toolId, name) {
    if (toolCards.has(toolId)) return toolCards.get(toolId);
    closeReasoning(); closeContent();
    const statusEl = el("span", { class: "tag amber tool-status" }, "等待确认");
    const previewEl = el("span", { class: "tool-preview-text" });
    const bodyEl = el("div", { class: "tool-card-body" });
    const cardEl = el("div", { class: "tool-card open" },
      el("div", { class: "tool-card-header" },
        el("span", { class: "arrow" }, "▶"),
        el("span", { class: "tool-icon" }, toolIcon(name)),
        el("span", { class: "tool-name" }, name),
        previewEl, statusEl),
      bodyEl);
    cardEl.querySelector(".tool-card-header").addEventListener("click", () =>
      cardEl.classList.toggle("open"));
    aiBody.append(cardEl);
    const rec = { toolId, name, cardEl, statusEl, previewEl, bodyEl, confirmEl: null };
    toolCards.set(toolId, rec);
    lastToolCard = rec;
    scrollBottom();
    return rec;
  }

  /* 按工具类型渲染参数（代码高亮 + diff，与 CLI 预览对齐） */
  function setToolArgs(rec, name, args) {
    rec.bodyEl.innerHTML = "";
    renderToolArgs(rec.bodyEl, name, args);
  }

  function setToolStatus(rec, text, cls) {
    rec.statusEl.className = `tag tool-status ${cls}`;
    rec.statusEl.textContent = text;
  }

  function setToolResult(rec, data) {
    const ok = data.success;
    setToolStatus(rec, ok ? "完成" : "失败", ok ? "green" : "red");
    rec.confirmEl?.remove();
    rec.confirmEl = null;

    // python 工具：代码已在参数区展示，结果区只放终端输出
    const pd = data.preview_data;
    if (pd && pd.type === "python") {
      rec.bodyEl.append(
        el("div", { class: "tool-section-label" }, "输出"),
        el("pre", { class: `term-output ${ok ? "" : "fail"}` }, stripAnsi(pd.output || data.preview || "")));
    } else if (data.preview) {
      const simple = ["write", "edit"].includes(rec.name) && ok;
      rec.bodyEl.append(
        el("div", { class: "tool-section-label" }, "结果"),
        el("pre", { class: `term-output ${ok ? "" : "fail"}` },
          stripAnsi(simple ? data.preview.split("\n")[0] : data.preview)));
    }
    if (!ok) rec.cardEl.classList.add("open");
    else rec.cardEl.classList.remove("open");
    scrollBottom();
  }

  // Todo 专用面板（跟随最近一次 Todo 调用位置，始终展示全部事项）
  let todoPanel = null;
  const showTodoPanel = (args) => {
    const todos = normalizeTodos(args);
    if (!todos.length) return;
    const panel = todoPanelEl(todos);
    if (todoPanel && todoPanel.isConnected) todoPanel.replaceWith(panel);
    else aiBody.append(panel);
    todoPanel = panel;
    scrollBottom();
  };

  async function handleConfirm(data) {
    // Todo 工具：直接展示任务面板，不创建工具卡片
    if (data.tool_name === "Todo") {
      showTodoPanel(data.tool_args);
      return;
    }
    const rec = ensureToolCard(data.tool_id, data.tool_name);
    rec.previewEl.textContent = data.preview || "";
    setToolArgs(rec, data.tool_name, data.tool_args);

    if (!data.needs_confirm) {
      setToolStatus(rec, "自动确认", "blue");
      return;
    }

    // 确认条（参数已在上方工具卡片中高亮展示，此处不再重复）
    const confirmEl = el("div", { class: "confirm-card" },
      el("div", { class: "confirm-title" }, `⚠️ 确认执行 ${data.tool_name} ?`),
      el("div", { class: "confirm-actions" },
        el("button", { class: "btn btn-sm btn-success", "data-r": "y" }, "✓ 允许"),
        el("button", { class: "btn btn-sm btn-danger", "data-r": "n" }, "✕ 拒绝"),
        el("button", { class: "btn btn-sm", "data-r": "all" }, "全部允许")));
    rec.confirmEl = confirmEl;
    aiBody.append(confirmEl);
    scrollBottom();

    $$("button", confirmEl).forEach(btn => {
      btn.addEventListener("click", async () => {
        $$("button", confirmEl).forEach(b => (b.disabled = true));
        try {
          await api.chatRespond(currentAgent(), currentModel(), btn.dataset.r);
          setToolStatus(rec, "已确认", "blue");
          confirmEl.remove();
          rec.confirmEl = null;
        } catch (e) {
          toast(e.message, "error");
          $$("button", confirmEl).forEach(b => (b.disabled = false));
        }
      });
    });
  }

  async function handleAskUser(data) {
    closeReasoning(); closeContent();
    const askEl = el("div", { class: "ask-card" });
    askEl.append(el("div", { class: "ask-question" }, "❓ " + (data.question || "")));

    const options = data.options || [];
    const selected = new Set();
    let answered = false;

    const submit = async (answer) => {
      if (answered) return;
      answered = true;
      try {
        await api.chatRespond(currentAgent(), currentModel(), answer);
        askEl.innerHTML = "";
        askEl.append(
          el("div", { class: "ask-question" }, "❓ " + (data.question || "")),
          el("div", { class: "sys-event success" }, el("span", { class: "icon" }, "✓"),
            el("span", null, `已回答: ${answer}`)));
      } catch (e) {
        toast(e.message, "error");
        answered = false;
      }
      scrollBottom();
    };

    if (options.length) {
      const optsEl = el("div", { class: "ask-options" });
      const multi = !!data.allow_multiple;
      for (const opt of options) {
        const b = el("button", { class: "ask-option-btn" }, opt);
        b.addEventListener("click", () => {
          if (multi) {
            if (selected.has(opt)) { selected.delete(opt); b.classList.remove("selected"); }
            else { selected.add(opt); b.classList.add("selected"); }
          } else {
            submit(opt);
          }
        });
        optsEl.append(b);
      }
      askEl.append(optsEl);
      if (multi) {
        const okBtn = el("button", { class: "btn btn-sm btn-primary" }, "确认选择");
        okBtn.addEventListener("click", () => {
          if (selected.size) submit([...selected].join(", "));
        });
        askEl.append(okBtn);
      }
    }

    // 自定义输入
    const inputEl = el("input", { class: "input", placeholder: "自定义回答…" });
    const sendBtn = el("button", { class: "btn btn-sm btn-primary" }, "回答");
    const doCustom = () => { if (inputEl.value.trim()) submit(inputEl.value.trim()); };
    sendBtn.addEventListener("click", doCustom);
    inputEl.addEventListener("keydown", (e) => { if (e.key === "Enter") doCustom(); });
    askEl.append(el("div", { class: "ask-input-row" }, inputEl, sendBtn));

    aiBody.append(askEl);
    scrollBottom();
    inputEl.focus();
  }

  /* ---- 事件分发 ---- */
  const handlers = {
    reasoning(d) {
      const r = ensureReasoning();
      r.content += d.content;
      r.textEl.textContent = r.content;
      scrollBottom();
    },
    content(d) {
      const c = ensureContent();
      c.raw += d.content;
      c.el.innerHTML = renderMd(c.raw);
      enhanceCodeBlocks(c.el);
      scrollBottom();
    },
    tool_confirm: handleConfirm,
    tool_auto_confirmed(d) {
      if (d.tool_name === "Todo") return;
      const rec = ensureToolCard(d.tool_id, d.tool_name);
      setToolStatus(rec, "自动确认", "blue");
    },
    tool_executing(d) {
      if (d.tool_name === "Todo") return;
      const rec = ensureToolCard(d.tool_id, d.tool_name);
      setToolStatus(rec, "执行中…", "blue");
    },
    tool_result(d) {
      if (d.tool_name === "Todo") return;  // 面板已在 confirm 阶段展示
      const rec = ensureToolCard(d.tool_id, d.tool_name);
      setToolResult(rec, d);
    },
    tool_rejected(d) {
      if (d.tool_name === "Todo") return;
      const rec = ensureToolCard(d.tool_id, d.tool_name);
      setToolStatus(rec, "已拒绝", "red");
      rec.confirmEl?.remove();
    },
    ask_user: handleAskUser,
    reflection(d) {
      addSysEvent(`🔁 ${d.tool_name} 执行失败，正在自我反思 (重试 ${d.retry}/${d.max_retries})…`, "warn", "🔁");
    },
    compressing(d) { addSysEvent(d.content, "warn", "📦"); },
    compressed(d) { addSysEvent(d.content, "success", "📦"); },
    compress_failed(d) { addSysEvent(d.content, "warn", "⚠️"); },
    fallback(d) { addSysEvent(d.content, "warn", "🔄"); },
    error(d) { addSysEvent(`错误: ${d.content}`, "error", "❌"); },
    aborted() { addSysEvent("已中断", "warn", "⛔"); },
  };

  let finishUsage = null;
  try {
    const resp = await api.chatStream(userContent, currentAgent(), currentModel(), images);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || "请求失败");
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const rawEvent = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of rawEvent.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let data;
          try { data = JSON.parse(line.slice(6)); } catch { continue; }
          if (data.type === "done") { finishUsage = data.usage; continue; }
          const handler = handlers[data.type];
          if (handler) {
            // 单个事件处理异常不中断整个流（如工具参数格式异常）
            try { await handler(data); }
            catch (e) { console.error(`SSE事件处理异常 [${data.type}]:`, e); }
          }
        }
      }
    }
  } catch (e) {
    addSysEvent(`连接错误: ${e.message}`, "error", "❌");
  } finally {
    closeReasoning();
    setStreaming(false);
    if (finishUsage) updateCtxMeter(finishUsage);
    refreshStatus();
    scrollBottom();
    chatUI.input.focus();
  }
}

/* ---- 恢复会话消息（刷新页面后） ---- */
async function restoreMessages() {
  const a = currentAgent(), m = currentModel();
  if (!a || !m) return;
  try {
    const { messages } = await api.chatMessages(a, m);
    if (!messages || !messages.length) return;
    renderRestoredMessages(messages);
  } catch { /* 忽略 */ }
}

function renderRestoredMessages(messages) {
  const col = msgColumn();
  for (const msg of messages) {
    if (msg.role === "user") {
      col.append(el("div", { class: "msg-user" },
        el("div", { class: "msg-user-bubble" },
          msg.image_count
            ? el("div", { class: "msg-user-images" },
                el("span", { class: "img-chip" }, `🖼 ${msg.image_count} 张图片`))
            : null,
          msg.content || "")));
    } else if (msg.role === "assistant") {
      const body = el("div", { class: "msg-ai-body" });
      if (msg.reasoning) {
        const block = el("div", { class: "thinking-block" },
          el("div", { class: "thinking-header" },
            el("span", { class: "arrow" }, "▶"), el("span", null, "思考过程")),
          el("div", { class: "thinking-content" }, msg.reasoning));
        block.querySelector(".thinking-header").addEventListener("click", () =>
          block.classList.toggle("open"));
        body.append(block);
      }
      if (msg.content) {
        const mdEl = el("div", { class: "md-content", html: renderMd(msg.content) });
        enhanceCodeBlocks(mdEl);
        body.append(mdEl);
      }
      for (const tc of msg.tool_calls || []) {
        // Todo：直接展示任务面板
        if (tc.name === "Todo") {
          const todos = normalizeTodos(tc.arguments);
          if (todos.length) body.append(todoPanelEl(todos));
          continue;
        }
        const statusCls = tc.success === false ? "red" : "green";
        const statusText = tc.success === false ? "失败" : "完成";
        const bodyEl = el("div", { class: "tool-card-body" });
        renderToolArgs(bodyEl, tc.name, tc.arguments);
        if (tc.result) {
          bodyEl.append(
            el("div", { class: "tool-section-label" }, "结果"),
            el("pre", { class: `term-output ${tc.success === false ? "fail" : ""}` }, stripAnsi(tc.result)));
        }
        const card = el("div", { class: "tool-card" },
          el("div", { class: "tool-card-header" },
            el("span", { class: "arrow" }, "▶"),
            el("span", { class: "tool-icon" }, toolIcon(tc.name)),
            el("span", { class: "tool-name" }, tc.name),
            el("span", { class: "tool-preview-text" }),
            el("span", { class: `tag tool-status ${statusCls}` }, statusText)),
          bodyEl);
        card.querySelector(".tool-card-header").addEventListener("click", () =>
          card.classList.toggle("open"));
        body.append(card);
      }
      col.append(el("div", { class: "msg-ai" },
        el("div", { class: "msg-ai-avatar" }, "❯"), body));
    }
  }
  scrollBottom();
}

/* ---- 新会话 / 压缩 ---- */
async function newSession() {
  const ok = await confirmDialog("新建会话", "当前会话将保存到历史记录，确定开始新会话吗？", { okText: "新建" });
  if (!ok) return;
  try {
    await api.chatReset(currentAgent(), currentModel());
    clearMessages();
    refreshStatus();
    toast("已开始新会话", "success");
  } catch (e) { toast(e.message, "error"); }
}

async function manualCompress() {
  try {
    const r = await api.chatCompress(currentAgent(), currentModel());
    toast(r.message, r.compressed ? "success" : "info");
    if (r.usage) updateCtxMeter(r.usage);
  } catch (e) { toast(e.message, "error"); }
}

/* ===================================================================
   聊天页快捷配置弹窗（工具 / 技能 / 模型）
   =================================================================== */

function requireAgent() {
  const a = currentAgent();
  if (!a) { toast("请先选择 Agent", "warn"); return null; }
  return a;
}

/* ---- 工具开关快捷弹窗 ---- */
async function showQuickTools() {
  const agent = requireAgent();
  if (!agent) return;
  let data;
  try { data = await api.getTools(agent); }
  catch (e) { toast(e.message, "error"); return; }

  const body = el("div", { class: "quick-list" });
  const groups = {};
  for (const t of data.tools) (groups[t.category] = groups[t.category] || []).push(t);

  const renderGroup = (cat, tools) => {
    body.append(el("div", { class: "quick-section" },
      cat === "builtin" ? "🔧 内置工具" : "📈 " + cat));
    for (const t of tools) {
      const sw = el("input", { type: "checkbox" });
      sw.checked = t.enabled;
      sw.addEventListener("change", async () => {
        try {
          await api.toggleTool(agent, t.name, sw.checked);
          toast(`工具 '${t.name}' 已${sw.checked ? "启用" : "禁用"}`, "success");
        } catch (e) { toast(e.message, "error"); sw.checked = !sw.checked; }
      });
      body.append(el("div", { class: "quick-item" },
        el("div", { class: "quick-item-info" },
          el("div", { class: "quick-item-name" }, toolIcon(t.name), t.name),
          el("div", { class: "quick-item-desc" }, t.description || "")),
        el("label", { class: "switch" }, sw, el("span", { class: "track" }))));
    }
  };
  for (const [cat, tools] of Object.entries(groups)) renderGroup(cat, tools);

  openModal({
    title: `🔧 工具开关 — ${agent}`,
    width: "min(620px, calc(100vw - 40px))",
    body,
  });
}

/* ---- 技能快捷弹窗 ---- */
async function showQuickSkills() {
  const agent = requireAgent();
  if (!agent) return;

  const body = el("div", { class: "quick-list" });
  const modal = openModal({
    title: `⚡ 技能管理 — ${agent}`,
    width: "min(620px, calc(100vw - 40px))",
    body,
  });

  async function render() {
    body.innerHTML = "";
    let data;
    try { data = await api.getSkills(agent); }
    catch (e) { body.append(el("div", { class: "card-sub" }, "加载失败: " + e.message)); return; }
    if (!data.skills.length) {
      body.append(el("div", { class: "card-sub", style: "padding:16px;text-align:center;" },
        "暂无技能。可让 AI 使用 skills_create 工具自动创建。"));
      return;
    }
    const activeCount = data.active?.length || 0;
    body.append(el("div", { class: "quick-section" }, `已激活 ${activeCount} / ${data.skills.length} 个技能`));
    for (const s of data.skills) {
      const sw = el("input", { type: "checkbox" });
      sw.checked = s.active;
      sw.addEventListener("change", async () => {
        try {
          if (sw.checked) await api.activateSkills(agent, [s.name]);
          else await api.deactivateSkill(agent, s.name);
          toast(`技能 '${s.name}' 已${sw.checked ? "激活" : "取消激活"}`, "success");
        } catch (e) { toast(e.message, "error"); sw.checked = !sw.checked; }
      });
      body.append(el("div", { class: "quick-item" },
        el("div", { class: "quick-item-info" },
          el("div", { class: "quick-item-name" }, "⚡ " + s.name,
            s.has_scripts ? el("span", { class: "tag blue" }, `${s.scripts.length} 脚本`) : null),
          el("div", { class: "quick-item-desc" }, plainText(s.prompt || s.prompt_preview, 90))),
        el("label", { class: "switch" }, sw, el("span", { class: "track" }))));
    }
  }
  await render();
}

/* ---- 模型切换快捷弹窗 ---- */
async function showQuickModel() {
  let data;
  try { data = await api.getModels(); }
  catch (e) { toast(e.message, "error"); return; }

  const body = el("div", { class: "quick-list" });
  body.append(el("div", { class: "quick-section" }, "点击切换聊天模型"));
  for (const m of data.models || []) {
    const isCur = m.name === currentModel();
    const item = el("div", {
      class: "quick-item",
      style: "cursor:pointer;" + (isCur ? "border-color:var(--accent);" : ""),
    },
      el("div", { class: "quick-item-info" },
        el("div", { class: "quick-item-name" }, "🧠 " + m.name,
          isCur ? el("span", { class: "tag green" }, "使用中") : null,
          m.vision ? el("span", { class: "tag purple" }, "视觉") : null),
        el("div", { class: "quick-item-desc" }, `${m.model} · 上下文 ${fmtNum(m.context_limit)}`)),
      el("span", { style: "color:var(--text-dim);font-size:16px;" }, "›"));
    item.addEventListener("click", async () => {
      if (m.name === currentModel()) { $("#modal-root").innerHTML = ""; return; }
      try {
        await api.selectModel(m.name);
        state.selectedModel = m.name;
        refreshSelectors();
        $("#modal-root").innerHTML = "";
        toast(`已切换到模型 '${m.name}'`, "success");
        clearMessages();
        await restoreMessages();
        refreshStatus();
      } catch (e) { toast(e.message, "error"); }
    });
    body.append(item);
  }

  openModal({
    title: "🧠 切换模型",
    width: "min(560px, calc(100vw - 40px))",
    body,
  });
}

/* ===================================================================
   6. 通用：需要选择 Agent 的视图辅助
   =================================================================== */

function agentSelector(current, onChange) {
  const sel = el("select", { class: "select" });
  for (const a of state.agents) sel.append(el("option", { value: a.name }, a.name));
  sel.value = current || state.activeAgent || state.agents[0]?.name || "";
  sel.addEventListener("change", () => onChange(sel.value));
  return sel;
}

function pageShell(title, desc, actions = []) {
  const inner = el("div", { class: "page-inner" });
  inner.append(el("div", { class: "page-header" },
    el("div", null,
      el("div", { class: "page-title" }, title),
      desc ? el("div", { class: "page-desc" }, desc) : null),
    el("div", { class: "page-actions" }, ...actions)));
  return inner;
}

/** ISO 时间戳 → 可读格式 "2026-07-20 10:48" */
function fmtTime(iso) {
  if (!iso) return "";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
  // 兼容 20260720_102016 格式
  const m2 = String(iso).match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/);
  if (m2) return `${m2[1]}-${m2[2]}-${m2[3]} ${m2[4]}:${m2[5]}`;
  return String(iso).slice(0, 16);
}

function emptyState(text, icon = "📭") {
  return el("div", { class: "empty-state" },
    el("div", { class: "icon" }, icon), el("div", null, text));
}

/* ===================================================================
   7. Agent 管理视图
   =================================================================== */

async function loadAgentsView() {
  const root = $("#view-agents");
  root.innerHTML = "";
  const data = await api.getAgents();
  state.agents = data.agents || [];

  const addBtn = el("button", { class: "btn btn-primary", onclick: showCreateAgent }, "✚ 新建 Agent");
  const inner = pageShell("Agent 管理", "创建、切换和管理你的 AI Agent", [addBtn]);
  root.append(el("div", { class: "page" }, inner));

  if (!state.agents.length) {
    inner.append(emptyState("暂无 Agent，点击右上角创建"));
    return;
  }

  for (const a of state.agents) {
    const isActive = a.name === data.active_agent;
    const card = el("div", { class: "card" },
      el("div", { class: "card-row" },
        el("div", null,
          el("div", { class: "card-title" },
            "🤖 " + a.name,
            isActive ? el("span", { class: "tag green" }, "当前") : null,
            a.primary_model ? el("span", { class: "tag blue" }, a.primary_model) : null),
          el("div", { class: "card-sub" }, a.description || a.workspace_path || "")),
        el("div", { class: "card-actions" },
          el("button", { class: "btn btn-sm", onclick: () => showEditAgent(a) }, "编辑"),
          el("button", { class: "btn btn-sm", onclick: () => showAgentFiles(a) }, "文件"),
          !isActive ? el("button", {
            class: "btn btn-sm",
            onclick: async () => {
              await api.selectAgent(a.name);
              state.activeAgent = a.name;
              refreshSelectors();
              toast(`已切换到 '${a.name}'`, "success");
              loadAgentsView();
            },
          }, "切换") : null,
          el("button", {
            class: "btn btn-sm btn-danger",
            onclick: async () => {
              const ok = await confirmDialog("删除 Agent", `确定删除 Agent '${a.name}' 吗？工作空间文件将被删除。`, { danger: true, okText: "删除" });
              if (!ok) return;
              await api.deleteAgent(a.name);
              toast("已删除", "success");
              loadAgentsView();
            },
          }, "删除"))));
    inner.append(card);
  }
}

function showCreateAgent() {
  const nameInput = el("input", { class: "input", placeholder: "Agent 名称（字母/数字/下划线）" });
  const descInput = el("input", { class: "input", placeholder: "描述（可选）" });
  const modelSel = el("select", { class: "select" });
  modelSel.append(el("option", { value: "" }, "（默认）"));
  for (const m of state.models) modelSel.append(el("option", { value: m.name }, m.name));

  openModal({
    title: "新建 Agent",
    body: el("div", null,
      el("div", { class: "form-row" }, el("label", null, "名称 *"), nameInput),
      el("div", { class: "form-row" }, el("label", null, "描述"), descInput),
      el("div", { class: "form-row" }, el("label", null, "主模型"), modelSel)),
    footer: [
      el("button", { class: "btn", onclick: () => $("#modal-root").innerHTML = "" }, "取消"),
      el("button", {
        class: "btn btn-primary",
        onclick: async (e) => {
          const name = nameInput.value.trim();
          if (!name) { toast("请输入名称", "warn"); return; }
          try {
            await api.createAgent({
              name, description: descInput.value.trim(),
              primary_model: modelSel.value || null,
            });
            $("#modal-root").innerHTML = "";
            toast(`Agent '${name}' 已创建`, "success");
            const d = await api.getAgents();
            state.agents = d.agents || [];
            refreshSelectors();
            loadAgentsView();
          } catch (e2) { toast(e2.message, "error"); }
        },
      }, "创建"),
    ],
  });
}

async function showEditAgent(agent) {
  let detail;
  try { detail = await api.getAgent(agent.name); } catch (e) { toast(e.message, "error"); return; }
  const cfg = detail.config || {};

  const descInput = el("input", { class: "input", value: cfg.description || "" });
  const modelSel = el("select", { class: "select" });
  modelSel.append(el("option", { value: "" }, "（未设置）"));
  for (const m of state.models) modelSel.append(el("option", { value: m.name }, m.name));
  modelSel.value = cfg.primary_model || "";
  const ratioInput = el("input", { class: "input", type: "number", min: "0.1", max: "0.95", step: "0.05", value: cfg.context_limit_ratio ?? 0.8 });
  const compressChk = el("input", { type: "checkbox" });
  compressChk.checked = cfg.auto_compress !== false;

  openModal({
    title: `编辑 Agent — ${agent.name}`,
    body: el("div", null,
      el("div", { class: "form-row" }, el("label", null, "描述"), descInput),
      el("div", { class: "form-row" }, el("label", null, "主模型"), modelSel),
      el("div", { class: "form-row" }, el("label", null, "压缩阈值比例 (0.1-0.95)"), ratioInput,
        el("div", { class: "form-hint" }, "上下文使用达到该比例时触发自动压缩")),
      el("div", { class: "form-row" },
        el("label", { class: "checkbox-row" }, compressChk, " 启用自动压缩"))),
    footer: [
      el("button", { class: "btn", onclick: () => $("#modal-root").innerHTML = "" }, "取消"),
      el("button", {
        class: "btn btn-primary",
        onclick: async () => {
          try {
            await api.updateAgent(agent.name, {
              description: descInput.value,
              primary_model: modelSel.value || null,
              context_limit_ratio: parseFloat(ratioInput.value) || 0.8,
              auto_compress: compressChk.checked,
            });
            $("#modal-root").innerHTML = "";
            toast("已保存", "success");
            loadAgentsView();
          } catch (e) { toast(e.message, "error"); }
        },
      }, "保存"),
    ],
  });
}

async function showAgentFiles(agent) {
  let detail;
  try { detail = await api.getAgent(agent.name); } catch (e) { toast(e.message, "error"); return; }
  const files = detail.files || {};
  const FILE_NAMES = ["soul.md", "memory.md", "tools.md", "usage.md"];
  let current = "soul.md";

  const editor = el("textarea", { class: "file-editor" });
  editor.value = files[current] || "";

  const tabs = el("div", { class: "file-tabs" });
  const renderTabs = () => {
    tabs.innerHTML = "";
    for (const fn of FILE_NAMES) {
      const t = el("span", { class: `file-tab ${fn === current ? "active" : ""}` }, fn);
      t.addEventListener("click", () => { current = fn; editor.value = files[fn] || ""; renderTabs(); });
      tabs.append(t);
    }
  };
  renderTabs();

  const FILE_DESC = {
    "soul.md": "Agent 性格设定（系统提示的一部分）",
    "memory.md": "长期记忆（始终在系统提示中）",
    "tools.md": "工具使用指南（系统提示的一部分）",
    "usage.md": "使用说明（系统提示的一部分）",
  };
  const hint = el("div", { class: "form-hint", style: "margin-bottom:8px;" }, FILE_DESC[current]);
  tabs.addEventListener("click", () => setTimeout(() => (hint.textContent = FILE_DESC[current]), 0));

  openModal({
    title: `Agent 文件 — ${agent.name}`,
    width: "min(760px, calc(100vw - 40px))",
    body: el("div", null, tabs, hint, editor),
    footer: [
      el("button", { class: "btn", onclick: () => $("#modal-root").innerHTML = "" }, "关闭"),
      el("button", {
        class: "btn btn-primary",
        onclick: async () => {
          try {
            await api.updateAgentFile(agent.name, current, editor.value);
            files[current] = editor.value;
            toast(`${current} 已保存`, "success");
          } catch (e) { toast(e.message, "error"); }
        },
      }, "保存当前文件"),
    ],
  });
}

/* ===================================================================
   8. 模型管理视图
   =================================================================== */

async function loadModelsView() {
  const root = $("#view-models");
  root.innerHTML = "";
  const data = await api.getModels();
  state.models = data.models || [];

  const addBtn = el("button", { class: "btn btn-primary", onclick: () => showModelForm(null) }, "✚ 添加模型");
  const inner = pageShell("模型管理", "配置 LLM 模型、嵌入模型和重排序模型", [addBtn]);
  root.append(el("div", { class: "page" }, inner));

  // 主模型列表
  inner.append(el("div", { class: "section-title" }, "🧠 聊天模型"));
  if (!state.models.length) inner.append(emptyState("暂无模型"));
  for (const m of state.models) {
    const isSel = m.name === data.last_selected;
    inner.append(el("div", { class: "card" },
      el("div", { class: "card-row" },
        el("div", null,
          el("div", { class: "card-title" },
            m.name,
            isSel ? el("span", { class: "tag green" }, "使用中") : null,
            m.vision ? el("span", { class: "tag purple" }, "视觉") : null),
          el("div", { class: "card-sub" }, `${m.model} · ${m.url} · 上下文 ${fmtNum(m.context_limit)}`)),
        el("div", { class: "card-actions" },
          el("button", { class: "btn btn-sm", onclick: () => showModelForm(m) }, "编辑"),
          !isSel ? el("button", {
            class: "btn btn-sm",
            onclick: async () => {
              await api.selectModel(m.name);
              state.selectedModel = m.name;
              refreshSelectors();
              toast(`已切换到 '${m.name}'`, "success");
              loadModelsView();
            },
          }, "使用") : null,
          el("button", {
            class: "btn btn-sm btn-danger",
            onclick: async () => {
              const ok = await confirmDialog("删除模型", `确定删除模型 '${m.name}' 吗？`, { danger: true, okText: "删除" });
              if (!ok) return;
              await api.deleteModel(m.name);
              toast("已删除", "success");
              loadModelsView();
              refreshSelectors();
            },
          }, "删除")))));
  }

  // 嵌入模型
  inner.append(el("div", { class: "section-title" }, "🧭 嵌入模型（向量搜索）"));
  inner.append(specialModelCard("embedding", data.embedding_model));

  // 重排序模型
  inner.append(el("div", { class: "section-title" }, "🔀 重排序模型（搜索优化）"));
  inner.append(specialModelCard("rerank", data.rerank_model));
}

function specialModelCard(kind, cfg) {
  const title = kind === "embedding" ? "嵌入模型" : "重排序模型";
  const configured = cfg && cfg.model;
  return el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("div", null,
        el("div", { class: "card-title" },
          configured ? (cfg.name || cfg.model) : `未配置${title}`,
          configured ? el("span", { class: "tag green" }, "已配置") : el("span", { class: "tag" }, "未配置")),
        configured ? el("div", { class: "card-sub" }, `${cfg.model} · ${cfg.url || ""}`) : null),
      el("div", { class: "card-actions" },
        el("button", { class: "btn btn-sm", onclick: () => showSpecialModelForm(kind, cfg) },
          configured ? "修改" : "配置"),
        configured ? el("button", {
          class: "btn btn-sm btn-danger",
          onclick: async () => {
            const ok = await confirmDialog("删除配置", `确定删除${title}配置吗？`, { danger: true, okText: "删除" });
            if (!ok) return;
            if (kind === "embedding") await api.delEmbedding();
            else await api.delRerank();
            toast("已删除", "success");
            loadModelsView();
          },
        }, "删除") : null)));
}

function showSpecialModelForm(kind, cfg) {
  const title = kind === "embedding" ? "嵌入模型" : "重排序模型";
  const nameInput = el("input", { class: "input", value: cfg?.name || "", placeholder: "配置名称（可选）" });
  const keyInput = el("input", { class: "input", value: cfg?.apiKey || "", placeholder: "API Key", type: "password" });
  const urlInput = el("input", { class: "input", value: cfg?.url || "", placeholder: "https://api.openai.com/v1" });
  const modelInput = el("input", { class: "input", value: cfg?.model || "", placeholder: kind === "embedding" ? "text-embedding-3-small" : "rerank模型ID" });
  const rows = [
    el("div", { class: "form-row" }, el("label", null, "名称"), nameInput),
    el("div", { class: "form-row" }, el("label", null, "API Key *"), keyInput),
    el("div", { class: "form-row" }, el("label", null, "Base URL *"), urlInput),
    el("div", { class: "form-row" }, el("label", null, "模型 ID *"), modelInput),
  ];
  let topNInput = null;
  if (kind === "rerank") {
    topNInput = el("input", { class: "input", type: "number", min: "1", max: "50", value: cfg?.top_n ?? 5 });
    rows.push(el("div", { class: "form-row" }, el("label", null, "Top N"), topNInput));
  }

  openModal({
    title: `配置${title}`,
    body: el("div", null, rows),
    footer: [
      el("button", { class: "btn", onclick: () => $("#modal-root").innerHTML = "" }, "取消"),
      el("button", {
        class: "btn btn-primary",
        onclick: async () => {
          const payload = {
            name: nameInput.value.trim(),
            apiKey: keyInput.value.trim(),
            url: urlInput.value.trim(),
            model: modelInput.value.trim(),
          };
          if (kind === "rerank") payload.top_n = parseInt(topNInput.value) || 5;
          if (!payload.apiKey || !payload.url || !payload.model) {
            toast("API Key / URL / 模型 ID 为必填", "warn"); return;
          }
          try {
            if (kind === "embedding") await api.setEmbedding(payload);
            else await api.setRerank(payload);
            $("#modal-root").innerHTML = "";
            toast("已保存", "success");
            loadModelsView();
          } catch (e) { toast(e.message, "error"); }
        },
      }, "保存"),
    ],
  });
}

function showModelForm(m) {
  const isEdit = !!m;
  m = m || {};
  const nameInput = el("input", { class: "input", value: m.name || "", placeholder: "唯一名称，如 gpt4o" });
  if (isEdit) nameInput.disabled = true;
  const keyInput = el("input", { class: "input", type: "password", value: m.apiKey || "", placeholder: "sk-..." });
  const urlInput = el("input", { class: "input", value: m.url || "", placeholder: "https://api.openai.com/v1" });
  const modelInput = el("input", { class: "input", value: m.model || "", placeholder: "gpt-4o / deepseek-chat / ..." });
  const limitInput = el("input", { class: "input", type: "number", value: m.context_limit ?? 128000 });
  const tempInput = el("input", {
    class: "input", type: "number", min: "0", max: "2", step: "0.1",
    value: m.temperature ?? "", placeholder: "留空用全局值 0.1",
  });
  const visionChk = el("input", { type: "checkbox" });
  visionChk.checked = !!m.vision;

  openModal({
    title: isEdit ? `编辑模型 — ${m.name}` : "添加模型",
    body: el("div", null,
      el("div", { class: "form-grid" },
        el("div", { class: "form-row" }, el("label", null, "名称 *"), nameInput),
        el("div", { class: "form-row" }, el("label", null, "模型 ID *"), modelInput)),
      el("div", { class: "form-row" }, el("label", null, "API Key *"), keyInput),
      el("div", { class: "form-row" }, el("label", null, "Base URL *"), urlInput),
      el("div", { class: "form-grid" },
        el("div", { class: "form-row" }, el("label", null, "上下文长度"), limitInput),
        el("div", { class: "form-row" }, el("label", null, "温度 (0-2)"), tempInput,
          el("div", { class: "form-hint" }, "留空用全局值 0.1；部分模型有强制要求（如 kimi-k3 必须为 1）"))),
        el("div", { class: "form-row" }, el("label", null, " "),
          el("label", { class: "checkbox-row" }, visionChk, " 支持视觉（图片识别）"))),
    footer: [
      el("button", { class: "btn", onclick: () => $("#modal-root").innerHTML = "" }, "取消"),
      el("button", {
        class: "btn btn-primary",
        onclick: async () => {
          const tempVal = tempInput.value.trim();
          const payload = {
            name: nameInput.value.trim(),
            apiKey: keyInput.value.trim(),
            url: urlInput.value.trim(),
            model: modelInput.value.trim(),
            context_limit: parseInt(limitInput.value) || 128000,
            vision: visionChk.checked,
          };
          if (tempVal !== "") {
            const t = parseFloat(tempVal);
            if (isNaN(t) || t < 0 || t > 2) { toast("温度需在 0-2 之间", "warn"); return; }
            payload.temperature = t;
          }
          if (!payload.name || !payload.apiKey || !payload.url || !payload.model) {
            toast("名称 / Key / URL / 模型 ID 为必填", "warn"); return;
          }
          try {
            if (isEdit) await api.updateModel(m.name, payload);
            else await api.addModel(payload);
            $("#modal-root").innerHTML = "";
            toast("已保存", "success");
            const d = await api.getModels();
            state.models = d.models || [];
            refreshSelectors();
            loadModelsView();
          } catch (e) { toast(e.message, "error"); }
        },
      }, "保存"),
    ],
  });
}

/* ===================================================================
   9. 备用模型视图
   =================================================================== */

async function loadFallbackView() {
  const root = $("#view-fallback");
  root.innerHTML = "";
  const data = await api.getFallback();
  const inner = pageShell("备用模型", "主模型异常时自动切换备用模型；视觉模型同理（image 工具使用）");
  root.append(el("div", { class: "page" }, inner));

  const renderCategory = (cat, title, desc) => {
    const list = data[cat] || [];
    inner.append(el("div", { class: "section-title" }, title));
    const card = el("div", { class: "card" });
    card.append(el("div", { class: "card-sub", style: "margin-bottom:10px;" }, desc));

    if (!list.length) {
      card.append(el("div", { class: "card-sub" }, "（未配置）"));
    } else {
      const listEl = el("div", { class: "reorder-list" });
      list.forEach((name, i) => {
        const modelInfo = (data.available_models || []).find(x => x.name === name);
        const item = el("div", { class: "reorder-item" },
          el("span", { class: "reorder-idx" }, String(i + 1)),
          el("span", { class: "reorder-name" }, name),
          modelInfo ? el("span", { class: "tag green" }, "已配置") : el("span", { class: "tag red" }, "未配置"),
          el("div", { class: "reorder-btns" },
            el("button", {
              class: "btn btn-sm", title: "上移", disabled: i === 0 ? "" : null,
              onclick: async () => {
                if (i === 0) return;
                const order = [...list];
                [order[i - 1], order[i]] = [order[i], order[i - 1]];
                await api.reorderFallback(cat, order);
                loadFallbackView();
              },
            }, "↑"),
            el("button", {
              class: "btn btn-sm", title: "下移", disabled: i === list.length - 1 ? "" : null,
              onclick: async () => {
                if (i === list.length - 1) return;
                const order = [...list];
                [order[i + 1], order[i]] = [order[i], order[i + 1]];
                await api.reorderFallback(cat, order);
                loadFallbackView();
              },
            }, "↓"),
            el("button", {
              class: "btn btn-sm btn-danger",
              onclick: async () => {
                await api.removeFallback(cat, name);
                toast("已移除", "success");
                loadFallbackView();
              },
            }, "移除")));
        listEl.append(item);
      });
      card.append(listEl);
    }

    // 添加
    const sel = el("select", { class: "select", style: "margin-top:12px;" });
    const candidates = (data.available_models || []).filter(x =>
      !(data[cat] || []).includes(x.name) && (cat === "main" || x.vision));
    sel.append(el("option", { value: "" }, "选择模型…"));
    for (const c of candidates)
      sel.append(el("option", { value: c.name }, c.name + (c.vision ? " 👁" : "")));
    const addBtn2 = el("button", { class: "btn btn-sm btn-primary" }, "添加");
    addBtn2.addEventListener("click", async () => {
      if (!sel.value) { toast("请选择模型", "warn"); return; }
      try {
        await api.addFallback({ category: cat, model_name: sel.value });
        toast("已添加", "success");
        loadFallbackView();
      } catch (e) { toast(e.message, "error"); }
    });
    const row = el("div", { style: "display:flex;gap:8px;margin-top:12px;align-items:center;" }, sel, addBtn2);
    if (list.length) {
      const clearBtn = el("button", { class: "btn btn-sm btn-danger" }, "清空");
      clearBtn.addEventListener("click", async () => {
        const ok = await confirmDialog("清空备用列表", `确定清空 ${cat} 备用列表吗？`, { danger: true, okText: "清空" });
        if (!ok) return;
        await api.clearFallback(cat);
        loadFallbackView();
      });
      row.append(clearBtn);
    }
    card.append(row);
    inner.append(card);
  };

  renderCategory("main", "🧠 主模型备用", "聊天主模型调用失败时，按顺序切换备用模型");
  renderCategory("vision", "👁 视觉模型备用", "image 工具的视觉模型不可用时按顺序切换（仅可选择支持视觉的模型）");
}

/* ===================================================================
   10. 技能视图
   =================================================================== */

async function loadSkillsView() {
  const root = $("#view-skills");
  root.innerHTML = "";
  let agent = state.activeAgent || state.agents[0]?.name;

  const inner = pageShell("技能管理", "技能是可复用的提示词 + 可选脚本");
  root.append(el("div", { class: "page" }, inner));
  const listEl = el("div");
  inner.append(el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("div", { class: "card-title" }, "选择 Agent"),
      agentSelector(agent, (v) => { agent = v; render(); }))));
  inner.append(listEl);

  async function render() {
    listEl.innerHTML = "";
    if (!agent) { listEl.append(emptyState("暂无 Agent")); return; }
    const data = await api.getSkills(agent);
    if (!data.skills.length) {
      listEl.append(emptyState("暂无技能。可在 CLI 中 /skills add 创建，或让 AI 使用 skills_create 工具自动创建。", "⚡"));
      return;
    }
    for (const s of data.skills) {
      listEl.append(el("div", { class: "card" },
        el("div", { class: "card-row" },
          el("div", null,
            el("div", { class: "card-title" },
              "⚡ " + s.name,
              s.active ? el("span", { class: "tag green" }, "已激活") : null,
              s.has_scripts ? el("span", { class: "tag blue" }, `${s.scripts.length} 个脚本`) : null),
            el("div", { class: "card-sub" }, plainText(s.prompt || s.prompt_preview, 140))),
          el("div", { class: "card-actions" },
            el("button", {
              class: "btn btn-sm",
              onclick: () => openModal({
                title: `技能 — ${s.name}`,
                width: "min(700px, calc(100vw - 40px))",
                body: el("div", null,
                  el("div", { class: "md-content", html: renderMd(s.prompt || "") })),
              }),
            }, "查看"),
            s.active
              ? el("button", {
                  class: "btn btn-sm",
                  onclick: async () => {
                    await api.deactivateSkill(agent, s.name);
                    toast("已取消激活", "success"); render();
                  },
                }, "取消激活")
              : el("button", {
                  class: "btn btn-sm btn-primary",
                  onclick: async () => {
                    await api.activateSkills(agent, [s.name]);
                    toast("已激活", "success"); render();
                  },
                }, "激活"),
            el("button", {
              class: "btn btn-sm btn-danger",
              onclick: async () => {
                const ok = await confirmDialog("删除技能", `确定删除技能 '${s.name}' 吗？`, { danger: true, okText: "删除" });
                if (!ok) return;
                await api.deleteSkill(agent, s.name);
                toast("已删除", "success"); render();
              },
            }, "删除")))));
    }
  }
  await render();
}

/* ===================================================================
   11. MCP 视图
   =================================================================== */

async function loadMCPView() {
  const root = $("#view-mcp");
  root.innerHTML = "";
  let agent = state.activeAgent || state.agents[0]?.name;

  const inner = pageShell("MCP 服务器", "连接外部工具服务器，扩展工具能力");
  root.append(el("div", { class: "page" }, inner));
  const listEl = el("div");

  inner.append(el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("div", { class: "card-title" }, "选择 Agent"),
      el("div", { class: "card-actions" },
        agentSelector(agent, (v) => { agent = v; render(); }),
        el("button", { class: "btn btn-sm btn-primary", onclick: () => showAddMCP(agent, render) }, "✚ 添加服务器")))));
  inner.append(listEl);

  async function render() {
    listEl.innerHTML = "";
    if (!agent) { listEl.append(emptyState("暂无 Agent")); return; }
    let data;
    try { data = await api.getMCP(agent); }
    catch (e) { listEl.append(emptyState("加载失败: " + e.message, "⚠️")); return; }

    if (!data.servers.length) {
      listEl.append(emptyState("暂无 MCP 服务器", "🔌"));
      return;
    }

    for (const s of data.servers) {
      const toolsEl = el("div", { style: "margin-top:10px;" });
      const card = el("div", { class: "card" },
        el("div", { class: "card-row" },
          el("div", null,
            el("div", { class: "card-title" },
              "🔌 " + s.name,
              el("span", { class: `dot ${s.connected ? "green" : "red"}` }),
              el("span", { class: "tag" }, `${(s.tools || []).length} 工具`)),
            el("div", { class: "card-sub" }, s.url)),
          el("div", { class: "card-actions" },
            el("button", {
              class: "btn btn-sm",
              onclick: async () => {
                toolsEl.innerHTML = "<div class='card-sub'>加载工具列表…</div>";
                try {
                  const t = await api.getMCPTools(agent, s.name);
                  toolsEl.innerHTML = "";
                  if (!t.tools.length) { toolsEl.append(el("div", { class: "card-sub" }, "无可用工具")); return; }
                  for (const tool of t.tools) {
                    const sw = el("input", { type: "checkbox" });
                    sw.checked = tool.enabled;
                    sw.addEventListener("change", async () => {
                      try {
                        await api.toggleMCPTool(agent, s.name, tool.name, sw.checked);
                        toast(`工具 '${tool.name}' 已${sw.checked ? "启用" : "禁用"}`, "success");
                      } catch (e) { toast(e.message, "error"); sw.checked = !sw.checked; }
                    });
                    toolsEl.append(el("div", { class: "tool-toggle-card", style: "margin-bottom:6px;" },
                      el("div", { class: "tool-toggle-info" },
                        el("div", { class: "tool-toggle-name" }, tool.name),
                        el("div", { class: "tool-toggle-desc" }, tool.description || "")),
                      el("label", { class: "switch" }, sw, el("span", { class: "track" }))));
                  }
                } catch (e) { toolsEl.innerHTML = `<div class='card-sub'>加载失败: ${escapeHtml(e.message)}</div>`; }
              },
            }, "工具"),
            el("button", {
              class: "btn btn-sm",
              onclick: async (e) => {
                e.target.disabled = true;
                try {
                  const r = await api.refreshMCP(agent, s.name);
                  toast(r.message, "success");
                  render();
                } catch (e2) { toast(e2.message, "error"); e.target.disabled = false; }
              },
            }, "刷新"),
            el("button", {
              class: "btn btn-sm btn-danger",
              onclick: async () => {
                const ok = await confirmDialog("移除服务器", `确定移除 MCP 服务器 '${s.name}' 吗？`, { danger: true, okText: "移除" });
                if (!ok) return;
                await api.removeMCP(agent, s.name);
                toast("已移除", "success");
                render();
              },
            }, "移除"))),
        toolsEl);
      listEl.append(card);
    }
  }
  await render();
}

function showAddMCP(agent, onDone) {
  const nameInput = el("input", { class: "input", placeholder: "服务器名称" });
  const urlInput = el("input", { class: "input", placeholder: "http://localhost:3000/sse" });
  const headersInput = el("textarea", { class: "input", rows: 3, placeholder: '每行一个 Header，格式: Key=Value\n例如:\nAuthorization=Bearer xxx' });

  openModal({
    title: "添加 MCP 服务器",
    body: el("div", null,
      el("div", { class: "form-row" }, el("label", null, "名称 *"), nameInput),
      el("div", { class: "form-row" }, el("label", null, "URL *"), urlInput),
      el("div", { class: "form-row" }, el("label", null, "HTTP Headers（可选）"), headersInput,
        el("div", { class: "form-hint" }, "每行一个，格式 Key=Value"))),
    footer: [
      el("button", { class: "btn", onclick: () => $("#modal-root").innerHTML = "" }, "取消"),
      el("button", {
        class: "btn btn-primary",
        onclick: async () => {
          const name = nameInput.value.trim();
          const url = urlInput.value.trim();
          if (!name || !url) { toast("名称和 URL 为必填", "warn"); return; }
          const headers = {};
          for (const line of headersInput.value.split("\n")) {
            const t = line.trim();
            if (!t) continue;
            const eq = t.indexOf("=");
            if (eq > 0) headers[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
          }
          try {
            const r = await api.addMCP(agent, { name, url, headers });
            $("#modal-root").innerHTML = "";
            toast(r.message, "success");
            onDone();
          } catch (e) { toast(e.message, "error"); }
        },
      }, "添加"),
    ],
  });
}

/* ===================================================================
   12. 知识库视图
   =================================================================== */

async function loadKnowledgeView() {
  const root = $("#view-knowledge");
  root.innerHTML = "";
  let agent = state.activeAgent || state.agents[0]?.name;

  const inner = pageShell("知识库", "管理 Agent 的知识文件，支持语义搜索");
  root.append(el("div", { class: "page" }, inner));
  const listEl = el("div");
  const statusEl = el("div", { class: "card-sub" });

  const fileInput = el("input", { type: "file", hidden: true });
  fileInput.addEventListener("change", async () => {
    const f = fileInput.files[0];
    fileInput.value = "";
    if (!f) return;
    try {
      const r = await api.uploadKnowledge(agent, f);
      toast(r.message || "已上传并索引", "success");
      render();
    } catch (e) { toast(e.message, "error"); }
  });

  inner.append(el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("div", null,
        el("div", { class: "card-title" }, "选择 Agent"),
        statusEl),
      el("div", { class: "card-actions" },
        agentSelector(agent, (v) => { agent = v; render(); }),
        el("button", {
          class: "btn btn-sm",
          onclick: async () => {
            const r = await promptDialog("按路径添加文件", [
              { key: "path", label: "服务器文件绝对路径", placeholder: "/path/to/file.md" },
            ], "添加");
            if (!r || !r.path.trim()) return;
            try {
              const res = await api.addKnowledge(agent, r.path.trim());
              toast(res.message || "已添加", "success");
              render();
            } catch (e) { toast(e.message, "error"); }
          },
        }, "按路径添加"),
        el("button", { class: "btn btn-sm btn-primary", onclick: () => fileInput.click() }, "⬆ 上传文件"),
        el("button", {
          class: "btn btn-sm",
          onclick: async (e) => {
            e.target.disabled = true;
            try {
              const r = await api.reindexKnowledge(agent);
              toast(r.message || "重建完成", "success");
              render();
            } catch (e2) { toast(e2.message, "error"); }
            e.target.disabled = false;
          },
        }, "重建索引"))),
    fileInput));
  inner.append(listEl);

  async function render() {
    listEl.innerHTML = "";
    statusEl.textContent = "";
    if (!agent) { listEl.append(emptyState("暂无 Agent")); return; }
    const data = await api.getKnowledge(agent);
    statusEl.textContent = data.vector_enabled ? "" : "⚠️ 未配置嵌入模型，文件仅保存不索引（模型管理 → 嵌入模型）";

    if (!data.files.length) {
      listEl.append(emptyState("知识库为空", "📚"));
      return;
    }
    for (const f of data.files) {
      listEl.append(el("div", { class: "card" },
        el("div", { class: "card-row" },
          el("div", null,
            el("div", { class: "card-title" }, "📄 " + f.name),
            el("div", { class: "card-sub" },
              `${fmtSize(f.size)}${f.segments ? ` · ${f.segments} 个段落` : ""}`)),
          el("div", { class: "card-actions" },
            el("button", {
              class: "btn btn-sm btn-danger",
              onclick: async () => {
                const ok = await confirmDialog("删除文件", `确定从知识库删除 '${f.name}' 吗？`, { danger: true, okText: "删除" });
                if (!ok) return;
                await api.removeKnowledge(agent, f.name);
                toast("已删除", "success");
                render();
              },
            }, "删除")))));
    }
  }
  await render();
}

/* ===================================================================
   13. 工具管理视图
   =================================================================== */

async function loadToolsView() {
  const root = $("#view-tools");
  root.innerHTML = "";
  let agent = state.activeAgent || state.agents[0]?.name;

  const inner = pageShell("工具管理", "启用/禁用 AI 可调用的工具（与 CLI /tools on|off 一致）");
  root.append(el("div", { class: "page" }, inner));
  const listEl = el("div");
  inner.append(el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("div", { class: "card-title" }, "选择 Agent"),
      agentSelector(agent, (v) => { agent = v; render(); }))));
  inner.append(listEl);

  async function render() {
    listEl.innerHTML = "";
    if (!agent) { listEl.append(emptyState("暂无 Agent")); return; }
    const data = await api.getTools(agent);

    // 按类别分组
    const groups = {};
    for (const t of data.tools) {
      (groups[t.category] = groups[t.category] || []).push(t);
    }
    for (const [cat, tools] of Object.entries(groups)) {
      const sec = el("div", { class: "section-title" }, cat === "builtin" ? "🔧 内置工具" : "📈 " + cat);
      listEl.append(sec);
      const grid = el("div", { class: "tools-grid" });
      for (const t of tools) {
        const sw = el("input", { type: "checkbox" });
        sw.checked = t.enabled;
        sw.addEventListener("change", async () => {
          try {
            await api.toggleTool(agent, t.name, sw.checked);
            toast(`工具 '${t.name}' 已${sw.checked ? "启用" : "禁用"}`, "success");
            cardEl.classList.toggle("disabled", !sw.checked);
          } catch (e) { toast(e.message, "error"); sw.checked = !sw.checked; }
        });
        const cardEl = el("div", { class: `tool-toggle-card ${t.enabled ? "" : "disabled"}` },
          el("div", { class: "tool-toggle-info" },
            el("div", { class: "tool-toggle-name" }, `${toolIcon(t.name)} ${t.name}`),
            el("div", { class: "tool-toggle-desc" }, t.description || "")),
          el("label", { class: "switch" }, sw, el("span", { class: "track" })));
        grid.append(cardEl);
      }
      listEl.append(grid);
    }
  }
  await render();
}

/* ===================================================================
   14. 向量索引视图
   =================================================================== */

async function loadEmbeddingView() {
  const root = $("#view-embedding");
  root.innerHTML = "";
  let agent = state.activeAgent || state.agents[0]?.name;

  const inner = pageShell("向量索引", "将 Agent 工作空间索引到向量数据库以启用语义搜索");
  root.append(el("div", { class: "page" }, inner));
  const statusCard = el("div", { class: "card" });

  inner.append(el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("div", { class: "card-title" }, "选择 Agent"),
      el("div", { class: "card-actions" },
        agentSelector(agent, (v) => { agent = v; render(); }),
        el("button", {
          class: "btn btn-sm btn-primary",
          onclick: async (e) => {
            e.target.disabled = true;
            try {
              const r = await api.embeddingIndex(agent);
              toast(r.message, "success");
              render();
            } catch (e2) { toast(e2.message, "error"); e2.target; }
            e.target.disabled = false;
          },
        }, "开始索引"),
        el("button", {
          class: "btn btn-sm",
          onclick: async (e) => {
            e.target.disabled = true;
            try {
              const r = await api.embeddingReindex(agent);
              toast(r.message || "重建完成", "success");
              render();
            } catch (e2) { toast(e2.message, "error"); }
            e.target.disabled = false;
          },
        }, "重新索引"),
        el("button", {
          class: "btn btn-sm btn-danger",
          onclick: async () => {
            const ok = await confirmDialog("清除索引", `确定清除 Agent '${agent}' 的向量索引吗？`, { danger: true, okText: "清除" });
            if (!ok) return;
            try {
              const r = await api.embeddingClear(agent);
              toast(r.message, "success");
              render();
            } catch (e) { toast(e.message, "error"); }
          },
        }, "清除索引")))));
  inner.append(statusCard);

  async function render() {
    if (!agent) { statusCard.innerHTML = ""; statusCard.append(emptyState("暂无 Agent")); return; }
    statusCard.innerHTML = "<div class='card-sub'>查询状态…</div>";
    try {
      const s = await api.embeddingStatus(agent);
      statusCard.innerHTML = "";
      statusCard.append(el("div", { class: "card-row" },
        el("div", null,
          el("div", { class: "card-title" }, "📊 索引状态"),
          el("div", { class: "card-sub" },
            s.enabled
              ? `Agent: ${agent} · 向量数量: ${fmtNum(s.count)}`
              : (s.message || "向量数据库未启用"))),
        el("span", { class: `tag ${s.enabled ? (s.count > 0 ? "green" : "amber") : "red"}` },
          s.enabled ? (s.count > 0 ? "已索引" : "未索引") : "未启用")));
    } catch (e) {
      statusCard.innerHTML = `<div class='card-sub'>查询失败: ${escapeHtml(e.message)}</div>`;
    }
  }
  await render();
}

/* ===================================================================
   15. 历史会话视图
   =================================================================== */

async function loadHistoryView() {
  const root = $("#view-history");
  root.innerHTML = "";
  let agent = state.activeAgent || state.agents[0]?.name;

  const inner = pageShell("历史会话", "查看、恢复或删除历史对话");
  root.append(el("div", { class: "page" }, inner));
  const listEl = el("div");
  inner.append(el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("div", { class: "card-title" }, "选择 Agent"),
      agentSelector(agent, (v) => { agent = v; render(); }))));
  inner.append(listEl);

  async function render() {
    listEl.innerHTML = "";
    if (!agent) { listEl.append(emptyState("暂无 Agent")); return; }
    const data = await api.getHistory(agent);
    if (!data.sessions.length) {
      listEl.append(emptyState("暂无历史会话", "🕘"));
      return;
    }
    for (const s of data.sessions) {
      const preview = (s.title || "").slice(0, 80);
      listEl.append(el("div", { class: "card history-item" },
        el("div", { class: "card-row" },
          el("div", null,
            el("div", { class: "card-title" },
              "💬 " + fmtTime(s.created_at || s.filename || ""),
              el("span", { class: "tag" }, `${s.message_count ?? "?"} 条消息`)),
            el("div", { class: "card-sub" }, preview || "（无摘要）")),
          el("div", { class: "card-actions" },
            el("button", {
              class: "btn btn-sm",
              onclick: () => showHistoryDetail(agent, s),
            }, "查看"),
            el("button", {
              class: "btn btn-sm btn-primary",
              onclick: async () => {
                const ok = await confirmDialog("恢复会话", "将该历史会话恢复为当前对话？（当前对话会先保存到历史）", { okText: "恢复" });
                if (!ok) return;
                try {
                  const r = await api.chatLoad(agent, currentModel(), s.filename);
                  toast(r.message, "success");
                  switchView("chat");
                  clearMessages();
                  renderRestoredMessages(r.messages || []);
                  if (r.usage) updateCtxMeter(r.usage);
                } catch (e) { toast(e.message, "error"); }
              },
            }, "恢复"),
            el("button", {
              class: "btn btn-sm btn-danger",
              onclick: async () => {
                const ok = await confirmDialog("删除会话", "确定删除该历史会话吗？", { danger: true, okText: "删除" });
                if (!ok) return;
                await api.deleteHistory(agent, s.filename);
                toast("已删除", "success");
                render();
              },
            }, "删除")))));
    }
  }
  await render();
}

async function showHistoryDetail(agent, sessionInfo) {
  let detail;
  try { detail = await api.getHistoryDetail(agent, sessionInfo.filename); }
  catch (e) { toast(e.message, "error"); return; }

  const body = el("div", { style: "display:flex;flex-direction:column;gap:12px;" });
  for (const m of detail.messages || []) {
    if (m.role === "system" || m.role === "tool") continue;
    const isUser = m.role === "user";
    body.append(el("div", { style: `display:flex;justify-content:${isUser ? "flex-end" : "flex-start"};` },
      el("div", {
        style: `max-width:85%;padding:8px 12px;border-radius:10px;font-size:13px;` +
          (isUser ? "background:var(--accent-dim);" : "background:var(--bg-2);"),
      }, el("div", { class: "md-content", html: renderMd(m.content || "") }))));
  }
  if (!body.children.length) body.append(emptyState("无可显示的消息"));

  openModal({
    title: `会话详情 — ${fmtTime(sessionInfo.created_at) || sessionInfo.filename}`,
    width: "min(760px, calc(100vw - 40px))",
    body,
  });
}

/* ===================================================================
   16. 设置视图
   =================================================================== */

async function loadSettingsView() {
  const root = $("#view-settings");
  root.innerHTML = "";
  const [settingsData, info] = await Promise.all([api.getSettings(), api.info()]);
  const s = settingsData.settings || {};

  const inner = pageShell("设置", "全局配置与系统信息");
  root.append(el("div", { class: "page" }, inner));

  // 压缩设置
  const compressChk = el("input", { type: "checkbox" });
  compressChk.checked = s.auto_compress !== false;
  const ratioInput = el("input", { class: "input", type: "number", min: "0.1", max: "0.95", step: "0.05", value: s.compression_ratio ?? 0.8, style: "width:120px;" });

  inner.append(el("div", { class: "section-title" }, "📦 上下文压缩"));
  inner.append(el("div", { class: "card" },
    el("div", { class: "form-row" },
      el("label", { class: "checkbox-row" }, compressChk, " 启用自动压缩（ReAct 循环内超阈值自动压缩）")),
    el("div", { class: "form-row" },
      el("label", null, "压缩阈值比例"),
      el("div", null, ratioInput),
      el("div", { class: "form-hint" }, "上下文使用达到该比例时触发压缩（0.8 = 80%）")),
    el("button", {
      class: "btn btn-primary btn-sm",
      onclick: async () => {
        try {
          await api.updateSettings({
            auto_compress: compressChk.checked,
            compression_ratio: parseFloat(ratioInput.value) || 0.8,
          });
          toast("设置已保存", "success");
        } catch (e) { toast(e.message, "error"); }
      },
    }, "保存设置")));

  // 系统信息
  inner.append(el("div", { class: "section-title" }, "ℹ️ 系统信息"));
  const infoRows = [
    ["版本", "v" + info.version],
    ["配置目录", info.config_dir],
    ["Agent 数量", String(info.agents_count)],
    ["模型数量", String(info.models_count)],
    ["当前 Agent", info.active_agent || "-"],
    ["当前模型", info.last_model || "-"],
  ];
  const infoCard = el("div", { class: "card" });
  for (const [k, v] of infoRows) {
    infoCard.append(el("div", {
      style: "display:flex;justify-content:space-between;padding:4px 0;font-size:13px;",
    }, el("span", { style: "color:var(--text-2);" }, k),
      el("span", { style: "font-family:var(--font-mono);" }, v)));
  }
  inner.append(infoCard);
}

/* ===================================================================
   17. 启动
   =================================================================== */

async function bootstrap() {
  initChatView();

  // 先加载基础数据，再初始化路由（避免直接以 #/skills 等 URL 打开时
  // 管理视图在 state.agents 为空的情况下渲染出"暂无 Agent"）
  try {
    const [agentData, modelData, info] = await Promise.all([
      api.getAgents(), api.getModels(), api.info(),
    ]);
    state.agents = agentData.agents || [];
    state.models = modelData.models || [];
    state.activeAgent = agentData.active_agent || state.agents[0]?.name || "";
    state.selectedModel = modelData.last_selected || state.models[0]?.name || "";
    $("#app-version").textContent = "v" + info.version;
  } catch (e) {
    toast("初始化失败: " + e.message, "error");
  }

  initRouter();
  refreshSelectors();
  await restoreMessages();
  refreshStatus();
  chatUI.input.focus();
}

document.addEventListener("DOMContentLoaded", bootstrap);
