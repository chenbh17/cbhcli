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

  // Harness 权限
  getPermissions: () => request("/permissions"),
  setPermissionMode: (mode) =>
    request("/permissions/mode", { method: "POST", body: JSON.stringify({ mode }) }),
  updatePermissionRule: (action, category, rule) =>
    request("/permissions/rules", { method: "POST", body: JSON.stringify({ action, category, rule }) }),

  // Harness 钩子
  getHooks: (agent) => request(`/hooks/${enc(agent)}`),
  reloadHooks: (agent) => request(`/hooks/${enc(agent)}/reload`, { method: "POST" }),

  // Harness 检查点回滚
  getBackups: (agent) => request(`/agents/${enc(agent)}/backups`),
  undoBackup: (agent, backup_id) =>
    request(`/agents/${enc(agent)}/undo`, { method: "POST", body: JSON.stringify({ backup_id: backup_id || null }) }),

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

  // 对话（v5.2.9：消息经 POST 发送立即返回，实时事件走 WebSocket）
  chatStream: (message, agent_name, model_name, images, session_id) =>
    request("/chat", { method: "POST", body: JSON.stringify({
      message, agent_name, model_name, images: images || [],
      session_id: session_id || "",
    }) }),
  chatRespond: (agent_name, model_name, response, session_id) =>
    request("/chat/respond", { method: "POST", body: JSON.stringify({ agent_name, model_name, response, session_id: session_id || "" }) }),
  chatReset: (agent_name, model_name, session_id) =>
    request("/chat/reset", { method: "POST", body: JSON.stringify({ agent_name, model_name, session_id: session_id || "" }) }),
  chatSwitchModel: (agent_name, old_model, new_model, session_id) =>
    request("/chat/switch_model", { method: "POST", body: JSON.stringify({ agent_name, old_model, new_model, session_id: session_id || "" }) }),
  chatAbort: (agent_name, model_name, session_id) =>
    request("/chat/abort", { method: "POST", body: JSON.stringify({ agent_name, model_name, session_id: session_id || "" }) }),
  chatStatus: (a, m, session_id) =>
    request(`/chat/status?agent_name=${enc(a)}&model_name=${enc(m)}${session_id ? `&session_id=${enc(session_id)}` : ""}`),
  chatMessages: (a, m, session_id) =>
    request(`/chat/messages?agent_name=${enc(a)}&model_name=${enc(m)}${session_id ? `&session_id=${enc(session_id)}` : ""}`),
  chatLoad: (agent_name, model_name, filename, workspace, session_id) =>
    request("/chat/load", { method: "POST", body: JSON.stringify({ agent_name, model_name, filename: filename || "", workspace: workspace || "", session_id: session_id || "" }) }),

  // 工作空间（v5.2.8：侧边栏会话按工作空间分组）
  workspaceInfo: (a) => request(`/workspace/info?agent_name=${enc(a)}`),
  workspaceBrowse: (p) => request(`/workspace/browse?path=${enc(p || "")}`),
  workspaceOpen: (a, m, path, resume) =>
    request("/workspace/open", { method: "POST", body: JSON.stringify({ agent_name: a, model_name: m, path, resume: !!resume }) }),
  workspaceClearSessions: (a, path) =>
    request("/workspace/sessions/clear", { method: "POST", body: JSON.stringify({ agent_name: a, path }) }),
  // 文件管理器（v5.2.8）
  filesList: (p) => request(`/files/list?path=${enc(p || "")}`),
  // 会话管理（v5.2.8：重命名/删除/复制）
  sessionRename: (a, s, title) =>
    request("/workspace/session/rename", { method: "POST", body: JSON.stringify({ agent_name: a, session_id: s.id, filename: s.filename, title }) }),
  sessionDelete: (a, s) =>
    request("/workspace/session/delete", { method: "POST", body: JSON.stringify({ agent_name: a, session_id: s.id, filename: s.filename }) }),
  sessionCopy: (a, s) =>
    request("/workspace/session/copy", { method: "POST", body: JSON.stringify({ agent_name: a, session_id: s.id, filename: s.filename }) }),
  // CLI 会话只读跟随轮询（问题6，v5.3.1+）
  cliSessionPoll: (a, session_id) =>
    request(`/cli_session/poll?agent_name=${enc(a)}&session_id=${enc(session_id)}`),
  chatCompress: (agent_name, model_name, instructions, session_id) =>
    request("/chat/compress", { method: "POST", body: JSON.stringify({ agent_name, model_name, instructions: instructions || "", session_id: session_id || "" }) }),
  chatUpload: (file, a, m) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("agent_name", a);
    fd.append("model_name", m);
    return fetch(`${BASE}/chat/upload`, { method: "POST", body: fd }).then(handleUploadResp);
  },

  // Agent 链条
  getChains: () => request("/chains"),
  createChain: (d) => request("/chains", { method: "POST", body: JSON.stringify(d) }),
  getChain: (n) => request(`/chains/${enc(n)}`),
  updateChain: (n, d) => request(`/chains/${enc(n)}`, { method: "PUT", body: JSON.stringify(d) }),
  deleteChain: (n) => request(`/chains/${enc(n)}`, { method: "DELETE" }),
  useChain: (a, m, chain_name, session_id) =>
    request("/chat/use-chain", { method: "POST", body: JSON.stringify({ agent_name: a, model_name: m, chain_name, session_id: session_id || "" }) }),
  offChain: (a, m, session_id) =>
    request("/chat/off-chain", { method: "POST", body: JSON.stringify({ agent_name: a, model_name: m, session_id: session_id || "" }) }),
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

/* ---- LaTeX 公式渲染（KaTeX，v5.1.9）：渲染正文 $/$$/\[/\( 公式，代码块一律保护 ---- */
function renderTex(tex, display) {
  if (!window.katex) return escapeHtml((display ? "$$" : "$") + tex + (display ? "$$" : "$"));
  try {
    return katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      strict: false,
      output: "html"
    });
  } catch {
    return escapeHtml((display ? "$$" : "$") + tex + (display ? "$$" : "$"));
  }
}

// 单遍扫描：保护代码块/行内代码，把公式抽成占位符（避免 marked 破坏 _ * 等符号）
function extractMath(text, maths) {
  const re =
    /(```[\s\S]*?```|~~~[\s\S]*?~~~)|(`[^`\n]*`)|(\$\$[\s\S]+?\$\$)|(\\\[[\s\S]+?\\\])|(\$[^\s$`][^$\n]*?[^\s$`]\$|\$[^\s$`]\$)|(\\\(.+?\\\))/g;
  return text.replace(re, (m, fence, inlineCode, dispA, dispB, inlA, inlB) => {
    if (fence !== undefined || inlineCode !== undefined) return m; // 代码原样保留
    let tex = "", display = false;
    if (dispA !== undefined) { tex = dispA.slice(2, -2); display = true; }
    else if (dispB !== undefined) { tex = dispB.slice(2, -2); display = true; }
    else if (inlA !== undefined) { tex = inlA.slice(1, -1); display = false; }
    else if (inlB !== undefined) { tex = inlB.slice(2, -2); display = false; }
    else return m;
    maths.push({ tex: tex.trim(), display });
    const ph = `@@CBHMATH${maths.length - 1}@@`;
    return display ? `\n\n${ph}\n\n` : ph;
  });
}

function renderMd(text) {
  if (!text) return "";
  if (!window.marked) return escapeHtml(text);
  try {
    const maths = [];
    const prepared = extractMath(text, maths);
    let html = marked.parse(prepared);
    if (maths.length) {
      html = html.replace(/(<p>)?@@CBHMATH(\d+)@@(<\/p>)?/g, (m, _p, idx) => {
        const seg = maths[Number(idx)];
        if (!seg) return m;
        const rendered = renderTex(seg.tex, seg.display);
        return seg.display
          ? `<div class="cbh-math-block">${rendered}</div>`
          : `<span class="cbh-math-inline">${rendered}</span>`;
      });
    }
    return sanitizeHtml(html);
  } catch {
    return escapeHtml(text);
  }
}

/* ---- 图片灯箱预览（v5.3.1+ 问题2）：点击图片放大显示，右键仍可原生保存 ---- */
function openLightbox(src, opts = {}) {
  if (!src) return;
  closeLightbox();
  const mask = el("div", { class: "lightbox-mask" });
  const img = el("img", { class: "lightbox-img", src, alt: opts.filename || "图片" });
  const bar = el("div", { class: "lightbox-bar" });
  if (opts.filename) bar.append(el("span", { class: "lightbox-name", title: opts.filename }, opts.filename));
  const dlUrl = opts.downloadUrl || src;
  bar.append(
    el("a", { class: "btn btn-sm lightbox-btn", href: dlUrl,
              download: opts.filename || "", title: "下载图片" }, "⬇ 下载"),
    el("button", { class: "btn btn-sm lightbox-btn lightbox-close", type: "button" }, "✕ 关闭"));
  mask.append(el("div", { class: "lightbox-box" }, bar, img));
  document.body.append(mask);
  const onKey = (e) => { if (e.key === "Escape") closeLightbox(); };
  mask._onKey = onKey;
  document.addEventListener("keydown", onKey);
  mask.addEventListener("click", (e) => { if (e.target === mask) closeLightbox(); });
  mask.querySelector(".lightbox-close").addEventListener("click", closeLightbox);
  // 点击图片本身不关闭（便于查看细节），右键保留浏览器原生菜单（另存为）
  img.addEventListener("click", (e) => e.stopPropagation());
  requestAnimationFrame(() => mask.classList.add("show"));
}

function closeLightbox() {
  const mask = document.querySelector(".lightbox-mask");
  if (!mask) return;
  if (mask._onKey) document.removeEventListener("keydown", mask._onKey);
  mask.remove();
}

// 全局点击代理：聊天区内的用户图片 / AI 展示图片 / Markdown 内嵌图片一律灯箱预览
document.addEventListener("click", (e) => {
  const img = e.target && e.target.closest ? e.target.closest("img") : null;
  if (!img || !img.closest("#chat-messages")) return;
  if (!img.matches(".msg-user-img, .ai-display-img, .md-content img")) return;
  e.preventDefault();
  openLightbox(img.src, {
    filename: img.alt || "",
    downloadUrl: img.dataset.downloadUrl || "",
  });
});

/* ---- Mermaid / ECharts 图表渲染（v5.2.0）----
 * 流式过程中 ```mermaid / ```echarts 代码块按代码显示，回复完成（done）或恢复历史时
 * 调用 renderDiagrams 将代码块原地替换为 SVG / ECharts 图表。渲染失败一律保留代码块。
 * 纯前端离线渲染（vendor/mermaid + vendor/echarts），无 Python 依赖、无需联网。 */
let _mermaidInited = false;
let _diagSeq = 0;
const _mermaidSvgCache = new Map(); // 源码 -> svg（避免历史/重复渲染时重复计算）
const _diagLibLoading = {};         // 懒加载 Promise 缓存

// 静态资源版本号：从已加载的 app.js 的 ?v= 参数推导，保证缓存失效一致
function _staticVer() {
  const s = document.querySelector('script[src*="/js/app.js"]');
  const m = s && s.src.match(/[?&]v=([^&]+)/);
  return m ? "?v=" + m[1] : "";
}

// 懒加载图表库（检测到图表块才注入脚本，避免拖慢首屏）
function loadDiagLib(name) {
  if (_diagLibLoading[name]) return _diagLibLoading[name];
  const ready = name === "mermaid" ? window.mermaid : window.echarts;
  if (ready) return (_diagLibLoading[name] = Promise.resolve());
  const src = name === "mermaid"
    ? "/vendor/mermaid/mermaid.min.js" + _staticVer()
    : "/vendor/echarts/echarts.min.js" + _staticVer();
  _diagLibLoading[name] = new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => resolve();
    s.onerror = () => { console.error(`加载 ${name} 失败:`, src); resolve(); };
    document.head.appendChild(s);
  });
  return _diagLibLoading[name];
}

function ensureMermaidInit() {
  if (_mermaidInited || !window.mermaid) return;
  try {
    window.mermaid.initialize({
      startOnLoad: false,
      theme: "dark",
      securityLevel: "strict", // 内置 DOMPurify 消毒，防 XSS
      logLevel: "fatal"
    });
    _mermaidInited = true;
  } catch (e) {
    console.error("mermaid initialize 失败:", e);
  }
}

// 解析 echarts option：JSON 优先 → JS 求值兜底（支持函数/尾逗号）→ "option = {...}" 形式
function parseEchartsOption(src) {
  let s = String(src).trim().replace(/;+\s*$/, ""); // 去尾部多余分号
  try { return JSON.parse(s); } catch (_) {}
  try { return new Function("return (" + s + ")")(); } catch (_) {}
  const m = s.match(/^(?:var|let|const)?\s*[A-Za-z_$][\w$]*\s*=\s*([\s\S]+)$/);
  if (m) {
    try { return new Function("return (" + m[1] + ")")(); } catch (_) {}
  }
  return null;
}

// 判断解析结果是否像 echarts option（含 series，echarts 的强特征）
function looksLikeEcharts(opt) {
  return !!opt && typeof opt === "object" && !Array.isArray(opt) && "series" in opt &&
    (Array.isArray(opt.series) || (opt.series && typeof opt.series === "object"));
}

// 收集 echarts 代码块：显式 echarts/echart 标签，或 json/javascript/js 标签且内容像 echarts option
function collectEchartsBlocks(container) {
  const blocks = [];
  container.querySelectorAll("pre code").forEach(code => {
    const pre = code.closest("pre");
    if (!pre || pre.dataset.diagDone) return;
    const m = (code.className || "").match(/language-([\w+#-]+)/);
    const lang = m ? m[1].toLowerCase() : "";
    const src = (code.textContent || "").trim();
    if (!src) return;
    if (lang === "echarts" || lang === "echart") {
      blocks.push({ pre, src });
    } else if (lang === "json" || lang === "javascript" || lang === "js") {
      if (looksLikeEcharts(parseEchartsOption(src))) blocks.push({ pre, src });
    }
  });
  return blocks;
}

// mermaid 安全渲染：清理残留临时元素 + 失败重试一次，成功结果按源码缓存
async function renderMermaidSafe(src) {
  if (_mermaidSvgCache.has(src)) return _mermaidSvgCache.get(src);
  const id = "cbh-mmd-" + (++_diagSeq);
  let lastErr = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      document.getElementById(id)?.remove();
      document.getElementById("d" + id)?.remove();
      const out = await window.mermaid.render(id, src);
      if (out && out.svg) {
        _mermaidSvgCache.set(src, out.svg);
        return out.svg;
      }
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error("mermaid render 无输出");
}

// 构建「图片 / 代码」切换包装器；onShowImg 在切回图片视图时回调（echarts 需 resize）
function buildDiagramWrap(src, onShowImg) {
  const wrap = el("div", { class: "cbh-diagram-wrap" });
  const btnImg = el("button", { class: "cbh-diagram-tab active", type: "button" }, "图片");
  const btnCode = el("button", { class: "cbh-diagram-tab", type: "button" }, "代码");
  const toolbar = el("div", { class: "cbh-diagram-toolbar" }, btnImg, btnCode);

  const imgView = el("div", { class: "cbh-diagram-view cbh-diagram-img" });
  const codeView = el("div", { class: "cbh-diagram-view cbh-diagram-code" });
  const codeEl = el("code");
  codeEl.textContent = src;
  const copyBtn = el("button", { class: "code-copy-btn", type: "button" }, "复制");
  copyBtn.addEventListener("click", () => {
    copyText(src)
      .then(() => { copyBtn.textContent = "已复制"; setTimeout(() => (copyBtn.textContent = "复制"), 1200); })
      .catch(() => { copyBtn.textContent = "复制失败"; setTimeout(() => (copyBtn.textContent = "复制"), 1200); });
  });
  const preEl = el("pre", null, codeEl, copyBtn);
  codeView.append(preEl);
  codeView.style.display = "none";

  wrap.append(toolbar, imgView, codeView);
  const show = (img) => {
    imgView.style.display = img ? "" : "none";
    codeView.style.display = img ? "none" : "";
    btnImg.classList.toggle("active", img);
    btnCode.classList.toggle("active", !img);
    if (img && onShowImg) { try { onShowImg(); } catch (_) {} }
  };
  btnImg.addEventListener("click", () => show(true));
  btnCode.addEventListener("click", () => show(false));
  return { wrap, imgView };
}

async function renderDiagrams(container) {
  if (!container) return;

  // ---- mermaid ----
  const mmdBlocks = Array.from(container.querySelectorAll("pre code.language-mermaid"))
    .map(code => ({ pre: code.closest("pre"), src: (code.textContent || "").trim() }))
    .filter(b => b.pre && !b.pre.dataset.diagDone && b.src);
  if (mmdBlocks.length) await loadDiagLib("mermaid");
  if (mmdBlocks.length && window.mermaid) {
    ensureMermaidInit();
    for (const { pre, src } of mmdBlocks) {
      if (!pre.isConnected) continue;
      pre.dataset.diagDone = "1";
      try {
        const svg = await renderMermaidSafe(src);
        const { wrap, imgView } = buildDiagramWrap(src);
        imgView.classList.add("cbh-mermaid");
        imgView.innerHTML = svg;
        pre.replaceWith(wrap);
      } catch (e) {
        console.warn("mermaid 渲染失败，保留代码块:", e);
      }
    }
  }

  // ---- echarts ----
  const ecBlocks = collectEchartsBlocks(container);
  if (ecBlocks.length) await loadDiagLib("echarts");
  if (ecBlocks.length && window.echarts) {
    for (const { pre, src } of ecBlocks) {
      if (!pre.isConnected) continue;
      pre.dataset.diagDone = "1";
      const option = parseEchartsOption(src);
      if (!looksLikeEcharts(option)) {
        console.warn("echarts option 解析失败，保留代码块");
        continue;
      }
      try {
        let chart = null;
        const { wrap, imgView } = buildDiagramWrap(src, () => { if (chart) chart.resize(); });
        const box = el("div", { class: "cbh-echarts" });
        imgView.append(box);
        pre.replaceWith(wrap);
        chart = window.echarts.init(box, "dark");
        chart.setOption(option);
        if (window.ResizeObserver) {
          const ro = new ResizeObserver(() => { try { chart.resize(); } catch (_) {} });
          ro.observe(box);
        }
      } catch (e) {
        console.warn("echarts 渲染失败:", e);
      }
    }
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
  activeChain: null,  // 当前激活的链条名称
  currentWorkspace: "",   // 当前打开的工作空间目录（v5.2.8）
  wsExpanded: {},         // 工作空间展开状态 {path: bool}（v5.2.8）
  wsShowAll: {},          // 工作空间"展开其余N个会话"状态（v5.2.8）
  wsFilter: "",           // 会话搜索过滤词（v5.2.8）
  currentSessionId: null, // 当前后端会话 id（服务器重启后自动找回用，v5.2.8）
  fmPath: "",             // 文件管理器当前浏览目录（空=工作空间根，v5.2.8）
  fmWorkspace: "",        // 文件管理器对应的的工作空间（切换时重置，v5.2.8）
  cliFollow: "",          // CLI 只读跟随视图的会话 id（问题6，v5.3.1+）
  cliFollowTimer: null,   // CLI 跟随轮询定时器
};

/* ---- 面板布局常量（v5.2.8：工作区/文件管理器左右互换 + 拖拽调宽） ---- */
const PANEL_MIN_W = 170, PANEL_MAX_W = 520;
const LS_FM_SIDE = "cbhcli.fmSide";       // 文件管理器在哪侧: left/right
const LS_WS_WIDTH = "cbhcli.wsPanelW";    // 工作区面板宽度
const LS_FM_WIDTH = "cbhcli.fmPanelW";    // 文件管理器面板宽度

function currentAgent() { return state.activeAgent; }
function currentModel() { return state.selectedModel; }

/* ===================================================================
   4. 路由
   =================================================================== */

const VIEW_LOADERS = {
  chat: null,
  agents: loadAgentsView,
  chains: loadChainsView,
  models: loadModelsView,
  fallback: loadFallbackView,
  skills: loadSkillsView,
  mcp: loadMCPView,
  knowledge: loadKnowledgeView,
  tools: loadToolsView,
  security: loadSecurityView,
  embedding: loadEmbeddingView,
  history: loadHistoryView,
  settings: loadSettingsView,
};

function switchView(name) {
  if (!VIEW_LOADERS.hasOwnProperty(name)) name = "chat";
  state.currentView = name;
  $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === name));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
  const settingsBtn = $("#btn-settings");
  if (settingsBtn) settingsBtn.classList.toggle("active", name === "settings");
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
  call_agent: "🔗", send_file: "📎",
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
  chatUI.modeBadge = $("#mode-badge");
  chatUI.modeBadgeText = $("#mode-badge-text");

  chatUI.agentSelect.addEventListener("change", onAgentChange);
  chatUI.modelSelect.addEventListener("change", onModelChange);
  chatUI.sendBtn.addEventListener("click", sendMessage);
  chatUI.abortBtn.addEventListener("click", abortStream);
  chatUI.attachBtn.addEventListener("click", () => chatUI.fileInput.click());
  chatUI.fileInput.addEventListener("change", handleFiles);
  $("#btn-new-session").addEventListener("click", newSession);
  $("#btn-compress").addEventListener("click", manualCompress);
  $("#btn-undo").addEventListener("click", showUndoModal);
  $("#btn-quick-tools").addEventListener("click", showQuickTools);
  $("#btn-quick-skills").addEventListener("click", showQuickSkills);
  $("#btn-quick-model").addEventListener("click", showQuickModel);
  chatUI.modeBadge.addEventListener("click", cyclePermissionMode);
  loadPermissionMode();

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

/* ---- 权限模式徽标（Harness 治理层） ---- */
const MODE_ORDER = ["readonly", "standard", "auto", "yolo"];

async function loadPermissionMode() {
  try {
    const p = await api.getPermissions();
    renderModeBadge(p.mode, p.modes);
  } catch { /* 权限引擎不可用时静默 */ }
}

function renderModeBadge(mode, modes) {
  if (!chatUI.modeBadge) return;
  const meta = (modes || []).find(m => m.id === mode);
  chatUI.modeBadgeText.textContent = meta ? `${meta.icon} ${meta.label}` : mode;
  chatUI.modeBadge.dataset.mode = mode;
  chatUI.modeBadge.title = meta ? `权限模式: ${meta.desc}（点击切换）` : "权限模式";
}

async function cyclePermissionMode() {
  try {
    const p = await api.getPermissions();
    const next = MODE_ORDER[(MODE_ORDER.indexOf(p.mode) + 1) % MODE_ORDER.length];
    if (next === "yolo") {
      const ok = await confirmDialog(
        "开启 YOLO 模式?",
        "YOLO 模式将无确认执行一切操作（含 rm / git push 等），deny 红线降级为警告。\n\n确认开启？",
        { danger: true, okText: "开启 YOLO" });
      if (!ok) return;
    }
    const r = await api.setPermissionMode(next);
    renderModeBadge(r.mode, r.modes);
    const meta = (r.modes || []).find(m => m.id === r.mode);
    toast(`权限模式: ${meta ? meta.icon + " " + meta.label : r.mode}`,
          r.mode === "yolo" ? "warn" : "success");
  } catch (e) {
    toast(e.message, "error");
  }
}

async function onAgentChange() {
  const name = chatUI.agentSelect.value;
  state.activeAgent = name;
  // 切换 Agent 时清除前端链条状态（等待后端 refreshStatus 重新同步）
  state.activeChain = null;
  updateChainIndicator();
  api.selectAgent(name).catch(() => {});
  liveRunReset();
  wsSubscribe(null);   // 退订旧会话（restoreMessages 会订阅新 Agent 的会话）
  clearMessages();
  await restoreMessages();
  await refreshStatus();
  updateChainIndicator();
  refreshWorkspaces();
}

async function onModelChange() {
  const name = chatUI.modelSelect.value;
  const old = state.selectedModel;
  if (name === old) return;
  state.selectedModel = name;
  try {
    // 原地切换模型：保留当前会话及上下文（对齐 CLI /model use）
    const r = await api.chatSwitchModel(currentAgent(), old, name, state.currentSessionId);
    toast(r.message || `已切换到模型 '${name}'`, "success");
  } catch (e) {
    state.selectedModel = old;
    chatUI.modelSelect.value = old;
    toast(e.message, "error");
    return;
  }
  // 会话消息未变，无需清空重渲染；仅刷新上下文用量（新模型限额可能不同）
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
    const s = await api.chatStatus(a, m, state.currentSessionId);
    updateCtxMeter(s);
    if (s.workspace) state.currentWorkspace = s.workspace;
    const cwdBar = $("#cwd-bar");
    if (cwdBar) {
      $("#cwd-bar-text").textContent = s.cwd || "";
      cwdBar.title = s.cwd || "";
    }
    // 同步链条激活状态（会话可能在后端已恢复链条）
    if (s.active_chain !== undefined && s.active_chain !== state.activeChain) {
      state.activeChain = s.active_chain || null;
      updateChainIndicator();
    }
    // v5.2.9：按当前订阅会话跟踪状态；后台运行中同步流式状态
    if (s.active) {
      if (s.session_id && s.session_id !== state.currentSessionId) {
        setSessionId(s.session_id);
      }
      if (s.run_active && !state.streaming) {
        setStreaming(true);
        ensureLiveTurn();
      } else if (!s.run_active && state.streaming && !liveRun.active) {
        // v5.2.9 兜底：当前会话未在运行却残留流式状态（如从其他运行中
        // 会话切走后）-> 复位，中断按钮只在当前会话运行中显示
        setStreaming(false);
      }
      // 确保已订阅当前会话（页面刷新/重连后补订阅）
      if (state.currentSessionId && ws.connected
          && ws.sessionSub !== state.currentSessionId) {
        wsSubscribe(state.currentSessionId, ws.lastSeq);
      }
    } else if (!state.streaming && state.currentSessionId
               && !state.cliFollow   // CLI 跟随视图：会话在 CLI 进程，Web 内存本就不存在，属正常
               && $(".msg-column", chatUI.messages)) {
      // 后端会话丢失（服务器重启/被驱逐）但页面仍有对话内容：按 id 找回
      const sid = state.currentSessionId;
      state.currentSessionId = null;  // 先清空，恢复失败时不重试
      restoreLostSession(sid);
    }
  } catch { /* 忽略 */ }
}

/* 后端会话丢失（服务器重启）后按会话 id 自动恢复 */
async function restoreLostSession(sessionId) {
  const a = currentAgent(), m = currentModel();
  if (!a || !m) return;
  try {
    const r = await api.chatLoad(a, m, "", "", sessionId);
    setSessionId(r.session_id || sessionId);
    if (r.workspace) state.currentWorkspace = r.workspace;
    liveRunReset();
    clearMessages();
    renderRestoredMessages(r.messages || []);
    if (r.usage) updateCtxMeter(r.usage);
    // v5.3.1+（问题6）：找回的会话正由 CLI 运行 → 进入只读跟随视图
    if (r.cli_running) { enterCliFollow(r.session_id || sessionId); return; }
    wsSubscribe(r.session_id || sessionId, r.run_active ? 0 : (r.run_seq || 0));
    toast("检测到服务重启，会话已自动恢复", "info");
    refreshWorkspaces();
  } catch {
    // 会话从未落盘（未完成过完整对话轮），无法恢复，保持当前展示
  }
}

/* 记录当前会话 id（并持久化，刷新页面后重新打开同一会话） */
function setSessionId(id) {
  state.currentSessionId = id || null;
  try {
    if (id) localStorage.setItem("cbhcli.lastSession." + currentAgent(), id);
    else localStorage.removeItem("cbhcli.lastSession." + currentAgent());
  } catch (_) {}
}

/* 重新拉取当前会话消息并重渲染（WS 事件日志被裁剪时的 resync 兜底） */
async function refreshCurrentSession() {
  const a = currentAgent(), m = currentModel();
  if (!a || !m || !state.currentSessionId) return;
  try {
    const { messages } = await api.chatMessages(a, m, state.currentSessionId);
    liveRunReset();
    clearMessages();
    renderRestoredMessages(messages || []);
  } catch (_) {}
}

/* ===================================================================
   CLI 会话只读跟随视图（v5.3.1+ 问题6）
   打开正在 CLI 中运行的会话时：禁用输入，每 3 秒轮询磁盘会话文件
   （CLI 每轮结束自动落盘 + 心跳上报状态），轮级实时刷新消息。
   CLI 结束后自动接管会话（载入 Web 内存），用户可无缝续接对话。
   =================================================================== */

function setComposerDisabled(disabled, hint) {
  chatUI.input.disabled = disabled;
  chatUI.sendBtn.disabled = disabled;
  if (disabled) chatUI.hint.textContent = hint || "";
  else if (!state.streaming) chatUI.hint.textContent = "";
}

function showCliFollowBanner(title) {
  hideCliFollowBanner();
  const banner = el("div", { class: "cli-follow-banner", id: "cli-follow-banner" },
    el("span", { class: "cli-follow-dot" }, "●"),
    el("span", null, "该会话正在 CLI 中运行 · 只读跟随视图（每轮自动刷新）"));
  msgColumn().append(banner);
  scrollBottom();
}

function hideCliFollowBanner() {
  document.getElementById("cli-follow-banner")?.remove();
}

function enterCliFollow(sid) {
  state.cliFollow = sid;
  setComposerDisabled(true, "该会话正在 CLI 中运行（只读跟随视图）");
  showCliFollowBanner();
  clearInterval(state.cliFollowTimer);
  state.cliFollowTimer = setInterval(pollCliSession, 3000);
}

function stopCliFollow() {
  clearInterval(state.cliFollowTimer);
  state.cliFollowTimer = null;
  state.cliFollow = "";
  hideCliFollowBanner();
  setComposerDisabled(false);
}

async function pollCliSession() {
  const sid = state.cliFollow;
  if (!sid) { stopCliFollow(); return; }
  try {
    const r = await api.cliSessionPoll(currentAgent(), sid);
    if (state.cliFollow !== sid) return;
    liveRunReset();
    clearMessages();
    renderRestoredMessages(r.messages || []);
    if (!r.cli_running) {
      // CLI 已结束：接管会话（从磁盘载入 Web 内存），可继续对话
      stopCliFollow();
      try {
        const lr = await api.chatLoad(currentAgent(), currentModel(), "", "", sid);
        if (lr && lr.session_id === sid && state.currentSessionId === sid) {
          liveRunReset();
          clearMessages();
          renderRestoredMessages(lr.messages || []);
          if (lr.usage) updateCtxMeter(lr.usage);
          wsSubscribe(sid, lr.run_seq || 0);
          toast("CLI 会话已结束，可在 Web 继续对话", "success");
        }
      } catch (_) {}
      refreshStatus();
      refreshWorkspaces();
      return;
    }
    showCliFollowBanner();  // clearMessages 后恢复横幅
  } catch (_) {}
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
    if (item.kind === "file") {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) {
        const fname = file.name || `paste_${Date.now()}.${file.type.split("/")[1] || "bin"}`;
        await uploadAttachment(new File([file], fname, { type: file.type }));
      }
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
  if (state.cliFollow) { toast("该会话正在 CLI 中运行，请等待结束", "warn"); return; }
  if (state.streaming) return;
  if (!text && state.attachments.length === 0) return;

  // v5.2.9：实时事件走 WebSocket，发送前确保通道就绪
  if (!(await ensureWsReady())) {
    toast("实时通道未就绪，请稍后重试", "warn");
    return;
  }

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
  const imageAtts = state.attachments.filter(f => f.is_image);
  const fileAtts = state.attachments.filter(f => !f.is_image);
  const bubbleChildren = [];
  // 显示图片缩略图
  if (imageAtts.length) {
    const imgGrid = el("div", { class: "msg-user-images" });
    for (const img of imageAtts) {
      const src = img.base64 || img.url;
      if (src) {
        // v5.3.1+：点击放大预览（全局灯箱代理），右键原生下载
        const imgEl = el("img", { src, alt: img.filename, class: "msg-user-img", title: img.filename,
                                  "data-download-url": img.download_url || img.url || "" });
        imgGrid.append(imgEl);
      }
    }
    bubbleChildren.push(imgGrid);
  }
  // 显示文件附件（可下载）
  if (fileAtts.length) {
    const fileList = el("div", { class: "msg-user-files" });
    for (const f of fileAtts) {
      const dlUrl = f.download_url || `/api/files/download/${f.filename}`;
      const link = el("a", { href: dlUrl, download: f.filename, class: "file-download-link" },
        el("span", { class: "file-icon" }, "📄"),
        el("span", null, f.filename),
        el("span", { class: "file-size" }, fmtSize(f.size)));
      fileList.append(link);
    }
    bubbleChildren.push(fileList);
  }
  bubbleChildren.push(el("div", { class: "msg-user-text" }, userContent));
  const bubble = el("div", { class: "msg-user" },
    el("div", { class: "msg-user-bubble" }, ...bubbleChildren));
  col.append(bubble);

  chatUI.input.value = "";
  autoGrow();
  state.attachments = [];
  renderAttachments();
  scrollBottom();

  // v5.2.9：POST 发送立即返回，实时事件经 WebSocket 到达（多浏览器一致）。
  // 用户气泡已在本地渲染；run_start 事件到达时因 sentLocally 跳过重复渲染。
  liveRunReset();
  liveRun.sentLocally = true;
  ensureLiveTurn();
  try {
    const r = await api.chatStream(userContent, a, m, images, state.currentSessionId);
    if (r && r.session_id) {
      if (r.session_id !== state.currentSessionId) setSessionId(r.session_id);
      if (ws.sessionSub !== r.session_id) wsSubscribe(r.session_id, 0);
    }
  } catch (e) {
    liveRun.sentLocally = false;
    endLiveTurn();
    toast(`发送失败: ${e.message}`, "error");
  }
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
    await api.chatAbort(currentAgent(), currentModel(), state.currentSessionId);
  } catch (e) { toast(e.message, "error"); }
}

/* ===================================================================
   WebSocket 实时通道 + 当前运行渲染状态（v5.2.9）
   - 事件经 /ws 订阅推送（与发送端解耦，多浏览器画面一致）
   - 会话切换/新建后旧会话后台继续运行，侧边栏实时显示状态
   =================================================================== */

const ws = {
  sock: null, connected: false, sessionSub: "", lastSeq: 0,
  retryTimer: null, retryDelay: 1000, pingTimer: null,
};

function wsSend(obj) {
  try { if (ws.sock && ws.sock.readyState === 1) ws.sock.send(JSON.stringify(obj)); } catch (_) {}
}

/** 订阅会话（同会话断线重连从 lastSeq 续订；新会话从 0 回放当前运行） */
function wsSubscribe(sessionId, sinceSeq) {
  if (!sessionId) { ws.sessionSub = ""; wsSend({ type: "unsubscribe" }); return; }
  const since = (sinceSeq === undefined || sinceSeq === null)
    ? (ws.sessionSub === sessionId ? ws.lastSeq : 0)
    : sinceSeq;
  ws.sessionSub = sessionId;
  wsSend({ type: "subscribe", session_id: sessionId, since_seq: since });
}

function wsConnect() {
  if (ws.sock && (ws.sock.readyState === 0 || ws.sock.readyState === 1)) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  try { ws.sock = new WebSocket(`${proto}://${location.host}/ws`); }
  catch (e) { wsScheduleReconnect(); return; }
  ws.sock.onopen = () => {
    ws.connected = true;
    ws.retryDelay = 1000;
    if (ws.sessionSub) wsSubscribe(ws.sessionSub, ws.lastSeq);
    ws.pingTimer = setInterval(() => wsSend({ type: "ping" }), 30000);
  };
  ws.sock.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (msg.type === "event" && msg.data) {
      if (msg.data.seq) ws.lastSeq = msg.data.seq;
      handleLiveEvent(msg.data);
    } else if (msg.type === "subscribed") {
      handleSubscribed(msg);
    } else if (msg.type === "notice" && msg.data) {
      handleWsNotice(msg.data);
    }
  };
  ws.sock.onclose = () => {
    ws.connected = false;
    clearInterval(ws.pingTimer);
    wsScheduleReconnect();
  };
  ws.sock.onerror = () => { try { ws.sock.close(); } catch (_) {} };
}

function wsScheduleReconnect() {
  clearTimeout(ws.retryTimer);
  ws.retryTimer = setTimeout(() => {
    ws.retryDelay = Math.min(ws.retryDelay * 1.5, 8000);
    wsConnect();
  }, ws.retryDelay);
}

/** 发送前确保 WS 就绪（最多等待约 5 秒） */
function ensureWsReady() {
  if (ws.connected && ws.sock && ws.sock.readyState === 1) return Promise.resolve(true);
  wsConnect();
  return new Promise((resolve) => {
    const t0 = Date.now();
    const timer = setInterval(() => {
      if (ws.connected && ws.sock && ws.sock.readyState === 1) {
        clearInterval(timer); resolve(true);
      } else if (Date.now() - t0 > 5000) {
        clearInterval(timer); resolve(false);
      }
    }, 200);
  });
}

/* ---- 订阅回执 ---- */
function handleSubscribed(msg) {
  if (msg.error) {
    // 会话不存在（服务器重启等）：清除订阅，等待状态轮询自动恢复
    if (ws.sessionSub === msg.session_id) { ws.sessionSub = ""; ws.lastSeq = 0; }
    return;
  }
  if (msg.resync) {
    // v5.3.1+（问题5）：续订游标落在聚合事件块中间（断线重连等），
    // 部分回放会重复渲染 → 全量重载会话后从头回放当前轮
    fullResyncSession(msg.session_id);
    return;
  }
}

/** 全量重载当前会话（resync 兜底：重拉消息 + 运行中则从头回放事件） */
async function fullResyncSession(sid) {
  const a = currentAgent(), m = currentModel();
  if (!a || !m || !sid || sid !== state.currentSessionId) return;
  try {
    const r = await api.chatLoad(a, m, "", "", sid);
    if (!r || r.session_id !== sid || sid !== state.currentSessionId) return;
    liveRunReset();
    clearMessages();
    renderRestoredMessages(r.messages || []);
    if (r.usage) updateCtxMeter(r.usage);
    wsSubscribe(sid, r.run_active ? 0 : (r.run_seq || 0));
    if (r.run_active) { setStreaming(true); ensureLiveTurn(); }
  } catch (_) {}
}

/* ---- 全局通知（会话运行状态/列表变化，多浏览器侧边栏同步） ---- */
function handleWsNotice(d) {
  if (d.type === "session_status") {
    scheduleWorkspacesRefresh();
    if (d.session_id && d.session_id === state.currentSessionId) {
      if (d.status === "running") {
        if (!state.streaming) setStreaming(true);
        ensureLiveTurn();
      } else if (!liveRun.active) {
        setStreaming(false);
      }
    }
  } else if (d.type === "sessions_changed") {
    scheduleWorkspacesRefresh();
  }
}

let _wsRefreshTimer = null;
function scheduleWorkspacesRefresh() {
  clearTimeout(_wsRefreshTimer);
  _wsRefreshTimer = setTimeout(() => refreshWorkspaces(), 600);
}

/* ---- 当前运行渲染状态（v5.2.9：事件驱动，多浏览器一致） ---- */
const liveRun = {
  active: false,        // 当前订阅会话是否处于一轮运行的渲染中
  sentLocally: false,   // 本浏览器发送的消息（run_start 不重复渲染用户气泡）
  aiBody: null,         // 本轮 AI 消息容器（首个内容事件时惰性创建）
  curReasoning: null, curContent: null, lastToolCard: null,
  toolCards: new Map(),
  pendingRespondEl: null,  // 待应答 UI（确认卡/提问卡），其他浏览器应答后撤销
};

function liveRunReset() {
  liveRun.active = false;
  liveRun.sentLocally = false;
  liveRun.aiBody = null;
  liveRun.curReasoning = null;
  liveRun.curContent = null;
  liveRun.lastToolCard = null;
  liveRun.toolCards = new Map();
  liveRun.pendingRespondEl = null;
  // v5.2.9 修复：切换/加载/新建会话时复位流式状态，中断按钮仅当前会话
  // 运行中显示（旧版在别的浏览器运行会话时切走后中断按钮残留）
  setStreaming(false);
}

/** 本轮 AI 消息容器（惰性创建：用户气泡先于 AI 内容，保证顺序正确） */
function getAiBody() {
  if (liveRun.aiBody) return liveRun.aiBody;
  const col = msgColumn();
  liveRun.aiBody = el("div", { class: "msg-ai-body" });
  col.append(el("div", { class: "msg-ai" },
    el("div", { class: "msg-ai-avatar" }, "❯"), liveRun.aiBody));
  scrollBottom();
  return liveRun.aiBody;
}

/** 标记一轮运行开始（本浏览器发送或收到 run_start 事件时调用） */
function ensureLiveTurn() {
  if (liveRun.active) return;
  liveRun.active = true;
  setStreaming(true);
}

/** 一轮运行结束（run_end 事件收尾） */
function endLiveTurn(usage) {
  const hadBody = !!liveRun.aiBody;
  closeLiveReasoning();
  closeLiveContent();
  liveRun.active = false;
  liveRun.sentLocally = false;
  setStreaming(false);
  if (usage) updateCtxMeter(usage);
  if (hadBody) void renderDiagrams(liveRun.aiBody);
  refreshStatus();
  refreshWorkspaces();
  scrollBottom();
  chatUI.input.focus();
}

function closeLiveReasoning() {
  if (liveRun.curReasoning) {
    liveRun.curReasoning.blockEl.querySelector(".thinking-live-dot")?.remove();
    // 思考结束后自动折叠，保持对话整洁（点击标题可重新展开）
    liveRun.curReasoning.blockEl.classList.remove("open");
    liveRun.curReasoning = null;
  }
}

function closeLiveContent() { liveRun.curContent = null; }

/** 其他浏览器发送消息时渲染用户气泡（run_start.message） */
function renderIncomingUserMessage(text) {
  const col = msgColumn();
  col.append(el("div", { class: "msg-user" },
    el("div", { class: "msg-user-bubble" },
      el("div", { class: "msg-user-text" }, text || ""))));
  scrollBottom();
}

/** 其他浏览器已应答：撤销本浏览器的待确认/待回答 UI */
function dismissPendingRespond(response) {
  const elx = liveRun.pendingRespondEl;
  liveRun.pendingRespondEl = null;
  if (!elx || !elx.isConnected) return;
  elx.replaceWith(el("div", { class: "sys-event" },
    el("span", { class: "icon" }, "💬"),
    el("span", null, `已在其他窗口应答: ${response || ""}`)));
}

/* ---- 实时事件入口（run_start/run_end/responded + 各类渲染事件） ---- */
function handleLiveEvent(d) {
  const t = d.type;
  if (t === "run_start") {
    // 其他浏览器发送的消息：先渲染用户气泡（本地发送则跳过重复渲染）
    if (!liveRun.sentLocally) renderIncomingUserMessage(d.message || "");
    ensureLiveTurn();
    return;
  }
  if (t === "run_end") {
    endLiveTurn(d.usage);
    return;
  }
  if (t === "responded") {
    dismissPendingRespond(d.response);
    return;
  }
  if (!liveRun.active) {
    // 迟到的事件（如订阅瞬间运行恰好结束）：忽略内容类事件防错乱
    if (["reasoning", "content", "tool_confirm", "tool_result"].includes(t)) return;
  }
  const h = LIVE_HANDLERS[t];
  if (h) {
    try { h(d); } catch (e) { console.error(`实时事件处理异常 [${t}]:`, e); }
  }
}

function ensureReasoning() {
  if (liveRun.curReasoning) return liveRun.curReasoning;
  closeLiveContent();
  const textEl = el("div", { class: "thinking-content" });
  const blockEl = el("div", { class: "thinking-block open" },
    el("div", { class: "thinking-header" },
      el("span", { class: "arrow" }, "▶"),
      el("span", null, "思考过程"),
      el("span", { class: "thinking-live-dot" })),
    textEl);
  blockEl.querySelector(".thinking-header").addEventListener("click", () =>
    blockEl.classList.toggle("open"));
  getAiBody().append(blockEl);
  liveRun.curReasoning = { content: "", textEl, blockEl };
  return liveRun.curReasoning;
}

function ensureContent() {
  if (liveRun.curContent) return liveRun.curContent;
  closeLiveReasoning();
  const mdEl = el("div", { class: "md-content" });
  getAiBody().append(mdEl);
  liveRun.curContent = { raw: "", el: mdEl };
  return liveRun.curContent;
}

function addSysEvent(text, cls = "", icon = "ℹ️") {
  closeLiveReasoning(); closeLiveContent();
  getAiBody().append(el("div", { class: `sys-event ${cls}` },
    el("span", { class: "icon" }, icon), el("span", null, text)));
  scrollBottom();
}

function ensureToolCard(toolId, name) {
  if (liveRun.toolCards.has(toolId)) return liveRun.toolCards.get(toolId);
  closeLiveReasoning(); closeLiveContent();
  const statusEl = el("span", { class: "tag amber tool-status" }, "等待确认");
  const previewEl = el("span", { class: "tool-preview-text" });
  const bodyEl = el("div", { class: "tool-card-body" });
  // v5.2.8：工具卡片默认收起展示（点击标题展开），避免页面过长
  const cardEl = el("div", { class: "tool-card" },
    el("div", { class: "tool-card-header" },
      el("span", { class: "arrow" }, "▶"),
      el("span", { class: "tool-icon" }, toolIcon(name)),
      el("span", { class: "tool-name" }, name),
      previewEl, statusEl),
    bodyEl);
  cardEl.querySelector(".tool-card-header").addEventListener("click", () =>
    cardEl.classList.toggle("open"));
  getAiBody().append(cardEl);
  const rec = { toolId, name, cardEl, statusEl, previewEl, bodyEl, confirmEl: null };
  liveRun.toolCards.set(toolId, rec);
  liveRun.lastToolCard = rec;
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
  /* v5.3.1: 领域 Harness 检查发现（cbhpacks 工具产出）→ 卡片顶部警告块 + 标题徽标 */
  function renderHarnessBox(rec, findings) {
    const blocks = findings.filter(f => f.level === "BLOCK");
    const warns = findings.filter(f => f.level === "WARN" || f.level === "BLOCK");
    const infos = findings.filter(f => f.level === "INFO");
    const cls = blocks.length ? " hf-block" : (warns.length ? "" : " hf-info");
    const box = el("div", { class: "harness-warn-box" + cls });
    const parts = [];
    if (warns.length) parts.push(`${warns.length} 项警告`);
    if (infos.length) parts.push(`${infos.length} 项提示`);
    box.append(el("div", { class: "harness-warn-title" },
      `${blocks.length ? "⛔" : "🛡️"} Harness 检查 · ${parts.join("，")}`));
    for (const f of findings.slice(0, 10)) {
      const icon = f.level === "BLOCK" ? "🔴" : f.level === "WARN" ? "🟡" : "🔵";
      box.append(el("div", { class: "hf-line" }, `${icon} [${f.code}] ${f.message || ""}`));
      if (f.fix) box.append(el("div", { class: "hf-fix" }, `→ ${f.fix}`));
    }
    if (findings.length > 10) box.append(el("div", { class: "hf-fix" }, `… 共 ${findings.length} 项`));
    rec.bodyEl.prepend(box);
    // 标题徽标（卡片收起时也可见）
    const badge = el("span", { class: "tag harness-badge" + (blocks.length ? " block" : "") },
      (blocks.length ? "⛔ " : "⚠️ ") + warns.length);
    rec.statusEl.insertAdjacentElement("beforebegin", badge);
    return warns.length;
  }
  function setToolResult(rec, data) {
    const ok = data.success;
    setToolStatus(rec, ok ? "完成" : "失败", ok ? "green" : "red");
    rec.confirmEl?.remove();
    rec.confirmEl = null;

    // v5.3.1: 领域 Harness 检查发现（cbhpacks 工具产出）——警告块+徽标+醒目系统事件
    const hfAll = data.harness_findings || [];
    let hfWarns = 0;
    if (hfAll.length) {
      hfWarns = renderHarnessBox(rec, hfAll);
      if (hfWarns) {
        const codes = hfAll.filter(f => f.level !== "INFO").map(f => f.code).join(" ");
        addSysEvent(`🛡️ ${rec.name}: harness ${hfWarns} 项警告（${codes}）`, "warn", "🛡️");
      }
    }

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

    // AI 发送的文件/图片（display_files）
    const displayFiles = data.display_files || [];
    if (displayFiles.length) {
      const filesArea = el("div", { class: "ai-display-files" });
      const images = displayFiles.filter(f => f.is_image);
      const files = displayFiles.filter(f => !f.is_image);
      // 渲染图片
      if (images.length) {
        const imgGrid = el("div", { class: "ai-display-images" });
        for (const img of images) {
          const imgUrl = img.url || "";
          if (imgUrl) {
            // v5.3.1+：点击放大预览（全局灯箱代理），右键原生下载
            const imgEl = el("img", { src: imgUrl, alt: img.filename, class: "ai-display-img", title: img.filename,
                                      "data-download-url": img.download_url || "" });
            imgGrid.append(imgEl);
          }
        }
        filesArea.append(imgGrid);
      }
      // 渲染文件下载链接
      if (files.length) {
        const fileList = el("div", { class: "ai-display-file-list" });
        for (const f of files) {
          const dlUrl = f.download_url || "";
          if (dlUrl) {
            fileList.append(
              el("a", { href: dlUrl, download: f.filename, class: "file-download-link" },
                el("span", { class: "file-icon" }, "📥"),
                el("span", null, f.filename)));
          }
        }
        filesArea.append(fileList);
      }
      // 图片也提供下载链接
      if (images.length) {
        const imgDlList = el("div", { class: "ai-display-file-list" });
        for (const img of images) {
          const dlUrl = img.download_url || "";
          if (dlUrl) {
            imgDlList.append(
              el("a", { href: dlUrl, download: img.filename, class: "file-download-link" },
                el("span", { class: "file-icon" }, "📥"),
                el("span", null, img.filename)));
          }
        }
        filesArea.append(imgDlList);
      }
      rec.bodyEl.append(filesArea);
      // 展开工具卡片让用户看到图片
      rec.cardEl.classList.add("open");
    }

    // v5.2.9：write/edit 等工具成功后收起卡片（含自动生成的下载链接），
    // 仅失败或含图片（需直接可见）时展开；用户想看详情点击卡片即可
    // v5.3.1：harness 警告（WARN/BLOCK）也强制展开，确保用户看见
    const hasImages = displayFiles.some(f => f.is_image);
    if (!ok || hasImages || hfWarns > 0) rec.cardEl.classList.add("open");
    else rec.cardEl.classList.remove("open");
    scrollBottom();
  }

  // Todo 专用面板（v5.2.8：每调用一次 Todo 展示一次面板，不再原地替换）
  const showTodoPanel = (args) => {
    const todos = normalizeTodos(args);
    if (!todos.length) return;
    // v5.2.9 修复：关闭当前思考/内容块，使面板之后的输出追加到面板下方
    // （否则 curContent 仍指向面板前的元素，后续文字渲染到面板上方）
    closeLiveReasoning(); closeLiveContent();
    getAiBody().append(todoPanelEl(todos));
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

    // v5.3.1+：回放时该确认已被应答 → 只读展示，不重建交互确认卡
    if (data.answered) {
      setToolStatus(rec, "已确认", "blue");
      return;
    }

    // 需确认时展开工具卡片，便于用户审查参数后再决定
    rec.cardEl.classList.add("open");

    // 确认条（参数已在上方工具卡片中高亮展示，此处不再重复）
    // v5.2.8：按钮顺序与 CLI 一致 [Y/n/all/always]，"始终允许"改名消除歧义
    const chainTag = data.chain_agent ? ` [${data.chain_agent}]` : "";
    const confirmEl = el("div", { class: "confirm-card" },
      el("div", { class: "confirm-title" }, `⚠️ 确认执行 ${data.tool_name}${chainTag} ?`),
      el("div", { class: "confirm-actions" },
        el("button", { class: "btn btn-sm btn-success", "data-r": "y" }, "✓ 允许"),
        el("button", { class: "btn btn-sm btn-danger", "data-r": "n" }, "✕ 拒绝"),
        el("button", { class: "btn btn-sm", "data-r": "all" }, "全部允许"),
        el("button", { class: "btn btn-sm", "data-r": "always" }, "始终允许该命令")));
    rec.confirmEl = confirmEl;
    liveRun.pendingRespondEl = confirmEl;  // v5.2.9：其他浏览器应答后统一撤销
    getAiBody().append(confirmEl);
    scrollBottom();

    $$("button", confirmEl).forEach(btn => {
      btn.addEventListener("click", async () => {
        $$("button", confirmEl).forEach(b => (b.disabled = true));
        // v5.3.1+：先解除待应答标记再发请求--responded 事件可能先于
        // HTTP 响应到达，若仍指向本卡片会被误显示"已在其他窗口应答"
        if (liveRun.pendingRespondEl === confirmEl) liveRun.pendingRespondEl = null;
        try {
          await api.chatRespond(currentAgent(), currentModel(), btn.dataset.r, state.currentSessionId);
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
    closeLiveReasoning(); closeLiveContent();
    // v5.3.1+：回放时提问已被回答 → 只读展示
    if (data.answered) {
      const doneEl = el("div", { class: "ask-card" },
        el("div", { class: "ask-question" }, "❓ " + (data.question || "")),
        el("div", { class: "sys-event success" }, el("span", { class: "icon" }, "✓"),
          el("span", null, `已回答: ${data.answer || ""}`)));
      getAiBody().append(doneEl);
      return;
    }
    const askEl = el("div", { class: "ask-card" });
    askEl.append(el("div", { class: "ask-question" }, "❓ " + (data.question || "")));

    const options = data.options || [];
    const selected = new Set();
    let answered = false;

    const submit = async (answer) => {
      if (answered) return;
      answered = true;
      // v5.3.1+：先解除待应答标记（responded 事件可能先于 HTTP 响应到达）
      if (liveRun.pendingRespondEl === askEl) liveRun.pendingRespondEl = null;
      try {
        await api.chatRespond(currentAgent(), currentModel(), answer, state.currentSessionId);
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

    liveRun.pendingRespondEl = askEl;  // v5.2.9：其他浏览器应答后统一撤销
    getAiBody().append(askEl);
    scrollBottom();
    inputEl.focus();
  }

  /* ---- 事件分发（v5.2.9：WebSocket 实时事件，模块级） ---- */
  const LIVE_HANDLERS = {
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
      // call_agent 工具特殊展示：下游 Agent 调用结果以折叠区块显示
      if (d.tool_name === "call_agent") {
        setToolStatus(rec, "完成", "green");
        rec.confirmEl?.remove();
        const callBlock = el("div", { class: "chain-agent-call" },
          el("div", { class: "chain-agent-call-header" },
            el("span", null, `🔗 ${d.preview?.split('\n')[0]?.substring(0, 100) || "Agent 调用完成"}`),
          ),
          el("div", { class: "chain-agent-call-content" },
            el("span", null, d.preview || "")),
        );
        rec.cardEl.append(callBlock);
        return;
      }
      setToolResult(rec, d);
    },
    tool_rejected(d) {
      if (d.tool_name === "Todo") return;
      const rec = ensureToolCard(d.tool_id, d.tool_name);
      setToolStatus(rec, "已拒绝", "red");
      rec.confirmEl?.remove();
    },
    tool_denied(d) {
      // 权限规则 / PreToolUse 钩子拦截（Harness 治理层）
      const rec = ensureToolCard(d.tool_id, d.tool_name);
      setToolStatus(rec, "已拦截", "red");
      rec.confirmEl?.remove();
      addSysEvent(`🚫 ${d.reason || "操作被拦截"}`, "error", "🚫");
    },
    tool_yolo_warn(d) {
      const rec = ensureToolCard(d.tool_id, d.tool_name);
      setToolStatus(rec, "红线警告", "red");
      addSysEvent(`⚠️ [YOLO] ${d.tool_name} 命中红线规则 ${d.rule}，已放行`, "warn", "⚠️");
    },
    loop_detected(d) {
      const text = d.verdict === "block"
        ? `🛑 死循环熔断: 已阻止 ${d.tool_name} 重复调用，告知模型换策略`
        : d.verdict === "abort"
          ? `🛑 模型多次陷入死循环，已熔断本轮任务`
          : `⚠️ 检测到疑似死循环（${d.tool_name} 重复调用），已提醒模型`;
      addSysEvent(text, "warn", "🔁");
    },
    rule_added(d) {
      addSysEvent(`✅ 已添加永久放行规则: ${d.rule}`, "success", "🛡️");
    },
    hook_output(d) {
      addSysEvent(`[hook:${d.event}] ${d.content}`, "info", "🪝");
    },
    ask_user: handleAskUser,
    // ---- 链条下游 Agent 事件 ----
    chain_call_start(d) {
      addSysEvent(`📌 调用 Agent: ${d.agent_name}`, "info", "🔗");
      // 创建下游 Agent 输出区块
      const block = el("div", { class: "chain-agent-call", id: `chain-block-${d.agent_name}` },
        el("div", { class: "chain-agent-call-header" },
          el("span", { class: "chain-agent-tag" }, `🔗 ${d.agent_name}`),
          el("span", { class: "chain-agent-task" }, d.task || ""),
        ),
        el("div", { class: "chain-agent-call-content", id: `chain-content-${d.agent_name}` }),
      );
      getAiBody().append(block);
      scrollBottom();
    },
    chain_call_content(d) {
      const contentEl = document.getElementById(`chain-content-${d.agent_name}`);
      if (!contentEl) return;
      let textEl = contentEl.querySelector(".chain-agent-text");
      if (!textEl) {
        textEl = el("div", { class: "chain-agent-text" });
        textEl._raw = "";
        contentEl.append(textEl);
      }
      // 累积原始文本，整体重新渲染（避免逐段 renderMd 产生碎片 HTML）
      textEl._raw = (textEl._raw || "") + (d.content || "");
      textEl.innerHTML = renderMd(textEl._raw);
      scrollBottom();
    },
    chain_call_reasoning(d) {
      const contentEl = document.getElementById(`chain-content-${d.agent_name}`);
      if (!contentEl) return;
      let rEl = contentEl.querySelector(".chain-agent-reasoning");
      if (!rEl) {
        rEl = el("div", { class: "chain-agent-reasoning" });
        contentEl.append(rEl);
      }
      rEl.textContent += d.content || "";
      scrollBottom();
    },
    chain_call_tool(d) {
      const contentEl = document.getElementById(`chain-content-${d.agent_name}`);
      if (!contentEl) return;
      const toolLine = el("div", { class: "chain-agent-tool" },
        `🔧 ${d.tool_name}(${JSON.stringify(d.arguments || {}).slice(0, 80)})`);
      contentEl.append(toolLine);
      scrollBottom();
    },
    chain_call_tool_result(d) {
      const contentEl = document.getElementById(`chain-content-${d.agent_name}`);
      if (!contentEl) return;
      const status = d.success ? "✅" : "❌";
      const output = (d.output || d.error || "").slice(0, 200);
      const resultLine = el("div", { class: "chain-agent-tool-result" },
        `${status} ${d.tool_name}: ${output}`);
      contentEl.append(resultLine);
      scrollBottom();
    },
    chain_call_ask_answered(d) {
      addSysEvent(`✓ ${d.agent_name} 的提问已回答: ${d.answer}`, "success", "💬");
    },
    chain_call_end(d) {
      const status = d.success ? "✅" : "❌";
      addSysEvent(`${status} ${d.agent_name} 完成`, d.success ? "success" : "error", "🔗");
      // 标记区块完成
      const block = document.getElementById(`chain-block-${d.agent_name}`);
      if (block) block.classList.add("chain-agent-call-done");
    },
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

/* ---- 恢复会话消息（刷新页面后） ---- */
async function restoreMessages() {
  const a = currentAgent(), m = currentModel();
  if (!a || !m) return;
  try {
    // v5.2.9：优先恢复本浏览器上次打开的会话（多浏览器各自记忆），
    // 其次默认活跃会话，最后最新历史会话
    let lastId = null;
    try { lastId = localStorage.getItem("cbhcli.lastSession." + a); } catch (_) {}
    if (lastId) {
      try {
        const r = await api.chatLoad(a, m, "", "", lastId);
        setSessionId(r.session_id || lastId);
        if (r.workspace) state.currentWorkspace = r.workspace;
        if (r.messages && r.messages.length) renderRestoredMessages(r.messages);
        if (r.usage) updateCtxMeter(r.usage);
        // v5.3.1+（问题6）：CLI 运行中的会话 → 只读跟随视图
        if (r.cli_running) { enterCliFollow(r.session_id || lastId); refreshWorkspaces(); return; }
        // 空闲会话从 run_seq 续订（跳过上一轮事件回放，消息已含全量，防双份）
        wsSubscribe(r.session_id || lastId, r.run_active ? 0 : (r.run_seq || 0));
        if (r.run_active) { setStreaming(true); ensureLiveTurn(); }
        refreshWorkspaces();
        return;
      } catch (_) { /* 该会话已不存在，继续回落 */ }
    }

    const st = await api.chatStatus(a, m);
    if (st.active) {
      if (st.session_id) setSessionId(st.session_id);
      updateCtxMeter(st);
      const md = await api.chatMessages(a, m, st.session_id);
      if (md.messages && md.messages.length) renderRestoredMessages(md.messages);
      wsSubscribe(st.session_id, md.run_active ? 0 : (md.run_seq || 0));
      if (st.run_active) { setStreaming(true); ensureLiveTurn(); }
      return;
    }
    // 无活跃会话（如服务器重启后重新打开页面）→ 自动恢复最近会话，
    // 避免页面空白/进度条归零且上下文丢失
    // v5.3.1+：按时间倒序最多尝试 10 个（作用域外的会话会被 403 拒绝，跳过继续）
    const hist = await api.getHistory(a, 10);
    for (const s of hist.sessions || []) {
      try {
        const r = await api.chatLoad(a, m, s.filename, s.workspace);
        setSessionId(r.session_id || s.id || null);
        if (r.workspace) state.currentWorkspace = r.workspace;
        if (r.messages && r.messages.length) renderRestoredMessages(r.messages);
        if (r.usage) updateCtxMeter(r.usage);
        if (r.cli_running) { enterCliFollow(r.session_id || s.id); refreshWorkspaces(); return; }
        wsSubscribe(r.session_id || s.id, r.run_active ? 0 : (r.run_seq || 0));
        refreshWorkspaces();
        return;
      } catch (_) { /* 尝试下一个会话 */ }
    }
  } catch { /* 忽略 */ }
}

function renderRestoredMessages(messages) {
  const col = msgColumn();
  for (const msg of messages) {
    if (msg.role === "user") {
      const children = [];
      // 恢复图片
      if (msg.image_urls && msg.image_urls.length) {
        const imgGrid = el("div", { class: "msg-user-images" });
        for (const url of msg.image_urls) {
          // v5.3.1+：点击放大预览（全局灯箱代理），右键原生下载
          const dlUrl = url.includes("/api/files/serve/")
            ? url.replace("/api/files/serve/", "/api/files/download/") : "";
          const imgEl = el("img", { src: url, alt: "图片", class: "msg-user-img",
                                    "data-download-url": dlUrl });
          imgGrid.append(imgEl);
        }
        children.push(imgGrid);
      } else if (msg.image_count) {
        children.push(el("div", { class: "msg-user-images" },
          el("span", { class: "img-chip" }, `🖼 ${msg.image_count} 张图片`)));
      }
      // 恢复文件附件
      if (msg.file_attachments && msg.file_attachments.length) {
        const fileList = el("div", { class: "msg-user-files" });
        for (const f of msg.file_attachments) {
          const link = el("a", { href: f.download_url, download: f.filename, class: "file-download-link" },
            el("span", { class: "file-icon" }, "📄"),
            el("span", null, f.filename));
          fileList.append(link);
        }
        children.push(fileList);
      }
      children.push(el("div", { class: "msg-user-text" }, msg.content || ""));
      col.append(el("div", { class: "msg-user" },
        el("div", { class: "msg-user-bubble" }, ...children)));
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
        // write/edit 成功时添加文件下载链接
        if (tc.success !== false && (tc.name === "write" || tc.name === "edit")) {
          const fp = (tc.arguments && tc.arguments.file_path) || "";
          if (fp) {
            const fname = fp.split("/").pop().split("\\").pop();
            const dlUrl = `/api/files/download/${encodeURIComponent(fname)}`;
            bodyEl.append(
              el("div", { class: "tool-file-actions" },
                el("a", { href: dlUrl, download: fname, class: "file-download-link" },
                  el("span", { class: "file-icon" }, "📥"),
                  el("span", null, `下载 ${fname}`))));
          }
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
        // v5.3.1: 历史 tool 消息携带的 harness findings（cbhpacks 工具产出）——徽标+警告块
        if (tc.harness_findings && tc.harness_findings.length) {
          const histWarns = tc.harness_findings.filter(
            f => f.level === "WARN" || f.level === "BLOCK").length;
          const badge = el("span", { class: "tag harness-badge" },
            `⚠️ ${histWarns}`);
          card.querySelector(".tool-status").insertAdjacentElement("beforebegin", badge);
          const box = el("div", { class: "harness-warn-box" });
          box.append(el("div", { class: "harness-warn-title" },
            `🛡️ Harness 检查（历史）· ${tc.harness_findings.length} 项发现`));
          for (const f of tc.harness_findings.slice(0, 10)) {
            const icon = f.level === "BLOCK" ? "🔴" : f.level === "WARN" ? "🟡" : "🔵";
            box.append(el("div", { class: "hf-line" }, `${icon} [${f.code}] ${f.message || ""}`));
            if (f.fix) box.append(el("div", { class: "hf-fix" }, `→ ${f.fix}`));
          }
          bodyEl.prepend(box);
        }
        body.append(card);
      }
      col.append(el("div", { class: "msg-ai" },
        el("div", { class: "msg-ai-avatar" }, "❯"), body));
    }
  }
  // 恢复的历史消息中渲染 mermaid / echarts 图表
  void renderDiagrams(col);
  scrollBottom();
}

/* ---- 新会话 / 压缩 ---- */
async function newSession() {
  const ok = await confirmDialog("新建会话",
    "新会话立即可用；当前会话若仍在执行任务，将继续在后台运行。",
    { okText: "新建" });
  if (!ok) return;
  stopCliFollow();  // v5.3.1+：退出 CLI 跟随视图（若有）
  try {
    // v5.2.9：旧会话不中断（后台继续运行），仅切换到全新会话
    const r = await api.chatReset(currentAgent(), currentModel(), state.currentSessionId);
    liveRunReset();
    clearMessages();
    setSessionId(r.session_id || null);
    wsSubscribe(r.session_id || null, 0);
    setStreaming(false);
    refreshStatus();
    refreshWorkspaces();
    toast("已开始新会话", "success");
  } catch (e) { toast(e.message, "error"); }
}

async function manualCompress() {
  // 可选压缩指令（保留/丢弃重点），对应 CLI /comp <指令>
  const instructions = await promptDialog(
    "手动压缩上下文",
    [{ key: "instructions", label: "压缩指令（可选）", value: "",
       placeholder: "例如：保留迁移方案，丢弃调试过程。留空则默认压缩",
       type: "text", hint: "指令将指导摘要模型保留/丢弃特定内容" }],
    "压缩");
  if (instructions === null) return;  // 用户取消
  try {
    const r = await api.chatCompress(currentAgent(), currentModel(),
                                     (instructions.instructions || "").trim(),
                                     state.currentSessionId);
    toast(r.message, r.compressed ? "success" : "info");
    if (r.usage) updateCtxMeter(r.usage);
  } catch (e) { toast(e.message, "error"); }
}

/* ---- 文件回滚（/undo） ---- */
async function showUndoModal() {
  const agent = requireAgent();
  if (!agent) return;
  let data;
  try { data = await api.getBackups(agent); }
  catch (e) { toast(e.message, "error"); return; }
  const backups = data.backups || [];

  const body = el("div");
  if (!backups.length) {
    body.append(el("p", { style: "color:var(--text-2);" },
      "暂无可回滚的备份。write/edit 工具执行前会自动备份目标文件。"));
  } else {
    body.append(el("p", { style: "color:var(--text-2);font-size:12.5px;margin-bottom:10px;" },
      `共 ${backups.length} 份备份（write/edit 前自动创建），点击恢复对应文件。`));
    const list = el("div", { class: "reorder-list" });
    for (const b of backups) {
      const kind = b.existed ? "修改" : "新建";
      list.append(el("div", { class: "reorder-item" },
        el("span", { class: "tag " + (b.existed ? "blue" : "green") }, kind),
        el("span", { class: "reorder-name", style: "font-size:12px;",
                     title: b.path }, `${(b.ts || "").slice(5, 16).replace("T", " ")}  ${b.path}`),
        el("button", {
          class: "btn btn-sm",
          onclick: async (e) => {
            e.target.disabled = true;
            try {
              const r = await api.undoBackup(agent, b.id);
              toast(r.message, "success");
              m.close();
            } catch (err) {
              toast(err.message, "error");
              e.target.disabled = false;
            }
          },
        }, "恢复")));
    }
    body.append(list);
  }

  const m = openModal({ title: "↩️ 回滚文件修改", body, width: "640px" });
}

/* ===================================================================
   侧边栏工作区会话列表（v5.2.8）
   =================================================================== */

function fmtRelTime(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  if (isNaN(t.getTime())) return "";
  const diffMin = Math.floor((Date.now() - t.getTime()) / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin}分`;
  const h = Math.floor(diffMin / 60);
  if (h < 24) return `${h}时`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}天`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}月`;
  return `${Math.floor(mo / 12)}年`;
}

/* 完整时间：年月日时分秒（v5.2.9 侧边栏会话时间详细显示） */
function fmtFullTime(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  if (isNaN(t.getTime())) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} `
       + `${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
}

function initSidebar() {
  $("#btn-new-session-side").addEventListener("click", async () => {
    await newSession();
    refreshWorkspaces();
  });
  $("#btn-settings").addEventListener("click", () => switchView("settings"));
  $("#btn-ws-refresh").addEventListener("click", () => refreshWorkspaces());
  $("#btn-ws-open").addEventListener("click", () => showWorkspaceBrowser());
  $("#btn-ws-search").addEventListener("click", () => {
    const inp = $("#ws-filter");
    inp.classList.toggle("hidden");
    if (!inp.classList.contains("hidden")) inp.focus();
    else { inp.value = ""; state.wsFilter = ""; refreshWorkspaces(); }
  });
  $("#ws-filter").addEventListener("input", (e) => {
    state.wsFilter = e.target.value.trim().toLowerCase();
    refreshWorkspaces();
  });
  // 定时刷新（会话标题/新会话后台落盘后同步到侧边栏）
  setInterval(() => { if (!state.streaming) refreshWorkspaces(); }, 20000);
}

async function refreshWorkspaces() {
  const a = currentAgent();
  const listEl = $("#ws-list");
  if (!a || !listEl) return;
  let data;
  try { data = await api.workspaceInfo(a); } catch { return; }
  state.currentWorkspace = data.current || state.currentWorkspace;
  renderWorkspaces(data);
  refreshFileManager();  // 工作空间变化时内部自动重置到根目录
}

function renderWorkspaces(data) {
  const listEl = $("#ws-list");
  listEl.innerHTML = "";
  const filter = state.wsFilter;
  for (const ws of data.workspaces || []) {
    const total = ws.sessions || [];
    let sessions = total;
    let hiddenCount = 0;
    if (filter) {
      sessions = total.filter(s => (s.title || "").toLowerCase().includes(filter));
    } else if (!state.wsShowAll[ws.path]) {
      sessions = total.slice(0, 5);
      hiddenCount = total.length - sessions.length;
    }
    const expanded = filter ? true
      : (state.wsExpanded[ws.path] ?? ws.path === data.current);

    const fMenuBtn = el("span", { class: "ws-session-menu ws-folder-menu", title: "工作空间管理" }, "⋯");
    fMenuBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      showWorkspaceMenu(ws, fMenuBtn);
    });
    const head = el("div", { class: "ws-folder-head" },
      el("span", { class: "ws-arrow" + (expanded ? " open" : "") }, "▶"),
      el("span", { class: "ws-folder-icon" }, "📂"),
      el("span", { class: "ws-folder-name", title: ws.path }, ws.name),
      fMenuBtn);
    head.addEventListener("click", () => {
      state.wsExpanded[ws.path] = !expanded;
      renderWorkspaces(data);
    });
    const row = el("div", {
      class: "ws-folder" + (ws.path === data.current ? " current" : ""),
    }, head);

    if (expanded) {
      const bodyEl = el("div", { class: "ws-sessions" });
      if (!sessions.length) {
        bodyEl.append(el("div", { class: "ws-empty" },
          filter ? "无匹配会话" : "暂无会话"));
      }
      for (const s of sessions) bodyEl.append(wsSessionRow(s));
      if (hiddenCount > 0) {
        const more = el("div", { class: "ws-more" }, `展开其余 ${hiddenCount} 个会话`);
        more.addEventListener("click", () => {
          state.wsShowAll[ws.path] = true;
          renderWorkspaces(data);
        });
        bodyEl.append(more);
      }
      row.append(bodyEl);
    }
    listEl.append(row);
  }
}

function wsSessionRow(s) {
  const isCurrent = !!s.id && s.id === state.currentSessionId;
  const menuBtn = el("span", { class: "ws-session-menu", title: "会话管理" }, "⋯");
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    showSessionMenu(s, menuBtn);
  });
  const titleRow = el("div", { class: "ws-session-row1" },
    el("span", { class: "ws-session-title", title: s.title }, s.title || "新会话"));
  // 运行状态徽标：Web 后台运行 / CLI 运行中（v5.3.1+ 问题6）
  if (s.running && s.cli) titleRow.append(el("span", { class: "ws-run-badge cli" }, "● CLI运行中"));
  else if (s.running) titleRow.append(el("span", { class: "ws-run-badge" }, "● 运行中"));
  // v5.3.1+（问题1）：消息数 + 最后对话时间（updated_at，非创建时间）
  const time = s.updated_at || s.created_at;
  const metaRow = el("div", { class: "ws-session-row2" },
    el("span", { class: "ws-session-count" }, `💬 ${s.message_count || 0} 条`),
    el("span", { class: "ws-session-time", title: fmtFullTime(time) }, fmtFullTime(time)));
  const row = el("div", { class: "ws-session" + (isCurrent ? " active" : "") },
    el("div", { class: "ws-session-main" }, titleRow, metaRow),
    menuBtn);
  row.addEventListener("click", () => openSidebarSession(s));
  return row;
}

/* ---- 会话管理菜单（悬停三点按钮弹出：重命名/复制/删除） ---- */
let _sessionMenuEl = null;
function closeSessionMenu() {
  if (_sessionMenuEl) { _sessionMenuEl.remove(); _sessionMenuEl = null; }
}

function showSessionMenu(s, anchor) {
  closeSessionMenu();
  const menu = el("div", { class: "ws-ctx-menu" });
  const mkItem = (label, cls, fn) => {
    const it = el("div", { class: "ws-ctx-item" + (cls ? " " + cls : "") }, label);
    it.addEventListener("click", (e) => {
      e.stopPropagation();
      closeSessionMenu();
      fn();
    });
    return it;
  };
  menu.append(
    mkItem("✏️ 重命名", "", () => doSessionRename(s)),
    mkItem("📄 复制会话", "", () => doSessionCopy(s)),
    mkItem("🗑 删除会话", "danger", () => doSessionDelete(s)));
  document.body.append(menu);
  // 定位：锚点右下方，靠右对齐，避免超出视口
  const rect = anchor.getBoundingClientRect();
  menu.style.top = Math.min(rect.bottom + 4, window.innerHeight - 120) + "px";
  menu.style.left = Math.max(8, rect.right - menu.offsetWidth - 8) + "px";
  _sessionMenuEl = menu;
  setTimeout(() => {
    document.addEventListener("click", closeSessionMenu, { once: true });
  }, 0);
}

async function doSessionRename(s) {
  const out = await promptDialog("重命名会话",
    [{ key: "title", label: "会话标题", value: s.title || "", type: "text" }],
    "保存");
  if (out === null) return;
  const title = (out.title || "").trim();
  if (!title) { toast("标题不能为空", "warn"); return; }
  try {
    const r = await api.sessionRename(currentAgent(), s, title);
    toast(r.message, "success");
    refreshWorkspaces();
  } catch (e) { toast(e.message, "error"); }
}

async function doSessionCopy(s) {
  try {
    const r = await api.sessionCopy(currentAgent(), s);
    toast(r.message, "success");
    refreshWorkspaces();
  } catch (e) { toast(e.message, "error"); }
}

async function doSessionDelete(s) {
  const ok = await confirmDialog("删除会话",
    `确定删除会话「${(s.title || "").slice(0, 30)}」吗？此操作不可恢复。`,
    { danger: true, okText: "删除" });
  if (!ok) return;
  const isCurrent = !!s.id && s.id === state.currentSessionId;
  try {
    const r = await api.sessionDelete(currentAgent(), s);
    toast(r.message, "success");
    if (isCurrent || r.was_active) {
      liveRunReset();
      clearMessages();
      setSessionId(null);
      wsSubscribe(null);
      setStreaming(false);
      refreshStatus();
    }
    refreshWorkspaces();
  } catch (e) { toast(e.message, "error"); }
}

/* ---- 工作空间（文件夹）三点管理菜单 ---- */
function showWorkspaceMenu(ws, anchor) {
  closeSessionMenu();
  const menu = el("div", { class: "ws-ctx-menu" });
  const mkItem = (label, cls, fn) => {
    const it = el("div", { class: "ws-ctx-item" + (cls ? " " + cls : "") }, label);
    it.addEventListener("click", (e) => {
      e.stopPropagation();
      closeSessionMenu();
      fn();
    });
    return it;
  };
  menu.append(
    mkItem("📂 选择该文件夹", "", () => doWorkspaceSelect(ws)),
    mkItem("✚ 新增会话", "", () => doWorkspaceNew(ws)),
    mkItem("🗑 删除全部会话", "danger", () => doWorkspaceClear(ws)));
  document.body.append(menu);
  const rect = anchor.getBoundingClientRect();
  menu.style.top = Math.min(rect.bottom + 4, window.innerHeight - 120) + "px";
  menu.style.left = Math.max(8, rect.right - menu.offsetWidth - 8) + "px";
  _sessionMenuEl = menu;
  setTimeout(() => {
    document.addEventListener("click", closeSessionMenu, { once: true });
  }, 0);
}

// 选择该文件夹：切换工作空间并恢复其最新会话（空文件夹则为新会话）
async function doWorkspaceSelect(ws) {
  if (ws.path === state.currentWorkspace) { switchView("chat"); return; }
  stopCliFollow();
  try {
    const r = await api.workspaceOpen(currentAgent(), currentModel(), ws.path, true);
    toast(r.message, "success");
    state.currentWorkspace = r.workspace;
    state.wsExpanded[ws.path] = true;
    liveRunReset();
    switchView("chat");
    clearMessages();
    if (r.messages && r.messages.length) renderRestoredMessages(r.messages);
    if (r.usage) updateCtxMeter(r.usage);
    setSessionId(r.session_id || null);
    if (r.session_id) {
      // v5.3.1+（问题6）：最新会话正由 CLI 运行 → 只读跟随视图
      if (r.cli_running) { enterCliFollow(r.session_id); refreshWorkspaces(); return; }
      wsSubscribe(r.session_id, r.run_active ? 0 : (r.run_seq || 0));
      if (r.run_active) { setStreaming(true); ensureLiveTurn(); }
    } else {
      wsSubscribe(null);  // 空文件夹：无会话，退订旧会话
    }
    refreshStatus();
    refreshWorkspaces();
  } catch (e) { toast(e.message, "error"); }
}

// 新增会话：切换到该文件夹并开始全新会话
async function doWorkspaceNew(ws) {
  if (ws.path === state.currentWorkspace) {
    await newSession();
    refreshWorkspaces();
    return;
  }
  stopCliFollow();
  try {
    const r = await api.workspaceOpen(currentAgent(), currentModel(), ws.path, false);
    toast(r.message, "success");
    state.currentWorkspace = r.workspace;
    state.wsExpanded[ws.path] = true;
    liveRunReset();
    switchView("chat");
    clearMessages();
    setSessionId(null);
    wsSubscribe(null);
    setStreaming(false);
    refreshStatus();
    refreshWorkspaces();
  } catch (e) { toast(e.message, "error"); }
}

// 删除该文件夹下的全部会话
async function doWorkspaceClear(ws) {
  const ok = await confirmDialog("删除全部会话",
    `确定删除工作空间「${ws.name}」下的全部会话吗？此操作不可恢复。`,
    { danger: true, okText: "全部删除" });
  if (!ok) return;
  const isCurrentWs = ws.path === state.currentWorkspace;
  try {
    const r = await api.workspaceClearSessions(currentAgent(), ws.path);
    toast(r.message, "success");
    if (isCurrentWs || r.was_active) {
      liveRunReset();
      clearMessages();
      setSessionId(null);
      wsSubscribe(null);
      setStreaming(false);
      refreshStatus();
    }
    refreshWorkspaces();
  } catch (e) { toast(e.message, "error"); }
}

async function openSidebarSession(s) {
  // v5.2.9：点击会话 = 切换到该会话（含后台运行中的会话）。
  // 运行中的会话不中断，画面由 WebSocket 事件回放接管（多浏览器一致）。
  if (s.id && s.id === state.currentSessionId) { switchView("chat"); return; }
  stopCliFollow();  // v5.3.1+：离开 CLI 跟随视图（若有）
  try {
    const r = await api.chatLoad(currentAgent(), currentModel(), s.filename, s.workspace, s.id);
    setSessionId(r.session_id || s.id || null);
    if (r.workspace) state.currentWorkspace = r.workspace;
    // 会话可能属于其他模型：同步模型选择器（程序赋值不触发 change 事件）
    if (r.model && r.model !== state.selectedModel) {
      state.selectedModel = r.model;
      chatUI.modelSelect.value = r.model;
    }
    liveRunReset();
    switchView("chat");
    clearMessages();
    renderRestoredMessages(r.messages || []);
    if (r.usage) updateCtxMeter(r.usage);
    // v5.3.1+（问题6）：CLI 正在运行的会话 → 只读跟随视图（轮级实时）
    if (r.cli_running) {
      enterCliFollow(r.session_id || s.id);
      refreshWorkspaces();
      return;
    }
    wsSubscribe(r.session_id || s.id, r.run_active ? 0 : (r.run_seq || 0));
    if (r.run_active) { setStreaming(true); ensureLiveTurn(); }
    refreshStatus();
    refreshWorkspaces();
  } catch (e) { toast(e.message, "error"); }
}

/* ---- 打开工作空间（文件夹选择弹窗） ---- */
async function showWorkspaceBrowser() {
  const body = el("div", { class: "ws-browser" });
  const crumbEl = el("div", { class: "ws-browser-crumb" });
  const listEl = el("div", { class: "ws-browser-list" });
  body.append(crumbEl, listEl);

  let curPath = state.currentWorkspace || "";
  let serverRoot = "";

  async function nav(path) {
    let data;
    try { data = await api.workspaceBrowse(path); }
    catch (e) { toast(e.message, "error"); return; }
    curPath = data.path;
    serverRoot = data.server_root;
    // 面包屑：根目录 + 相对路径各段
    crumbEl.innerHTML = "";
    const rootBtn = el("span", { class: "ws-crumb", title: serverRoot },
      "📁 " + (serverRoot.split("/").pop() || serverRoot));
    rootBtn.addEventListener("click", () => nav(serverRoot));
    crumbEl.append(rootBtn);
    let acc = serverRoot;
    for (const seg of data.path.slice(serverRoot.length).split("/").filter(Boolean)) {
      acc += "/" + seg;
      const target = acc;
      const c = el("span", { class: "ws-crumb" }, " / " + seg);
      c.addEventListener("click", () => nav(target));
      crumbEl.append(c);
    }
    // 目录列表
    listEl.innerHTML = "";
    if (curPath !== serverRoot) {
      const up = el("div", { class: "ws-browser-item up" }, "⬆ 上级目录");
      const parent = curPath.slice(0, curPath.lastIndexOf("/")) || "/";
      up.addEventListener("click", () => nav(parent.startsWith(serverRoot) ? parent : serverRoot));
      listEl.append(up);
    }
    if (!data.dirs.length) {
      listEl.append(el("div", { class: "ws-empty" }, "（无子文件夹）"));
    }
    for (const d of data.dirs) {
      const item = el("div", { class: "ws-browser-item" }, "📁 " + d.name);
      item.addEventListener("click", () => nav(d.path));
      listEl.append(item);
    }
  }

  const openBtn = el("button", {
    class: "btn btn-primary",
    onclick: async (e) => {
      e.target.disabled = true;
      try {
        const r = await api.workspaceOpen(currentAgent(), currentModel(), curPath);
        stopCliFollow();
        toast(r.message, "success");
        state.currentWorkspace = r.workspace;
        state.wsExpanded[r.workspace] = true;
        m.close();
        liveRunReset();
        switchView("chat");
        clearMessages();
        setSessionId(null);
        wsSubscribe(null);
        setStreaming(false);
        refreshStatus();
        refreshWorkspaces();
      } catch (err) {
        toast(err.message, "error");
        e.target.disabled = false;
      }
    },
  }, "打开当前文件夹");

  const m = openModal({
    title: "📂 打开工作空间",
    body,
    footer: [el("button", { class: "btn", onclick: () => m.close() }, "取消"), openBtn],
    width: "560px",
  });
  nav(curPath || undefined);
}

/* ===================================================================
   面板布局：工作区/文件管理器左右互换 + 拖拽调宽（v5.2.8）
   =================================================================== */

function applyPanelLayout() {
  const fmSide = localStorage.getItem(LS_FM_SIDE) || "right";
  const app = $("#app");
  const wsPanel = $("#ws-panel"), fmPanel = $("#fm-panel");
  const rLeft = $("#resize-left"), rRight = $("#resize-right");
  const main = $("#main");
  if (fmSide === "left") app.append(fmPanel, rLeft, main, rRight, wsPanel);
  else app.append(wsPanel, rLeft, main, rRight, fmPanel);
}

function swapPanels() {
  const cur = localStorage.getItem(LS_FM_SIDE) || "right";
  localStorage.setItem(LS_FM_SIDE, cur === "right" ? "left" : "right");
  applyPanelLayout();
}

function initPanelLayout() {
  applyPanelLayout();
  const wsW = parseInt(localStorage.getItem(LS_WS_WIDTH) || "", 10);
  const fmW = parseInt(localStorage.getItem(LS_FM_WIDTH) || "", 10);
  if (wsW) $("#ws-panel").style.width = wsW + "px";
  if (fmW) $("#fm-panel").style.width = fmW + "px";
  $("#btn-ws-swap").addEventListener("click", swapPanels);
  $("#btn-fm-swap").addEventListener("click", swapPanels);
  setupResizeHandle($("#resize-left"), "left");
  setupResizeHandle($("#resize-right"), "right");
}

function setupResizeHandle(handle, side) {
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    // 左手柄控制其左侧面板，右手柄控制其右侧面板
    const panel = side === "left" ? handle.previousElementSibling
                                  : handle.nextElementSibling;
    if (!panel || !panel.classList.contains("side-panel")) return;
    const startX = e.clientX, startW = panel.offsetWidth;
    const lsKey = panel.id === "ws-panel" ? LS_WS_WIDTH : LS_FM_WIDTH;
    const move = (ev) => {
      let w = side === "left" ? startW + (ev.clientX - startX)
                              : startW - (ev.clientX - startX);
      w = Math.max(PANEL_MIN_W, Math.min(PANEL_MAX_W, w));
      panel.style.width = w + "px";
      localStorage.setItem(lsKey, String(w));
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      document.body.classList.remove("resizing");
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    document.body.classList.add("resizing");
  });
}

/* ===================================================================
   文件管理器（v5.2.8：当前工作空间文件浏览/下载等）
   =================================================================== */

const FM_ICONS = {
  py: "🐍", js: "📜", ts: "📜", sh: "⚙️", md: "📝", txt: "📄", log: "📄",
  json: "🧾", yaml: "🧾", yml: "🧾", toml: "🧾", csv: "📊", xlsx: "📊",
  png: "🖼️", jpg: "🖼️", jpeg: "🖼️", gif: "🖼️", svg: "🖼️",
  pdf: "📕", zip: "📦", tar: "📦", gz: "📦", whl: "📦",
  html: "🌐", css: "🎨",
};
function fmIcon(entry) {
  if (entry.is_dir) return "📁";
  const i = entry.name.lastIndexOf(".");
  const ext = i > 0 ? entry.name.slice(i + 1).toLowerCase() : "";
  return FM_ICONS[ext] || "📄";
}

function initFileManager() {
  $("#btn-fm-refresh").addEventListener("click", () => refreshFileManager());
  $("#btn-fm-up").addEventListener("click", () => {
    if (!state.fmPath || state.fmPath === state.fmWorkspace) return;
    const parent = state.fmPath.slice(0, state.fmPath.lastIndexOf("/")) || "/";
    state.fmPath = parent.startsWith(state.fmWorkspace) ? parent : state.fmWorkspace;
    refreshFileManager();
  });
}

async function refreshFileManager() {
  if (!$("#fm-list")) return;
  // v5.3.1+ 第四轮：文件管理器锚定【服务启动目录】（与侧边栏同逻辑），
  // 选择子文件夹作为工作空间后不缩窄，主目录/兄弟文件夹保持可浏览
  let data;
  try {
    data = await api.filesList(state.fmPath);
  } catch {
    state.fmPath = "";  // 路径越界（如残留的旧浏览位置）→ 回根目录
    try { data = await api.filesList(""); } catch { return; }
  }
  state.fmPath = data.path;
  state.fmWorkspace = data.workspace;
  renderFileManager(data);
}

function renderFileManager(data) {
  // 面包屑：工作空间根 + 各级相对目录
  const crumbEl = $("#fm-crumb");
  crumbEl.innerHTML = "";
  const rootBtn = el("span", { class: "ws-crumb", title: data.workspace }, "🏠 根");
  rootBtn.addEventListener("click", () => {
    state.fmPath = data.workspace;
    refreshFileManager();
  });
  crumbEl.append(rootBtn);
  let acc = data.workspace.replace(/\/$/, "");
  for (const seg of data.path.slice(data.workspace.length).split("/").filter(Boolean)) {
    acc += "/" + seg;
    const target = acc;
    const c = el("span", { class: "ws-crumb" }, " / " + seg);
    c.addEventListener("click", () => { state.fmPath = target; refreshFileManager(); });
    crumbEl.append(c);
  }

  const listEl = $("#fm-list");
  listEl.innerHTML = "";
  if (!data.entries.length) {
    listEl.append(el("div", { class: "ws-empty" }, "（空目录）"));
    return;
  }
  for (const ent of data.entries) {
    const menuBtn = el("span", { class: "ws-session-menu fm-menu", title: "更多操作" }, "⋯");
    menuBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      showFileMenu(ent, menuBtn);
    });
    const row = el("div", { class: "fm-item" },
      el("span", { class: "fm-icon" }, fmIcon(ent)),
      el("span", { class: "fm-name", title: ent.path }, ent.name),
      el("span", { class: "fm-meta" }, ent.is_dir ? "" : fmtSize(ent.size)),
      menuBtn);
    row.addEventListener("click", () => {
      // 文件夹点击进入；文件点击不触发下载（下载仅通过三点菜单）
      if (ent.is_dir) { state.fmPath = ent.path; refreshFileManager(); }
    });
    listEl.append(row);
  }
}

function downloadFile(ent) {
  const a = el("a", {
    href: `/api/files/download_path?path=${enc(ent.path)}`,
    download: ent.name,
  });
  document.body.append(a);
  a.click();
  a.remove();
}

function showFileMenu(ent, anchor) {
  closeSessionMenu();
  const menu = el("div", { class: "ws-ctx-menu" });
  const mkItem = (label, cls, fn) => {
    const it = el("div", { class: "ws-ctx-item" + (cls ? " " + cls : "") }, label);
    it.addEventListener("click", (e) => {
      e.stopPropagation();
      closeSessionMenu();
      fn();
    });
    return it;
  };
  if (ent.is_dir) {
    menu.append(
      mkItem("✅ 选择该文件夹", "", () => selectFolderFromFM(ent.path)),
      mkItem("📂 打开为工作空间（新会话）", "", () => openFolderAsWorkspace(ent.path)),
      mkItem("📋 复制文件名称", "", () => {
        copyText(ent.name);
        toast("已复制文件名称", "success");
      }),
      mkItem("📋 复制路径", "", () => {
        copyText(ent.path);
        toast("已复制路径", "success");
      }));
  } else {
    menu.append(
      mkItem("📥 下载", "", () => downloadFile(ent)),
      mkItem("📋 复制文件名称", "", () => {
        copyText(ent.name);
        toast("已复制文件名称", "success");
      }),
      mkItem("📋 复制路径", "", () => {
        copyText(ent.path);
        toast("已复制路径", "success");
      }));
  }
  document.body.append(menu);
  const rect = anchor.getBoundingClientRect();
  menu.style.top = Math.min(rect.bottom + 4, window.innerHeight - 100) + "px";
  menu.style.left = Math.max(8, rect.right - menu.offsetWidth - 8) + "px";
  _sessionMenuEl = menu;
  setTimeout(() => {
    document.addEventListener("click", closeSessionMenu, { once: true });
  }, 0);
}

async function openFolderAsWorkspace(path) {
  stopCliFollow();
  try {
    const r = await api.workspaceOpen(currentAgent(), currentModel(), path, false);
    toast(r.message, "success");
    state.currentWorkspace = r.workspace;
    state.wsExpanded[r.workspace] = true;
    liveRunReset();
    switchView("chat");
    clearMessages();
    setSessionId(null);
    wsSubscribe(null);
    setStreaming(false);
    refreshStatus();
    refreshWorkspaces();
  } catch (e) { toast(e.message, "error"); }
}

/* v5.3.1+ 第四轮：文件管理器"选择该文件夹"——切换工作空间并恢复其最新会话
   （与侧边栏文件夹菜单"选择该文件夹"同逻辑，resume=true） */
async function selectFolderFromFM(path) {
  stopCliFollow();
  try {
    const r = await api.workspaceOpen(currentAgent(), currentModel(), path, true);
    toast(r.message, "success");
    state.currentWorkspace = r.workspace;
    state.wsExpanded[r.workspace] = true;
    liveRunReset();
    switchView("chat");
    clearMessages();
    if (r.messages && r.messages.length) renderRestoredMessages(r.messages);
    if (r.usage) updateCtxMeter(r.usage);
    setSessionId(r.session_id || null);
    if (r.session_id) {
      if (r.cli_running) { enterCliFollow(r.session_id); refreshWorkspaces(); return; }
      wsSubscribe(r.session_id, r.run_active ? 0 : (r.run_seq || 0));
      if (r.run_active) { setStreaming(true); ensureLiveTurn(); }
    } else {
      wsSubscribe(null);  // 空文件夹：无会话
    }
    refreshStatus();
    refreshWorkspaces();
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
  let data, mcpServers = [];
  try { data = await api.getTools(agent); }
  catch (e) { toast(e.message, "error"); return; }
  try {
    const mcpData = await api.getMCP(agent);
    mcpServers = mcpData.servers || [];
  } catch (e) { mcpServers = []; }

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

  /* ---- MCP 工具开关（与 MCP 页面"工具"按钮一致） ---- */
  body.append(el("div", { class: "quick-section" }, "🔌 MCP 工具"));
  if (!mcpServers.length) {
    body.append(el("div", { class: "quick-item-desc", style: "padding:2px 2px 8px;" },
      "暂无 MCP 服务器，可在「MCP」页面添加"));
  } else {
    // 并行拉取各服务器的完整工具列表（含禁用状态）
    const perServer = await Promise.all(mcpServers.map(async (s) => {
      try {
        const t = await api.getMCPTools(agent, s.name);
        return { server: s, tools: t.tools || [], error: null };
      } catch (e) { return { server: s, tools: [], error: e.message }; }
    }));
    for (const { server: s, tools, error } of perServer) {
      body.append(el("div", { class: "quick-item" },
        el("div", { class: "quick-item-info" },
          el("div", { class: "quick-item-name" },
            "🔌 " + s.name, " ",
            el("span", { class: `dot ${s.connected ? "green" : "red"}` }), " ",
            el("span", { class: "tag" }, `${tools.length} 工具`)),
          el("div", { class: "quick-item-desc" }, s.url || ""))));
      if (error) {
        body.append(el("div", { class: "quick-item-desc", style: "padding:0 2px 6px;" },
          `工具列表加载失败: ${error}`));
        continue;
      }
      if (!tools.length) {
        body.append(el("div", { class: "quick-item-desc", style: "padding:0 2px 6px;" },
          s.connected ? "无可用工具" : "未连接，请先在「MCP」页面刷新连接"));
        continue;
      }
      for (const tool of tools) {
        const sw = el("input", { type: "checkbox" });
        sw.checked = tool.enabled;
        sw.addEventListener("change", async () => {
          try {
            await api.toggleMCPTool(agent, s.name, tool.name, sw.checked);
            toast(`MCP 工具 '${tool.name}' 已${sw.checked ? "启用" : "禁用"}`, "success");
          } catch (e) { toast(e.message, "error"); sw.checked = !sw.checked; }
        });
        body.append(el("div", { class: "quick-item" },
          el("div", { class: "quick-item-info" },
            el("div", { class: "quick-item-name" }, toolIcon("mcp_" + s.name), tool.name),
            el("div", { class: "quick-item-desc" }, tool.description || "")),
          el("label", { class: "switch" }, sw, el("span", { class: "track" }))));
      }
    }
  }

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
      const old = currentModel();
      try {
        // 原地切换模型：保留当前会话及上下文（对齐 CLI /model use）
        const r = await api.chatSwitchModel(currentAgent(), old, m.name, state.currentSessionId);
        state.selectedModel = m.name;
        refreshSelectors();
        $("#modal-root").innerHTML = "";
        toast(r.message || `已切换到模型 '${m.name}'`, "success");
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
  // v5.2.9：返回按钮（详情页 -> 设置主页 -> 会话），避免进入设置后无法返回
  const backBtns = [];
  if (state.currentView === "settings") {
    backBtns.push(el("button", {
      class: "btn btn-ghost btn-sm page-back-btn",
      onclick: () => switchView("chat"),
    }, "← 返回会话"));
  } else if (state.currentView !== "chat") {
    backBtns.push(el("button", {
      class: "btn btn-ghost btn-sm page-back-btn",
      onclick: () => switchView("settings"),
    }, "← 设置"));
  }
  inner.append(el("div", { class: "page-header" },
    el("div", null,
      el("div", { class: "page-title" }, title),
      desc ? el("div", { class: "page-desc" }, desc) : null),
    el("div", { class: "page-actions" }, ...backBtns, ...actions)));
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
   7. Agent 链条管理视图
   =================================================================== */

async function loadChainsView() {
  const root = $("#view-chains");
  root.innerHTML = "";
  const data = await api.getChains();
  const chains = data.chains || [];

  const addBtn = el("button", { class: "btn btn-primary", onclick: () => showCreateChainModal() }, "✚ 新建链条");
  const inner = pageShell("Agent 链条", "多 Agent 调用编排，实现跨 Agent 工作流", [addBtn]);
  root.append(el("div", { class: "page" }, inner));

  if (!chains.length) {
    inner.append(emptyState("暂无链条，点击右上角创建"));
    return;
  }

  for (const c of chains) {
    const card = el("div", { class: "card chain-card" });

    // 头部
    const header = el("div", { class: "card-row" },
      el("div", null,
        el("div", { class: "card-title" },
          el("span", { class: "chain-icon" }, "🔗"),
          el("span", null, c.name),
          ...(!c.valid ? [el("span", { class: "badge badge-error" }, "⚠️ 无效")] : []),
        ),
        c.description ? el("div", { class: "card-desc" }, c.description) : null,
      ),
      el("div", { class: "card-actions" },
        el("button", { class: "btn btn-sm",
          onclick: () => showChainDetail(c.name) }, "查看"),
        el("button", { class: "btn btn-sm",
          onclick: () => showCreateChainModal(c) }, "编辑"),
        el("button", { class: "btn btn-sm btn-danger",
          onclick: () => deleteChainConfirm(c.name) }, "删除"),
      ),
    );
    card.append(header);

    // 树形结构
    const tree = el("div", { class: "chain-tree" });
    for (let i = 0; i < c.levels.length; i++) {
      const level = c.levels[i];
      const indent = "  ".repeat(i);
      for (let j = 0; j < level.agents.length; j++) {
        const a = level.agents[j];
        const isLast = j === level.agents.length - 1;
        const connector = i === 0 ? "" : (isLast ? "└── " : "├── ");
        const node = el("div", { class: "chain-tree-node" },
          el("span", { class: "chain-tree-indent" }, indent),
          el("span", { class: "chain-tree-conn" }, connector),
          el("span", { class: "chain-tree-name" }, a.name),
          a.description ? el("span", { class: "chain-tree-desc" }, ` - ${a.description}`) : null,
        );
        if (a.call_instruction) {
          node.append(el("div", { class: "chain-tree-instr" },
            el("span", { class: "chain-tree-indent" }, indent + "    "),
            el("span", { class: "chain-tree-instr-text" }, `调用说明: ${a.call_instruction}`),
          ));
        }
        tree.append(node);
      }
    }
    card.append(tree);
    inner.append(card);
  }
}

function showCreateChainModal(existing = null) {
  const isEdit = !!existing;
  const agents = state.agents || [];

  const nameInput = el("input", {
    class: "input", placeholder: "链条名称（如 dev-deploy）",
  });
  if (existing?.name) nameInput.value = existing.name;
  if (isEdit) nameInput.disabled = true;

  const descInput = el("input", {
    class: "input", placeholder: "链条描述（可选）",
  });
  if (existing?.description) descInput.value = existing.description;

  // 层级构建器
  const levelsContainer = el("div", { class: "chain-levels-container" });
  const existingLevels = (existing?.levels || []).map(l => ({
    level: l.level,
    agents: l.agents.map(a => ({ name: a.name, call_instruction: a.call_instruction || "" })),
  }));
  if (!existingLevels.length) {
    existingLevels.push({ level: 1, agents: [{ name: "", call_instruction: "" }] });
  }

  function renderLevels() {
    levelsContainer.innerHTML = "";
    for (let li = 0; li < existingLevels.length; li++) {
      const level = existingLevels[li];
      const levelDiv = el("div", { class: "chain-level" },
        el("div", { class: "chain-level-header" },
          el("span", { class: "chain-level-num" }, `Level ${li + 1}${li === 0 ? " (元 Agent)" : ""}`),
          li > 0 ? el("button", { class: "btn btn-ghost btn-xs btn-danger",
            onclick: () => { existingLevels.splice(li, 1); renderLevels(); }
          }, "✕") : null,
        ),
      );

      for (let ai = 0; ai < level.agents.length; ai++) {
        const agent = level.agents[ai];
        const used = existingLevels.flatMap((l, idx) =>
          idx !== li ? l.agents.map(a => a.name) : []
        );
        const available = agents.filter(a => !used.includes(a.name) || a.name === agent.name);

        const sel = el("select", { class: "select chain-agent-select" },
          el("option", { value: "" }, "-- 选择 Agent --"),
          ...available.map(a => {
            const opt = el("option", { value: a.name },
              `${a.name} - ${a.description || ""}`);
            if (a.name === agent.name) opt.selected = true;
            return opt;
          }),
        );
        sel.addEventListener("change", () => { agent.name = sel.value; });

        const agentRow = el("div", { class: "chain-agent-row" }, sel);
        if (li > 0) {
          const instrInput = el("input", {
            class: "input chain-instr-input",
            placeholder: "调用说明（可选）",
          });
          instrInput.value = agent.call_instruction || "";
          instrInput.addEventListener("input", () => { agent.call_instruction = instrInput.value; });
          agentRow.append(instrInput);
          agentRow.append(el("button", { class: "btn btn-ghost btn-xs btn-danger",
            onclick: () => { level.agents.splice(ai, 1); renderLevels(); }
          }, "✕"));
        }
        levelDiv.append(agentRow);
      }

      if (li > 0) {
        levelDiv.append(el("button", { class: "btn btn-ghost btn-xs",
          onclick: () => { level.agents.push({ name: "", call_instruction: "" }); renderLevels(); }
        }, "✚ 添加同级 Agent"));
      }

      levelsContainer.append(levelDiv);
    }

    levelsContainer.append(el("button", { class: "btn btn-ghost btn-sm",
      onclick: () => {
        existingLevels.push({ level: existingLevels.length + 1, agents: [{ name: "", call_instruction: "" }] });
        renderLevels();
      }
    }, "✚ 添加下一层"));
  }

  renderLevels();

  const { close } = openModal({
    title: isEdit ? "编辑链条" : "新建链条",
    body: el("div", null,
      el("div", { class: "form-row" }, el("label", null, "链条名称"), nameInput),
      el("div", { class: "form-row" }, el("label", null, "描述"), descInput),
      el("div", { class: "form-row" }, el("label", null, "层级结构"), levelsContainer),
    ),
    footer: [
      el("button", { class: "btn", onclick: () => close() }, "取消"),
      el("button", { class: "btn btn-primary", onclick: async () => {
        const name = nameInput.value.trim();
        if (!name) { toast("请输入链条名称", "warn"); return; }
        for (const l of existingLevels) {
          for (const a of l.agents) {
            if (!a.name) { toast("请选择所有 Agent", "warn"); return; }
          }
        }
        const payload = {
          name,
          description: descInput.value.trim(),
          levels: existingLevels.map((l, i) => ({
            level: i + 1,
            agents: l.agents.map(a => ({
              name: a.name,
              ...(a.call_instruction ? { call_instruction: a.call_instruction } : {}),
            })),
          })),
        };
        try {
          if (isEdit) {
            await api.updateChain(existing.name, payload);
            toast("链条已更新", "success");
          } else {
            await api.createChain(payload);
            toast("链条已创建", "success");
          }
          close();
          loadChainsView();
        } catch (e) { toast(e.message, "error"); }
      } }, "保存"),
    ],
  });
}

async function showChainDetail(name) {
  try {
    const chain = await api.getChain(name);

    const treeDiv = el("div", { class: "chain-tree" });
    for (let i = 0; i < chain.levels.length; i++) {
      const level = chain.levels[i];
      const indent = "  ".repeat(i);
      for (let j = 0; j < level.agents.length; j++) {
        const a = level.agents[j];
        const isLast = j === level.agents.length - 1;
        const connector = i === 0 ? "" : (isLast ? "└── " : "├── ");
        const node = el("div", { class: "chain-tree-node" },
          el("span", { class: "chain-tree-indent" }, indent),
          el("span", { class: "chain-tree-conn" }, connector),
          el("span", { class: "chain-tree-name" }, a.name),
          el("span", { class: "chain-tree-desc" },
            ` (${a.model || "继承"}) - ${a.description || ""}`),
        );
        if (a.call_instruction) {
          node.append(el("div", { class: "chain-tree-instr" },
            el("span", { class: "chain-tree-indent" }, indent + "    "),
            el("span", { class: "chain-tree-instr-text" }, `调用说明: ${a.call_instruction}`),
          ));
        }
        treeDiv.append(node);
      }
    }

    const bodyParts = [];
    if (chain.description) {
      bodyParts.push(el("p", { style: "color:var(--text-1);font-size:13.5px;" }, chain.description));
    }
    if (!chain.valid) {
      bodyParts.push(el("div", { class: "badge badge-error" },
        `⚠️ 引用了不存在的 Agent: ${(chain.missing_agents || []).join(", ")}`));
    }
    bodyParts.push(treeDiv);

    const { close } = openModal({
      title: `🔗 ${chain.name}`,
      body: el("div", null, ...bodyParts),
      footer: [
        el("button", { class: "btn", onclick: () => close() }, "关闭"),
        el("button", { class: "btn btn-primary", onclick: async () => {
          try {
            await api.useChain(state.activeAgent, state.selectedModel, chain.name, state.currentSessionId);
            toast(`链条 '${chain.name}' 已激活`, "success");
            state.activeChain = chain.name;
            updateChainIndicator();
            close();
            switchView("chat");
          } catch (e) { toast(e.message, "error"); }
        } }, "🔗 激活链条"),
      ],
    });
  } catch (e) { toast(e.message, "error"); }
}

async function deleteChainConfirm(name) {
  const ok = await confirmDialog("删除链条",
    `确定删除链条 '${name}'？\n此操作不影响链条中引用的 Agent。`,
    { danger: true, okText: "删除" });
  if (!ok) return;
  try {
    await api.deleteChain(name);
    toast(`链条 '${name}' 已删除`, "success");
    if (state.activeChain === name) {
      state.activeChain = null;
      updateChainIndicator();
    }
    loadChainsView();
  } catch (e) { toast(e.message, "error"); }
}

function updateChainIndicator() {
  const btn = $("#btn-chain");
  if (!btn) return;
  if (state.activeChain) {
    btn.textContent = `🔗 ${state.activeChain}`;
    btn.title = `链条: ${state.activeChain}（点击切换/取消）`;
    btn.classList.add("chain-active");
  } else {
    btn.textContent = "🔗";
    btn.title = "Agent 链条（点击选择激活）";
    btn.classList.remove("chain-active");
  }
}

function initChainIndicator() {
  const btn = $("#btn-chain");
  if (!btn) return;
  btn.addEventListener("click", showChainPicker);
}

async function showChainPicker() {
  let chains = [];
  try {
    const data = await api.getChains();
    chains = data.chains || [];
  } catch (e) { toast(e.message, "error"); return; }

  // 只显示当前 Agent 是元 Agent 的链条（与 CLI 一致）
  const currentAgent = state.activeAgent;
  const available = chains.filter(c => {
    const rootLevel = (c.levels || [])[0];
    if (!rootLevel || !rootLevel.agents || !rootLevel.agents.length) return false;
    return rootLevel.agents[0].name === currentAgent;
  });

  const body = el("div", { class: "quick-list" });

  function makeRow(name, desc, isActive) {
    const radio = el("input", { type: "radio", name: "chain-pick", value: name });
    radio.checked = isActive;
    return el("label", { class: "quick-item", style: "cursor:pointer;" },
      el("div", { class: "quick-item-info" },
        el("div", { class: "quick-item-name" }, name === "" ? "无链条" : `🔗 ${name}`),
        el("div", { class: "quick-item-desc" }, desc)),
      el("label", { class: "switch" }, radio, el("span", { class: "track" })));
  }

  body.append(makeRow("", "普通单 Agent 模式", !state.activeChain));

  for (const c of available) {
    const desc = c.description || "";
    const invalid = !c.valid ? " (无效)" : "";
    body.append(makeRow(c.name, `${desc}${invalid}`, state.activeChain === c.name));
  }

  if (!available.length) {
    body.append(el("div", { class: "card-sub", style: "padding:16px;text-align:center;" },
      `当前 Agent '${currentAgent}' 没有以其为元 Agent 的链条。\n可切换到对应元 Agent 或在链条管理页面创建。`));
  }

  body.append(el("div", { style: "margin-top:12px;border-top:1px solid var(--border);padding-top:10px;" },
    el("button", {
      class: "btn btn-ghost btn-sm",
      onclick: () => { closeFn(); switchView("chains"); },
    }, "🔗 管理链条配置")));

  const { close: closeFn } = openModal({
    title: `🔗 Agent 链条 - ${currentAgent}`,
    width: "min(620px, calc(100vw - 40px))",
    body,
  });

  body.addEventListener("change", async (e) => {
    if (e.target.name !== "chain-pick") return;
    const chainName = e.target.value;
    try {
      if (chainName) {
        await api.useChain(state.activeAgent, state.selectedModel, chainName, state.currentSessionId);
        state.activeChain = chainName;
        toast(`链条 '${chainName}' 已激活`, "success");
      } else {
        await api.offChain(state.activeAgent, state.selectedModel, state.currentSessionId);
        state.activeChain = null;
        toast("链条绑定已取消", "success");
      }
      updateChainIndicator();
      closeFn();
    } catch (err) { toast(err.message, "error"); }
  });
}


/* ===================================================================
   8. Agent 管理视图
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
            m.vision ? el("span", { class: "tag purple" }, "视觉") : null,
            m.thinking !== undefined && m.thinking !== null
              ? el("span", { class: "tag" }, `thinking:${m.thinking}`) : null,
            m.reasoning_effort
              ? el("span", { class: "tag" }, `effort:${m.reasoning_effort}`) : null),
          el("div", { class: "card-sub" }, `${m.model} · ${m.url} · 上下文 ${fmtNum(m.context_limit)}`)),
        el("div", { class: "card-actions" },
          el("button", { class: "btn btn-sm", onclick: () => showModelForm(m) }, "编辑"),
          !isSel ? el("button", {
            class: "btn btn-sm",
            onclick: async () => {
              try {
                if (state.activeAgent) {
                  // 原地切换模型：保留当前会话及上下文（对齐 CLI /model use）
                  await api.chatSwitchModel(state.activeAgent, state.selectedModel, m.name, state.currentSessionId);
                } else {
                  await api.selectModel(m.name);
                }
              } catch (e) { toast(e.message, "error"); return; }
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
    body: el("div", null, ...rows),
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
  const maxTokensInput = el("input", {
    class: "input", type: "number", min: "1",
    value: m.max_tokens ?? "", placeholder: "留空用API默认值",
  });
  const thinkingSel = el("select", { class: "select" });
  thinkingSel.append(el("option", { value: "" }, "（不传）"));
  thinkingSel.append(el("option", { value: "true" }, "开启 (true)"));
  thinkingSel.append(el("option", { value: "false" }, "关闭 (false)"));
  thinkingSel.value = m.thinking === undefined || m.thinking === null ? "" : String(m.thinking);
  const reasoningSel = el("select", { class: "select" });
  reasoningSel.append(el("option", { value: "" }, "（不传）"));
  reasoningSel.append(el("option", { value: "minimum" }, "minimum"));
  reasoningSel.append(el("option", { value: "low" }, "low"));
  reasoningSel.append(el("option", { value: "medium" }, "medium"));
  reasoningSel.append(el("option", { value: "high" }, "high"));
  reasoningSel.append(el("option", { value: "xhigh" }, "xhigh"));
  reasoningSel.append(el("option", { value: "max" }, "max"));
  reasoningSel.value = m.reasoning_effort || "";
  const reasoningHint = el("div", { class: "form-hint" },
    "留空不传；推理强度 minimum / low / medium / high / xhigh / max");
  // thinking=off 时不能配置 reasoning_effort（DeepSeek 等 API 报 400）
  const syncReasoningState = () => {
    const off = thinkingSel.value === "false";
    reasoningSel.disabled = off;
    reasoningHint.textContent = off
      ? "⚠️ thinking 已关闭(off)，不能配置 reasoning_effort（API 会报 400）"
      : "留空不传；推理强度 minimum / low / medium / high / xhigh / max";
  };
  thinkingSel.addEventListener("change", syncReasoningState);
  syncReasoningState();

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
        el("div", { class: "form-row" }, el("label", null, "max_tokens"),
          maxTokensInput,
          el("div", { class: "form-hint" }, "留空用API默认值；思考模型建议设置（如 8192）")),
        el("div", { class: "form-row" }, el("label", null, "thinking"),
          thinkingSel,
          el("div", { class: "form-hint" }, "留空不传；开启/关闭思考模式（如 DeepSeek）")),
        el("div", { class: "form-row" }, el("label", null, "reasoning_effort"),
          reasoningSel, reasoningHint),
        el("div", { class: "form-row" }, el("label", null, " "),
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
          const maxTokensVal = maxTokensInput.value.trim();
          if (maxTokensVal !== "") {
            const mt = parseInt(maxTokensVal);
            if (isNaN(mt) || mt <= 0) { toast("max_tokens 需为正整数", "warn"); return; }
            payload.max_tokens = mt;
          }
          if (thinkingSel.value !== "") {
            payload.thinking = thinkingSel.value === "true";
          }
          const reasoningVal = reasoningSel.value;
          if (reasoningVal !== "") {
            // thinking=off 时不能配置 reasoning_effort（DeepSeek 等 API 报 400）
            if (payload.thinking === false) {
              toast("thinking 已关闭(off)，不能配置 reasoning_effort（API 会报 400）", "warn");
              return;
            }
            payload.reasoning_effort = reasoningVal;
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

/* ===================================================================
   安全视图（权限模式 + 权限规则 + Hooks 管理）
   =================================================================== */

const SEC_MODE_COLORS = { readonly: "blue", standard: "green", auto: "yellow", yolo: "red" };

async function loadSecurityView() {
  const root = $("#view-security");
  root.innerHTML = "";
  const [perm, hooksData] = await Promise.all([
    api.getPermissions(),
    currentAgent() ? api.getHooks(currentAgent()).catch(() => ({ hooks: {} })) : { hooks: {} },
  ]);

  const inner = pageShell("安全与钩子",
    "权限模式决定 AI 操作的放行策略；Hooks 是生命周期事件上自动执行的自定义命令（硬规则）");
  root.append(el("div", { class: "page" }, inner));

  /* ---- 权限模式（4 卡片） ---- */
  inner.append(el("div", { class: "section-title" }, "权限模式"));
  const modeGrid = el("div", { class: "mode-grid" });
  for (const m of perm.modes || []) {
    const color = SEC_MODE_COLORS[m.id] || "blue";
    const card = el("div", {
      class: `mode-card ${color}` + (m.current ? " active" : ""),
      title: "点击切换到此模式",
      onclick: async () => {
        if (m.current) return;
        if (m.id === "yolo") {
          const ok = await confirmDialog("开启 YOLO 模式?",
            "YOLO 模式将无确认执行一切操作（含 rm / git push 等），deny 红线降级为警告。\n\n确认开启？",
            { danger: true, okText: "开启 YOLO" });
          if (!ok) return;
        }
        try {
          await api.setPermissionMode(m.id);
          toast(`权限模式已切换: ${m.icon} ${m.label}`, m.id === "yolo" ? "warn" : "success");
          loadSecurityView();
          loadPermissionMode();  // 同步聊天头部徽标
        } catch (e) { toast(e.message, "error"); }
      },
    },
      el("div", { class: "mode-card-icon" }, m.icon),
      el("div", { class: "mode-card-name" }, m.id),
      el("div", { class: "mode-card-desc" }, m.desc),
      m.current ? el("div", { class: "tag " + color }, "当前") : null);
    modeGrid.append(card);
  }
  inner.append(modeGrid);
  inner.append(el("div", { class: "card-sub", style: "margin:6px 0 4px;" },
    `默认模式: ${perm.default_mode} · yolo_keep_deny: ${perm.yolo_keep_deny}（配置文件 ~/.cbhcli/permissions.json）`));

  /* ---- 权限规则管理 ---- */
  inner.append(el("div", { class: "section-title", style: "margin-top:18px;" }, "权限规则（用户自定义）"));

  const addRow = el("div", { class: "rule-add-row" });
  const catSel = el("select", { class: "select", style: "width:110px;" },
    el("option", { value: "allow" }, "✅ allow"),
    el("option", { value: "ask" }, "❓ ask"),
    el("option", { value: "deny" }, "🚫 deny"));
  const ruleInput = el("input", {
    class: "input", style: "flex:1;",
    placeholder: "规则，如 terminal(pytest:*) · edit(/project/**) · python(*)",
  });
  const addBtn = el("button", { class: "btn btn-primary btn-sm" }, "添加");
  const doAdd = async () => {
    const rule = ruleInput.value.trim();
    if (!rule) return;
    try {
      await api.updatePermissionRule("add", catSel.value, rule);
      toast(`已添加 ${catSel.value} 规则`, "success");
      loadSecurityView();
    } catch (e) { toast(e.message, "error"); }
  };
  addBtn.addEventListener("click", doAdd);
  ruleInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doAdd(); });
  addRow.append(catSel, ruleInput, addBtn);
  inner.append(addRow);

  const ruleCats = [
    ["deny", "🚫 deny（红线，硬拦截）", "red"],
    ["ask", "❓ ask（人工确认）", "amber"],
    ["allow", "✅ allow（免确认放行）", "green"],
  ];
  for (const [cat, title, color] of ruleCats) {
    const rules = (perm.rules && perm.rules[cat]) || [];
    const card = el("div", { class: "card", style: "margin-top:10px;" });
    card.append(el("div", { class: "card-title" }, title));
    if (!rules.length) {
      card.append(el("div", { class: "card-sub" }, "（无自定义规则）"));
    } else {
      const list = el("div", { class: "reorder-list" });
      for (const r of rules) {
        list.append(el("div", { class: "reorder-item" },
          el("span", { class: "reorder-name", style: "font-family:var(--font-mono);font-size:12px;" }, r),
          el("span", { class: `tag ${color}` }, cat),
          el("button", {
            class: "btn btn-sm btn-danger",
            onclick: async () => {
              try {
                await api.updatePermissionRule("rm", cat, r);
                toast("已删除", "success");
                loadSecurityView();
              } catch (e) { toast(e.message, "error"); }
            },
          }, "删除")));
      }
      card.append(list);
    }
    inner.append(card);
  }
  inner.append(el("div", { class: "card-sub", style: "margin-top:6px;" },
    "内置规则：deny 红线 14 条（rm -rf /、写 .env/.git 等）+ ask 危险操作 15 条 + allow 只读命令若干，随模式自动生效。优先级: deny > ask > allow"));

  /* ---- Hooks 管理 ---- */
  inner.append(el("div", { class: "section-title", style: "margin-top:18px;" },
    `Hooks 钩子（Agent: ${currentAgent() || "未选择"}）`));
  const hooksCard = el("div", { class: "card" });
  const hooks = hooksData.hooks || {};
  const events = Object.keys(hooks);
  if (!events.length) {
    hooksCard.append(el("div", { class: "card-sub" },
      "未配置钩子。配置文件: ~/.cbhcli/hooks.json（全局）或 <agent工作空间>/hooks.json"));
    hooksCard.append(el("pre", { class: "code-block", style: "margin-top:8px;font-size:11.5px;" },
      '示例:\n{\n  "PreToolUse": [\n    {"matcher": "terminal", "command": "python3 ~/guard.py"}\n  ]\n}'));
  } else {
    for (const ev of events) {
      hooksCard.append(el("div", { class: "hook-event" },
        el("span", { class: "tag blue" }, ev)));
      const list = el("div", { class: "reorder-list", style: "margin:4px 0 10px;" });
      for (const h of hooks[ev]) {
        list.append(el("div", { class: "reorder-item" },
          el("span", { class: "tag" }, h.matcher || "*"),
          el("span", { class: "reorder-name", style: "font-family:var(--font-mono);font-size:12px;" }, h.command)));
      }
      hooksCard.append(list);
    }
  }
  const reloadBtn = el("button", { class: "btn btn-sm" }, "🔄 重新加载 hooks.json");
  reloadBtn.addEventListener("click", async () => {
    if (!currentAgent()) { toast("请先选择 Agent", "warn"); return; }
    try {
      const r = await api.reloadHooks(currentAgent());
      toast(r.message, "success");
      loadSecurityView();
    } catch (e) { toast(e.message, "error"); }
  });
  hooksCard.append(el("div", { style: "margin-top:10px;" }, reloadBtn));
  inner.append(hooksCard);
}

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
                const ok = await confirmDialog("恢复会话", "将该历史会话恢复为当前对话？", { okText: "恢复" });
                if (!ok) return;
                try {
                  const r = await api.chatLoad(agent, currentModel(), s.filename, s.workspace);
                  toast(r.message, "success");
                  setSessionId(r.session_id || s.id || null);
                  if (r.workspace) state.currentWorkspace = r.workspace;
                  liveRunReset();
                  switchView("chat");
                  clearMessages();
                  renderRestoredMessages(r.messages || []);
                  if (r.usage) updateCtxMeter(r.usage);
                  wsSubscribe(r.session_id || s.id, r.run_active ? 0 : (r.run_seq || 0));
                  refreshWorkspaces();
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
  // 历史详情中渲染 mermaid / echarts 图表
  void renderDiagrams(body);

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

  // v5.2.8：原侧边栏各配置入口统一收归设置页
  const NAV_CARDS = [
    ["agents", "🤖", "Agent", "创建 / 管理 / 编辑 Agent"],
    ["chains", "🔗", "链条", "多 Agent 调用编排"],
    ["models", "🧠", "模型", "大模型 / 嵌入 / 重排序配置"],
    ["fallback", "🔄", "备用模型", "主模型异常自动切换"],
    ["skills", "⚡", "技能", "可复用提示词与脚本"],
    ["mcp", "🔌", "MCP", "外部工具服务器"],
    ["knowledge", "📚", "知识库", "知识文件管理与检索"],
    ["tools", "🔧", "工具", "内置工具开关"],
    ["security", "🛡️", "安全", "权限模式 / 钩子 / 回滚"],
    ["embedding", "🧭", "索引", "向量索引管理"],
    ["history", "🕘", "历史", "查看 / 恢复 / 删除历史会话"],
  ];
  inner.append(el("div", { class: "section-title" }, "️ 管理功能"));
  const grid = el("div", { class: "settings-nav-grid" });
  for (const [view, icon, label, desc] of NAV_CARDS) {
    const card = el("div", { class: "settings-nav-card" },
      el("div", { class: "settings-nav-icon" }, icon),
      el("div", null,
        el("div", { class: "settings-nav-label" }, label),
        el("div", { class: "settings-nav-desc" }, desc)));
    card.addEventListener("click", () => switchView(view));
    grid.append(card);
  }
  inner.append(grid);

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
  initSidebar();
  initPanelLayout();
  initFileManager();
  wsConnect();  // v5.2.9：WebSocket 实时通道（会话事件多浏览器同步）

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
  initChainIndicator();
  refreshSelectors();
  await restoreMessages();
  refreshStatus();
  refreshWorkspaces();
  refreshFileManager();
  updateChainIndicator();
  chatUI.input.focus();
}

document.addEventListener("DOMContentLoaded", bootstrap);
