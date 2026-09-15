/* ===================================================================
   CBHCLI ADMIN 管理服务 — 前端应用（原生 JS SPA，零构建）
   -------------------------------------------------------------------
   · hash 路由：#/login #/dashboard #/users #/sessions #/monitor
                #/audit #/settings
   · 管理员会话 token 存 sessionStorage（cbhcli_admin_token），
     所有管理 API 请求带 X-Admin-Token 请求头
   · 401：清除 token 并切回登录视图（改主密码接口的
     invalid_password 除外，见 api() 内注释）
   · 轮询：仪表盘 3s / 实时监控 2s / 运行中会话详情 2s
     document.hidden 时暂停（visibilitychange），切页/登出清除
   · XSS：API 文本一律 textContent 渲染；Markdown 用 marked 解析
     （经 sanitize 过滤）后渲染，marked 不可用时降级纯文本
   =================================================================== */

"use strict";

/* ===================================================================
   1. 常量与全局状态
   =================================================================== */

const API_BASE = "/admin/api";
const TOKEN_KEY = "cbhcli_admin_token";
const POLL_DASHBOARD = 3000;   // 仪表盘轮询间隔
const POLL_MONITOR = 2000;     // 实时监控轮询间隔
const POLL_DETAIL = 2000;      // 运行中会话详情轮询间隔
const POLL_HEALTH = 15000;     // 仪表盘健康卡片慢轮询
const PAGE_LIMIT = 100;        // 审计/会话分页大小
const SVC_INTERVAL = 20000;    // 顶栏服务状态探测间隔

const VIEW_NAMES = ["dashboard", "users", "sessions", "monitor", "audit", "settings"];

const state = {
  route: "",                 // 当前路由名
  navSeq: 0,                 // 导航代数（异步竞态保护）
  users: [],                 // 用户列表缓存（筛选下拉）
  eventTypes: null,          // 事件类型缓存
  sess: { userId: "", status: "", q: "", page: 0, total: 0, lastSig: "" },      // 会话筛选
  audit: { tab: "events", page: 0, type: "", userId: "", range: 0, total: 0 },
  loginRec: { page: 0, total: 0, userId: "" },
  drawer: null,              // 当前会话详情抽屉
};

/* ===================================================================
   2. 基础工具：DOM / 格式化 / 剪贴板 / Markdown
   =================================================================== */

function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

function clear(node) {
  if (!node) return;
  while (node.firstChild) node.removeChild(node.firstChild);
}

function appendChildren(node, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) { appendChildren(node, c); continue; }
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
}

/**
 * 创建元素。文本一律作为 textContent 子节点写入（XSS 安全）；
 * 仅 "html" 属性用于已消毒的 Markdown 输出（内部专用）。
 */
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v; // 仅接受 renderMarkdown() 的消毒输出
    else if (k === "style") node.style.cssText = v;
    else if (k === "dataset") { for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = dv; }
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  appendChildren(node, children);
  return node;
}

function enc(s) { return encodeURIComponent(String(s === null || s === undefined ? "" : s)); }

function nowSec() { return Date.now() / 1000; }

/** 把（epoch 秒 | epoch 数字串 | ISO 字符串）统一转成 epoch 秒；无效返回 0 */
function toSec(v) {
  if (v === null || v === undefined || v === "") return 0;
  if (typeof v === "number") return isFinite(v) ? v : 0;
  const s = String(v).trim();
  if (/^\d+(\.\d+)?$/.test(s)) return Number(s);
  const ms = Date.parse(s);
  return isFinite(ms) ? ms / 1000 : 0;
}

function pad2(n) { return String(n).padStart(2, "0"); }

/** 绝对时间 YYYY-MM-DD HH:MM:SS */
function fmtAbs(ts) {
  const s = toSec(ts);
  if (!s) return "—";
  const d = new Date(s * 1000);
  if (isNaN(d.getTime())) return "—";
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} `
    + `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** 仅时间 HH:MM:SS */
function fmtClock(ts) {
  const s = toSec(ts);
  if (!s) return "—";
  const d = new Date(s * 1000);
  if (isNaN(d.getTime())) return "—";
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** 相对时间：N 秒前 / N 分钟前 / N 小时前 / N 天前 */
function fmtRel(ts) {
  const s = toSec(ts);
  if (!s) return "—";
  let diff = nowSec() - s;
  if (diff < 0) diff = 0;
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

/** 时长格式化：X天X小时X分钟 */
function fmtDuration(seconds) {
  let s = Math.floor(Number(seconds));
  if (!isFinite(s) || s < 0) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}天${h}小时${m}分钟`;
  if (h > 0) return `${h}小时${m}分钟`;
  if (m > 0) return `${m}分钟`;
  return `${s}秒`;
}

/** 字节格式化：B / KB / MB / GB */
function fmtBytes(n) {
  const v = Number(n);
  if (!isFinite(v) || v < 0) return "—";
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1024 * 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} MB`;
  return `${(v / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtInt(n) {
  const v = Number(n);
  return isFinite(v) ? v.toLocaleString("en-US") : "—";
}

function truncate(s, n) {
  const str = String(s === null || s === undefined ? "" : s);
  return str.length > n ? `${str.slice(0, n)}…` : str;
}

function setText(node, text) {
  if (!node) return;
  node.textContent = (text === null || text === undefined || text === "") ? "—" : String(text);
}

/** token 有效期显示：null/空=永久，否则 N 天 */
function fmtTtl(v) {
  if (v === null || v === undefined || v === "") return "永久";
  const n = Number(v);
  return (isFinite(n) && n > 0) ? `${n} 天` : "永久";
}

/* ---- 剪贴板（navigator.clipboard 优先，失败降级 execCommand） ---- */
async function copyText(text) {
  const s = String(text === null || text === undefined ? "" : text);
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      await navigator.clipboard.writeText(s);
      return true;
    }
  } catch (e) { /* 继续降级 */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = s;
    ta.setAttribute("readonly", "readonly");
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0;";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch (e) {
    return false;
  }
}

/* ---- Markdown（marked，失败降级纯文本） ---- */
let markedReady = false;
if (window.marked && typeof window.marked.parse === "function") {
  try {
    window.marked.setOptions({ breaks: true, gfm: true });
    markedReady = true;
  } catch (e) {
    markedReady = false;
    console.warn("[admin] marked 初始化失败，Markdown 将降级为纯文本:", e);
  }
} else {
  console.warn("[admin] marked 未加载（/admin/vendor/marked.umd.js），Markdown 将降级为纯文本");
}

/** 轻度消毒 marked 输出：去脚本/事件属性/javascript: 链接 */
function sanitizeHtml(html) {
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    doc.querySelectorAll("script,iframe,object,embed,form,link,meta,style,base").forEach(n => n.remove());
    doc.querySelectorAll("*").forEach(node => {
      for (const attr of Array.from(node.attributes)) {
        const name = attr.name.toLowerCase();
        const val = String(attr.value || "").trim().toLowerCase();
        if (name.startsWith("on")) node.removeAttribute(attr.name);
        else if ((name === "href" || name === "src") && val.startsWith("javascript:")) node.removeAttribute(attr.name);
      }
    });
    return doc.body.innerHTML;
  } catch (e) {
    return String(html).replace(/[<>]/g, ch => (ch === "<" ? "&lt;" : "&gt;"));
  }
}

/** 渲染 Markdown 为内容块；marked 不可用/解析失败时降级为 textContent */
function mdBlock(text) {
  const div = el("div", { class: "md" });
  const src = String(text === null || text === undefined ? "" : text);
  let html = null;
  if (markedReady && src) {
    try { html = sanitizeHtml(window.marked.parse(src)); } catch (e) { html = null; }
  }
  if (html === null) div.textContent = src;
  else div.innerHTML = html;
  return div;
}

/** 提取消息内容文本：str 原样；list（多模态）取 type==text 拼接 */
function extractText(content) {
  if (content === null || content === undefined) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts = [];
    for (const item of content) {
      if (typeof item === "string") { parts.push(item); continue; }
      if (item && typeof item === "object" && item.type === "text" && typeof item.text === "string") {
        parts.push(item.text);
      }
    }
    if (parts.length) return parts.join("\n");
    const kinds = content.map(it => (it && typeof it === "object" ? it.type : typeof it)).filter(Boolean);
    return kinds.length ? `[${kinds.join(", ")} 内容]` : "";
  }
  if (typeof content === "object") {
    try { return JSON.stringify(content, null, 2); } catch (e) { return String(content); }
  }
  return String(content);
}

/* ===================================================================
   3. API 层（token / 401 处理 / 错误封装）
   =================================================================== */

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "ApiError";
    this.status = status || 0;
    this.code = code || "error";
  }
}

function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
}

function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch (e) { /* sessionStorage 不可用时忽略 */ }
}

let sessionExpiredBusy = false;

/** 管理员会话失效：清 token、停轮询、回登录视图 */
function onSessionExpired() {
  if (sessionExpiredBusy) return;
  sessionExpiredBusy = true;
  setToken("");
  stopServiceWatch();
  Poll.clear();
  LiveWS.disconnect();
  closeDrawer();
  closeAllModals();
  showLoginView("管理员会话无效或已过期，请重新登录");
  toast("管理员会话无效或已过期，请重新登录", "warn");
  setTimeout(() => { sessionExpiredBusy = false; }, 600);
}

/**
 * 统一请求封装。
 * opts: { method, body, on401: "logout" | "error" }
 *  · 默认 on401="logout"：任何 401 → 清除 token 并切回登录视图；
 *  · on401="error"：401 交由调用方处理（仅 /login 与 /settings/password 使用；
 *    其中改主密码接口的 401 invalid_password=当前主密码错误，
 *    此时管理员会话本身有效，不应误登出；同接口的 unauthorized 仍会登出）。
 */
async function api(path, opts = {}) {
  const { method = "GET", body, on401 = "logout", headers = {} } = opts;
  const h = Object.assign({}, headers);
  const token = getToken();
  if (token) h["X-Admin-Token"] = token;
  let payload;
  if (body !== undefined) {
    h["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(API_BASE + path, { method, headers: h, body: payload });
  } catch (e) {
    markService(false);
    throw new ApiError("网络请求失败，请确认管理服务正在运行", 0, "network_error");
  }
  markService(true);

  if (res.status === 401) {
    let err = {};
    try { err = await res.json(); } catch (e) { err = {}; }
    const code = err.error || "unauthorized";
    const sessionInvalid = (code === "unauthorized");
    if (on401 === "logout" || sessionInvalid) onSessionExpired();
    throw new ApiError(err.message || "管理员会话无效或已过期，请重新登录", 401, code);
  }
  if (!res.ok) {
    let err = {};
    try { err = await res.json(); } catch (e) { err = {}; }
    throw new ApiError(err.message || `请求失败（HTTP ${res.status}）`, res.status, err.error || "http_error");
  }
  try {
    return await res.json();
  } catch (e) {
    throw new ApiError("服务返回了无法解析的数据", res.status, "bad_json");
  }
}

/** 导出 CSV：需要带 X-Admin-Token，用 fetch + blob 下载 */
async function downloadCsv(path, filename) {
  const h = {};
  const token = getToken();
  if (token) h["X-Admin-Token"] = token;
  let res;
  try {
    res = await fetch(API_BASE + path, { headers: h });
  } catch (e) {
    markService(false);
    throw new ApiError("网络请求失败，请确认管理服务正在运行", 0, "network_error");
  }
  markService(true);
  if (res.status === 401) {
    let err = {};
    try { err = await res.json(); } catch (e) { err = {}; }
    if (!err.error || err.error === "unauthorized") onSessionExpired();
    throw new ApiError(err.message || "管理员会话无效", 401, err.error || "unauthorized");
  }
  if (!res.ok) throw new ApiError(`导出失败（HTTP ${res.status}）`, res.status, "http_error");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: filename || "export.csv", style: "display:none;" });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/* ===================================================================
   4. 轮询管理器（setInterval + 页面隐藏暂停 + 切页/登出清除）
   =================================================================== */

const Poll = {
  items: new Set(),

  /** 注册轮询：fn 每次返回 Promise（错误已内部吞掉）；runNow=true 注册后立即执行一次 */
  add(fn, ms, runNow = true) {
    const item = { fn, ms, timer: null };
    this.items.add(item);
    const wrapped = () => {
      try {
        const p = fn();
        if (p && typeof p.catch === "function") p.catch(() => {});
      } catch (e) { /* 忽略轮询异常 */ }
    };
    item.wrapped = wrapped;
    if (!document.hidden) {
      if (runNow) wrapped();
      item.timer = setInterval(wrapped, ms);
    }
    return item;
  },

  remove(item) {
    if (!item) return;
    if (item.timer) clearInterval(item.timer);
    this.items.delete(item);
  },

  /** 切页/登出时清除全部定时器 */
  clear() {
    for (const item of this.items) {
      if (item.timer) clearInterval(item.timer);
      item.timer = null;
    }
    this.items.clear();
  },

  /** 页面隐藏 → 暂停 */
  pause() {
    for (const item of this.items) {
      if (item.timer) { clearInterval(item.timer); item.timer = null; }
    }
  },

  /** 页面恢复可见 → 恢复（立即拉一次再进入定时） */
  resume() {
    for (const item of this.items) {
      if (!item.timer) {
        item.wrapped();
        item.timer = setInterval(item.wrapped, item.ms);
      }
    }
  },
};

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    Poll.pause();
  } else {
    Poll.resume();
    if (svcFn && getToken()) svcFn(); // 顶栏服务状态也立即刷新
  }
});

/* ===================================================================
   4.1 实时事件流（WebSocket /admin/ws）
   -------------------------------------------------------------------
   管理服务端把客户端上报的审计事件（轮开始/思考/正文/工具调用/轮结束）
   实时广播到本连接：
   · 会话详情抽屉 → 渲染"和 Web 一致的实时交互画面"
   · 实时监控看板 → 事件到达即时刷新（叠加原有 2s 轮询兜底）
   断开自动指数退避重连（1s→30s）；未登录不连接；登出/会话失效断开。
   =================================================================== */

const LiveWS = {
  sock: null,
  ready: false,
  retry: 0,
  timer: null,
  pingTimer: null,
  listeners: new Set(),

  connect() {
    if (!getToken()) return;
    this.disconnect();
    const proto = (location.protocol === "https:") ? "wss://" : "ws://";
    const url = `${proto}${location.host}/admin/ws?token=${encodeURIComponent(getToken())}`;
    let sock;
    try { sock = new WebSocket(url); } catch (e) { this._scheduleReconnect(); return; }
    this.sock = sock;
    sock.onopen = () => {
      this.ready = true;
      this.retry = 0;
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.pingTimer = setInterval(() => {
        try {
          if (this.ready && this.sock) this.sock.send(JSON.stringify({ type: "ping" }));
        } catch (e) { /* 忽略 */ }
      }, 25000);
    };
    sock.onmessage = (e) => { this._dispatch(e && e.data); };
    sock.onclose = () => { this._teardown(); this._scheduleReconnect(); };
    sock.onerror = () => { try { sock.close(); } catch (e) { /* 忽略 */ } };
  },

  disconnect() {
    this._teardown();
  },

  _teardown() {
    this.ready = false;
    if (this.pingTimer) { clearInterval(this.pingTimer); this.pingTimer = null; }
    if (this.sock) {
      try { this.sock.onclose = null; this.sock.close(); } catch (e) { /* 忽略 */ }
      this.sock = null;
    }
  },

  _scheduleReconnect() {
    if (this.timer || !getToken()) return;
    const delay = Math.min(30000, 1000 * Math.pow(1.8, Math.min(this.retry, 8)));
    this.retry += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, delay);
  },

  _dispatch(raw) {
    let msg = null;
    try { msg = JSON.parse(raw); } catch (e) { return; }
    if (!msg || msg.type !== "event") return;
    for (const fn of Array.from(this.listeners)) {
      try { fn(msg); } catch (e) { /* 单监听器异常不影响其它 */ }
    }
  },

  addListener(fn) { this.listeners.add(fn); return fn; },
  removeListener(fn) { this.listeners.delete(fn); },
};

/* ---- 顶栏服务状态探测（全局，独立于页面轮询） ---- */
let svcTimer = null;
let svcFn = null;

function markService(ok) {
  const dot = $("#svc-dot");
  const txt = $("#svc-text");
  if (!dot || !txt) return;
  dot.className = "dot " + (ok ? "green" : "red");
  txt.textContent = ok ? "服务正常" : "服务异常";
}

function startServiceWatch() {
  stopServiceWatch();
  svcFn = async () => {
    try {
      const hl = await api("/health");
      if (hl && hl.version) setVersion(hl.version);
      markService(true);
    } catch (e) {
      if (e && e.status !== 401) markService(false);
    }
  };
  svcFn();
  svcTimer = setInterval(() => { if (!document.hidden) svcFn(); }, SVC_INTERVAL);
}

function stopServiceWatch() {
  if (svcTimer) { clearInterval(svcTimer); svcTimer = null; }
  svcFn = null;
}

/* ===================================================================
   5. Toast / 模态框 / 抽屉
   =================================================================== */

function toast(msg, type = "info", duration = 3200) {
  const root = $("#toast-root");
  if (!root) return;
  const cls = (type && type !== "info") ? `toast ${type}` : "toast";
  const t = el("div", { class: cls }, msg);
  root.append(t);
  setTimeout(() => {
    t.classList.add("fade");
    setTimeout(() => t.remove(), 320);
  }, duration);
}

const modalStack = new Set();

function openModal({ title, body, footer, width, closeOnMask = true }) {
  const mask = el("div", { class: "modal-mask" });
  const modal = el("div", { class: "modal" });
  if (width) modal.style.width = width;

  const close = () => {
    mask.remove();
    document.removeEventListener("keydown", onKey);
    modalStack.delete(close);
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };

  modal.append(
    el("div", { class: "modal-header" },
      el("div", { class: "modal-title" }, title),
      el("button", { class: "modal-close", type: "button", onclick: close }, "✕")),
    el("div", { class: "modal-body" }, body),
  );

  const footChildren = footer ? (Array.isArray(footer) ? footer : [footer]) : null;
  if (footChildren && footChildren.length) {
    modal.append(el("div", { class: "modal-footer" }, ...footChildren));
  }

  mask.append(modal);
  if (closeOnMask) mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
  $("#modal-root").append(mask);
  document.addEventListener("keydown", onKey);
  modalStack.add(close);
  return { close, modal, mask };
}

function closeAllModals() {
  for (const close of Array.from(modalStack)) {
    try { close(); } catch (e) { /* 忽略 */ }
  }
}

function confirmDialog(title, message, { danger = false, okText = "确认" } = {}) {
  return new Promise((resolve) => {
    const { close } = openModal({
      title,
      body: el("p", { style: "color:var(--text-1);font-size:13.5px;white-space:pre-wrap;" }, message),
      footer: [
        el("button", { class: "btn", type: "button", onclick: () => { close(); resolve(false); } }, "取消"),
        el("button", {
          class: danger ? "btn btn-danger" : "btn btn-primary",
          type: "button",
          onclick: () => { close(); resolve(true); },
        }, okText),
      ],
    });
  });
}

function emptyState(text, icon = "🗂") {
  return el("div", { class: "empty-state" },
    icon ? el("div", { class: "icon" }, icon) : null,
    el("div", null, text));
}

/* ---- 会话详情抽屉 ---- */

function closeDrawer() {
  const d = state.drawer;
  if (!d) return;
  if (d.poll) Poll.remove(d.poll);
  if (d.refreshTimer) { clearTimeout(d.refreshTimer); d.refreshTimer = null; }
  if (d.wsListener) { LiveWS.removeListener(d.wsListener); d.wsListener = null; }
  document.removeEventListener("keydown", onDrawerKey);
  if (d.mask) d.mask.remove();
  if (d.drawer) d.drawer.remove();
  state.drawer = null;
}

function onDrawerKey(e) {
  if (e.key === "Escape") closeDrawer();
}

/* ===================================================================
   6. 登录 / 登出 / 布局 / 路由
   =================================================================== */

function setLoginError(text) {
  const node = $("#login-error");
  if (!node) return;
  if (text) { node.textContent = text; node.classList.remove("hidden"); }
  else { node.textContent = ""; node.classList.add("hidden"); }
}

function showLoginView(errText) {
  const shell = $("#app-shell");
  const login = $("#login-view");
  if (shell) shell.classList.add("hidden");
  if (login) login.classList.remove("hidden");
  setLoginError(errText || "");
  const input = $("#login-password");
  if (input) { input.value = ""; setTimeout(() => { try { input.focus(); } catch (e) {} }, 80); }
}

function showAppShell() {
  const shell = $("#app-shell");
  const login = $("#login-view");
  if (login) login.classList.add("hidden");
  if (shell) shell.classList.remove("hidden");
}

function setVersion(ver) {
  const text = ver ? `cbhcli ${ver}` : "cbhcli admin";
  const f1 = $("#sidebar-footer");
  const f2 = $("#login-footer");
  if (f1) f1.textContent = text;
  if (f2) f2.textContent = text;
}

function startClock() {
  const node = $("#topbar-clock");
  if (!node) return;
  const tick = () => { node.textContent = fmtAbs(nowSec()); };
  tick();
  setInterval(tick, 1000);
}

function setActiveNav(name) {
  $$("#sidebar-nav .nav-item").forEach(n =>
    n.classList.toggle("active", n.dataset.route === name));
}

function activateView(name) {
  for (const v of VIEW_NAMES) {
    const node = $(`#view-${v}`);
    if (node) node.classList.toggle("active", v === name);
  }
  const content = $("#content");
  if (content) content.scrollTop = 0;
}

function parseHash() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  const qIdx = raw.indexOf("?");
  const pathPart = qIdx < 0 ? raw : raw.slice(0, qIdx);
  const queryPart = qIdx < 0 ? "" : raw.slice(qIdx + 1);
  const route = (pathPart || "").split("/")[0] || "";
  const params = {};
  if (queryPart) {
    for (const kv of queryPart.split("&")) {
      if (!kv) continue;
      const i = kv.indexOf("=");
      const k = i < 0 ? kv : kv.slice(0, i);
      const v = i < 0 ? "" : kv.slice(i + 1);
      try { params[decodeURIComponent(k)] = decodeURIComponent(v); } catch (e) { params[k] = v; }
    }
  }
  return { route, params };
}

/** 路由分发：切页时清除定时器、关闭抽屉/模态框 */
function handleRoute() {
  const { route, params } = parseHash();
  if (!getToken()) {
    // 未登录：仅当主界面还可见（如会话刚失效）时才重置登录视图，
    // 避免重复调用 showLoginView() 清掉登录错误提示
    const shell = $("#app-shell");
    if (shell && !shell.classList.contains("hidden")) showLoginView();
    return;
  }

  let name = route;
  if (!name || name === "login" || VIEW_NAMES.indexOf(name) < 0) {
    if (!name || name === "login") { location.hash = "#/dashboard"; return; }
    name = "dashboard"; // 未知路由回落
    if (name !== route) { location.hash = "#/dashboard"; return; }
  }

  state.route = name;
  state.navSeq += 1;
  const seq = state.navSeq;

  Poll.clear();          // 切页清除全部页面定时器
  closeDrawer();         // 关闭会话抽屉（含其轮询）
  closeAllModals();

  showAppShell();
  setActiveNav(name);
  activateView(name);

  const loaders = {
    dashboard: () => loadDashboard(seq),
    users: () => loadUsersView(params, seq),
    sessions: () => loadSessionsView(params, seq),
    monitor: () => loadMonitorView(seq),
    audit: () => loadAuditView(params, seq),
    settings: () => loadSettingsView(params, seq),
  };
  loaders[name]();
}

async function doLogout() {
  try {
    if (getToken()) await api("/logout", { method: "POST", on401: "error" });
  } catch (e) { /* 登出接口失败不阻塞本地清理 */ }
  setToken("");
  stopServiceWatch();
  Poll.clear();
  LiveWS.disconnect();
  closeDrawer();
  closeAllModals();
  state.users = [];
  state.eventTypes = null;
  location.hash = "#/login";
  showLoginView("");
  toast("已退出登录", "success");
}

function initLogin() {
  const form = $("#login-form");
  const input = $("#login-password");
  if (!form || !input) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const password = input.value;
    if (!password) { setLoginError("请输入主密码"); return; }
    const btn = $("#login-btn");
    btn.disabled = true;
    btn.textContent = "登录中…";
    setLoginError("");
    try {
      const res = await api("/login", { method: "POST", body: { password }, on401: "error" });
      setToken(res.admin_token || "");
      if (res.version) setVersion(res.version);
      input.value = "";
      showAppShell();
      startServiceWatch();
      LiveWS.connect();
      toast(`登录成功（cbhcli ${res.version || "?"}）`, "success");
      if (location.hash === "#/dashboard") handleRoute();
      else location.hash = "#/dashboard";
    } catch (err) {
      if (err.status === 401) setLoginError("主密码错误，请重试");
      else if (err.status === 429) setLoginError(err.message || "登录失败次数过多，请稍后再试");
      else setLoginError(err.message || "登录失败");
    } finally {
      btn.disabled = false;
      btn.textContent = "登 录";
    }
  });
}

/* ===================================================================
   7. 通用小组件
   =================================================================== */

function badge(text, color, extraClass) {
  const cls = "badge" + (color ? ` ${color}` : "") + (extraClass ? ` ${extraClass}` : "");
  const label = (text === null || text === undefined) ? "—" : String(text);
  return el("span", { class: cls, title: label }, label);
}

/** 事件类型 → 徽标颜色（按需求映射） */
function eventBadgeClass(type) {
  const t = String(type || "");
  if (t.indexOf("tool_call") >= 0) return "blue";
  if (t.indexOf("tool_blocked") >= 0) return "red";
  if (t.indexOf("mode_change") >= 0) return "purple";
  if (t.indexOf("auth.") === 0 || t.indexOf("admin_action.") === 0) return "green";
  if (t.indexOf("error") >= 0 || t.indexOf("fail") >= 0) return "red";
  return "gray";
}

/** 事件 payload → 单行摘要（如 tool=terminal args=ls） */
function fmtPayloadValue(v) {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return truncate(v.replace(/\s+/g, " "), 60);
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `[${v.length} 项]`;
  if (typeof v === "object") {
    try { return truncate(JSON.stringify(v), 60); } catch (e) { return "[对象]"; }
  }
  return String(v);
}

function summarizePayload(payload) {
  if (payload === null || payload === undefined) return "";
  if (typeof payload !== "object") return truncate(String(payload), 160);
  const parts = [];
  let keys = [];
  try { keys = Object.keys(payload); } catch (e) { keys = []; }
  for (const k of keys) {
    if (parts.length >= 8) { parts.push("…"); break; }
    parts.push(`${k}=${fmtPayloadValue(payload[k])}`);
  }
  return truncate(parts.join(" "), 220);
}

/** 顶栏导航项加载用户下拉选项 */
function fillUserSelect(sel, { placeholder = "全部用户", value = "" } = {}) {
  if (!sel) return;
  clear(sel);
  sel.append(el("option", { value: "" }, placeholder));
  for (const u of state.users) {
    const label = u.display_name ? `${u.name}（${u.display_name}）` : u.name;
    sel.append(el("option", { value: u.id }, label));
  }
  sel.value = value || "";
}

async function ensureUsers(force = false) {
  if (state.users.length && !force) return state.users;
  const res = await api("/users");
  state.users = res.users || [];
  return state.users;
}

/* 有效期选择器（7/30/90/365/自定义/永久） */
function buildTtlPicker({ value = 30 } = {}) {
  const sel = el("select", { class: "select" });
  const opts = [["7", "7 天"], ["30", "30 天"], ["90", "90 天"], ["365", "365 天"],
    ["custom", "自定义…"], ["forever", "永久（不设置有效期）"]];
  for (const [v, label] of opts) sel.append(el("option", { value: v }, label));

  const num = el("input", { class: "input", type: "number", min: "1", step: "1", placeholder: "天数", style: "width:110px;" });

  const sync = () => { num.classList.toggle("hidden", sel.value !== "custom"); };
  sel.addEventListener("change", sync);

  // 初始值：数字命中预设则选中，否则自定义 / 永久
  if (value === null || value === undefined) {
    sel.value = "forever";
  } else {
    const s = String(value);
    sel.value = ["7", "30", "90", "365"].indexOf(s) >= 0 ? s : "custom";
    if (sel.value === "custom") num.value = s;
  }
  sync();

  const node = el("div", { class: "btn-row" }, sel, num);
  return {
    node,
    select: sel,
    num,
    /** 返回 number（天数，非法为 NaN）| null（永久） */
    getValue() {
      if (sel.value === "forever") return null;
      if (sel.value === "custom") {
        const n = parseInt(num.value, 10);
        return (isFinite(n) && n > 0) ? n : NaN;
      }
      return parseInt(sel.value, 10);
    },
  };
}

/* ===================================================================
   8. 仪表盘（#/dashboard）
   =================================================================== */

const dashUI = { built: false };

function buildDashboard() {
  if (dashUI.built) return;
  dashUI.built = true;
  const root = $("#view-dashboard");
  clear(root);

  const statCard = (label, id, color) => {
    const value = el("div", { class: "stat-value" }, "—");
    const card = el("div", { class: "stat-card" + (color ? ` ${color}` : "") },
      el("div", { class: "stat-label" }, label), value);
    dashUI[id] = value;
    return card;
  };

  dashUI.events = el("div", { class: "list list-scroll", id: "dash-events" }, emptyState("暂无事件", "⚡"));
  dashUI.online = el("div", { class: "list", id: "dash-online" }, emptyState("暂无在线用户", "🟢"));
  dashUI.onlineCount = el("div", { class: "card-sub" }, "");
  dashUI.health = el("div", { class: "kv-grid", id: "dash-health" }, el("div", { class: "kv-item" }, el("div", { class: "kv-label" }, "状态"), el("div", { class: "kv-value" }, "加载中…")));

  const mkSelfCheck = () => el("button", { class: "btn btn-sm", type: "button", onclick: () => runSelfCheck() }, "🔄 重新自检");

  root.append(
    el("div", { class: "page-header" },
      el("div", null,
        el("div", { class: "page-title" }, "仪表盘"),
        el("div", { class: "page-desc" }, "管理服务总览 · 每 3 秒自动刷新")),
      el("div", { class: "page-actions" },
        el("button", { class: "btn btn-sm", type: "button", onclick: () => manualDashboardRefresh() }, "🔄 刷新"),
        mkSelfCheck())),

    el("div", { class: "stats-grid" },
      statCard("用户总数", "statUsers"),
      statCard("在线", "statOnline", "green"),
      statCard("今日会话", "statToday", "amber"),
      statCard("运行中", "statRunning", "purple")),

    el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("div", { class: "card-title" }, "🩺 服务健康"),
        el("div", { class: "card-actions" }, mkSelfCheck())),
      dashUI.health),

    el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("div", { class: "card-title" }, "⚡ 实时活动流"),
        el("div", { class: "card-sub" }, "最近 30 条事件 · 每 3 秒刷新")),
      dashUI.events),

    el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("div", { class: "card-title" }, "🟢 在线用户"),
        dashUI.onlineCount),
      dashUI.online),
  );
}

async function manualDashboardRefresh() {
  try {
    await refreshDashboardOverview();
    toast("已刷新", "success");
  } catch (e) {
    if (e.status !== 401) toast(e.message || "刷新失败", "error");
  }
}

async function runSelfCheck() {
  try {
    await refreshDashboardAll();
    toast("自检完成", "success");
  } catch (e) {
    if (e.status !== 401) toast(e.message || "自检失败", "error");
  }
}

function renderDashboardStats(ov) {
  const st = (ov && ov.stats) || {};
  setText(dashUI.statUsers, fmtInt(st.users));
  setText(dashUI.statOnline, fmtInt(st.online));
  setText(dashUI.statToday, fmtInt(st.today_sessions));
  setText(dashUI.statRunning, fmtInt(st.running));
}

function renderDashboardEvents(events) {
  const box = dashUI.events;
  if (!box) return;
  clear(box);
  if (!events || !events.length) { box.append(emptyState("暂无事件", "⚡")); return; }
  for (const ev of events) {
    box.append(el("div", { class: "list-item" },
      el("span", { class: "li-time", title: fmtAbs(ev.ts) }, fmtClock(ev.ts)),
      el("span", { class: "li-user", title: ev.user_name || ev.user_id || "" }, ev.user_name || ev.user_id || "—"),
      badge(ev.type || "—", eventBadgeClass(ev.type)),
      el("span", { class: "li-summary", title: summarizePayload(ev.payload) }, summarizePayload(ev.payload) || "—")));
  }
}

function renderDashboardOnline(users) {
  const box = dashUI.online;
  if (!box) return;
  clear(box);
  const list = users || [];
  if (dashUI.onlineCount) dashUI.onlineCount.textContent = `${list.length} 人在线`;
  if (!list.length) { box.append(emptyState("暂无在线用户", "🟢")); return; }
  for (const u of list) {
    const client = u.client || {};
    const surface = client.surface || (Array.isArray(u.surfaces) && u.surfaces.length ? u.surfaces[0] : "");
    box.append(el("div", { class: "list-item" },
      el("span", { class: "dot green", style: "width:7px;height:7px;" }),
      el("span", { class: "li-user", title: u.display_name || "" }, u.name || u.user_id || "—"),
      el("span", { class: "li-summary" }, (client.hostname || "—") + (surface ? ` · ${surface}` : "")),
      el("span", { class: "li-time", title: fmtAbs(u.last_seen) }, fmtRel(u.last_seen))));
  }
}

/** 健康信息（仪表盘 / 设置页复用）；lastEventTs 可选覆盖「最近上报」 */
function buildHealthItems(hl, lastEventTs) {
  const items = [];
  const add = (label, value) => {
    items.push(el("div", { class: "kv-item" },
      el("div", { class: "kv-label" }, label),
      el("div", { class: "kv-value" }, value)));
  };
  const lastTs = lastEventTs || (hl && hl.last_event_ts) || 0;
  add("运行时长", fmtDuration(hl.uptime_seconds));
  add("版本", hl.version ? `cbhcli ${hl.version}` : "—");
  add("数据库", (hl.db_ok ? "✓ 正常" : "✗ 异常") + ` · ${fmtBytes(hl.db_size)}`);
  add("快照数", fmtInt(hl.snapshot_count));
  add("最近上报", lastTs ? fmtRel(lastTs) : "暂无");
  add("启动时间", fmtAbs(hl.started_at));
  add("磁盘剩余", `${hl.disk_free || "?"} / ${hl.disk_total || "?"}`);
  add("数据目录", hl.data_dir || "—");
  return items;
}

function renderDashboardHealth(hl, lastEventTs) {
  const box = dashUI.health;
  if (!box || !hl) return;
  clear(box);
  appendChildren(box, buildHealthItems(hl, lastEventTs));
}

async function refreshDashboardOverview() {
  const seq = state.navSeq;
  const ov = await api("/overview");
  if (state.navSeq !== seq || state.route !== "dashboard") return;
  renderDashboardStats(ov);
  renderDashboardEvents(ov.recent_events || []);
  renderDashboardOnline(ov.online_users || []);
  const lastTs = (ov.recent_events && ov.recent_events.length) ? ov.recent_events[0].ts : 0;
  dashUI.lastEventTs = lastTs;
  markService(true);
}

async function refreshDashboardHealth() {
  const seq = state.navSeq;
  const hl = await api("/health");
  if (state.navSeq !== seq || state.route !== "dashboard") return;
  renderDashboardHealth(hl, dashUI.lastEventTs);
  if (hl.version) setVersion(hl.version);
  markService(true);
}

async function refreshDashboardAll() {
  const seq = state.navSeq;
  const results = await Promise.all([
    api("/overview"),
    api("/health").catch(() => null),
  ]);
  if (state.navSeq !== seq || state.route !== "dashboard") return;
  const ov = results[0];
  const hl = results[1];
  renderDashboardStats(ov);
  renderDashboardEvents(ov.recent_events || []);
  renderDashboardOnline(ov.online_users || []);
  const lastTs = (ov.recent_events && ov.recent_events.length) ? ov.recent_events[0].ts : 0;
  dashUI.lastEventTs = lastTs;
  if (hl) {
    renderDashboardHealth(hl, lastTs);
    if (hl.version) setVersion(hl.version);
  }
  markService(true);
}

function loadDashboard(seq) {
  buildDashboard();
  refreshDashboardAll().catch(e => {
    if (e.status !== 401) toast(e.message || "概览加载失败", "error");
  });
  // 仪表盘 3s 轮询（页面隐藏暂停；切页清除）
  Poll.add(async () => {
    try { await refreshDashboardOverview(); }
    catch (e) { if (e.status !== 401) markService(false); }
  }, POLL_DASHBOARD, false);
  // 健康卡片慢轮询（15s）
  Poll.add(async () => {
    try { await refreshDashboardHealth(); }
    catch (e) { if (e.status !== 401) markService(false); }
  }, POLL_HEALTH, false);
}

/* ===================================================================
   9. 用户管理（#/users）
   =================================================================== */

const usersUI = { built: false, tbody: null };

function buildUsersView() {
  if (usersUI.built) return;
  usersUI.built = true;
  const root = $("#view-users");
  clear(root);
  usersUI.tbody = el("tbody", { id: "users-tbody" });

  const headers = ["用户名", "显示名", "状态", "最后登录", "最后活跃", "密钥", "有效期", "操作"];
  root.append(
    el("div", { class: "page-header" },
      el("div", null,
        el("div", { class: "page-title" }, "用户管理"),
        el("div", { class: "page-desc" }, "创建 / 续期 / 重置密钥 / 权限快照")),
      el("div", { class: "page-actions" },
        el("button", { class: "btn btn-sm", type: "button", onclick: () => refreshUsers(true) }, "🔄 刷新"),
        el("button", { class: "btn btn-primary btn-sm", type: "button", onclick: openCreateUserModal }, "＋ 创建用户"))),
    el("div", { class: "table-wrap" },
      el("table", { class: "table" },
        el("thead", null, el("tr", null, ...headers.map(h => el("th", null, h)))),
        usersUI.tbody)),
  );
}

async function loadUsersView(params, seq) {
  buildUsersView();
  try {
    await refreshUsers(false);
  } catch (e) {
    if (e.status !== 401) toast(e.message || "用户列表加载失败", "error");
  }
}

async function refreshUsers(showToast) {
  const res = await api("/users");
  state.users = res.users || [];
  renderUsersTable();
  if (showToast) toast("已刷新用户列表", "success");
  return state.users;
}

function userStatusBadge(u) {
  if (u.disabled) return el("span", { class: "badge gray" }, "已禁用");
  if (u.online) return el("span", { class: "badge green" }, "● 在线");
  return el("span", { class: "badge gray" }, "离线");
}

function renderUsersTable() {
  const tbody = usersUI.tbody;
  if (!tbody) return;
  clear(tbody);
  const users = state.users;
  if (!users.length) {
    tbody.append(el("tr", null, el("td", { colspan: "8" }, emptyState("暂无用户，点击右上角「＋ 创建用户」", "👤"))));
    return;
  }
  for (const u of users) {
    const actionBtns = el("div", { class: "row-actions" },
      el("button", { class: "btn btn-xs", type: "button", onclick: () => openRenewModal(u) }, "续期"),
      el("button", { class: "btn btn-xs", type: "button", onclick: () => resetKeyFlow(u) }, "重置密钥"),
      el("button", { class: "btn btn-xs", type: "button", onclick: () => openPermissionsModal(u) }, "权限"),
      el("button", { class: "btn btn-xs", type: "button", onclick: () => { location.hash = `#/sessions?user_id=${enc(u.id)}`; } }, "会话"),
      el("button", { class: "btn btn-xs", type: "button", onclick: () => toggleUserDisabled(u) }, u.disabled ? "启用" : "禁用"),
      el("button", { class: "btn btn-xs btn-danger", type: "button", onclick: () => openDeleteUserModal(u) }, "删除"));

    tbody.append(el("tr", null,
      el("td", { class: "td-strong" }, u.name || "—"),
      el("td", null, u.display_name || "—"),
      el("td", null, userStatusBadge(u)),
      el("td", { class: "td-nowrap", title: fmtAbs(u.last_login_at) }, fmtRel(u.last_login_at)),
      el("td", { class: "td-nowrap", title: fmtAbs(u.last_seen_at) }, fmtRel(u.last_seen_at)),
      el("td", { class: "td-key" }, buildKeyCell(u)),
      el("td", { class: "td-nowrap" }, fmtTtl(u.token_ttl_days)),
      el("td", { class: "td-actions" }, actionBtns)));
  }
}

/* ---- 密钥单元格（前缀+****；眼睛查看全量 / 复制） ---- */

function buildKeyCell(u) {
  const prefix = u.key_prefix || "";
  const masked = prefix ? `${prefix}****` : "—";
  const textNode = el("span", {
    class: "key-cell-text mono",
    title: `key_version ${u.key_version || 1} · 点击「眼睛」查看完整密钥`,
  }, masked);
  let revealed = false;
  let fullKey = "";

  const eyeBtn = el("button", { class: "btn btn-xs btn-ghost icon-btn key-eye", type: "button", title: "查看完整密钥" }, "👁");
  const copyBtn = el("button", { class: "btn btn-xs btn-ghost icon-btn", type: "button", title: "复制密钥" }, "📋");
  const cell = el("div", { class: "key-cell" }, textNode,
    el("div", { class: "key-cell-actions" }, eyeBtn, copyBtn));

  async function fetchKey() {
    if (fullKey) return fullKey;
    const res = await api(`/users/${enc(u.id)}/key`);
    if (!res.available) {
      toast("该密钥创建于升级前，仅可查看前缀；如需完整密钥请先「重置密钥」", "warn", 6000);
      return "";
    }
    fullKey = res.key || "";
    return fullKey;
  }

  eyeBtn.addEventListener("click", async () => {
    if (revealed) {
      revealed = false;
      textNode.textContent = masked;
      textNode.classList.remove("revealed");
      cell.classList.remove("revealed");
      eyeBtn.classList.remove("on");
      eyeBtn.title = "查看完整密钥";
      return;
    }
    eyeBtn.disabled = true;
    try {
      const key = await fetchKey();
      if (!key) return;
      revealed = true;
      textNode.textContent = key;
      textNode.classList.add("revealed");
      cell.classList.add("revealed");
      eyeBtn.classList.add("on");
      eyeBtn.title = "隐藏密钥";
    } catch (e) {
      toast(e.message || "密钥获取失败", "error");
    } finally {
      eyeBtn.disabled = false;
    }
  });

  copyBtn.addEventListener("click", async () => {
    copyBtn.disabled = true;
    try {
      const key = await fetchKey();
      if (!key) return;
      const ok = await copyText(key);
      if (ok) {
        copyBtn.textContent = "✓";
        toast(`已复制 ${u.name || ""} 的密钥`, "success");
        setTimeout(() => { copyBtn.textContent = "📋"; }, 1600);
      } else {
        toast("复制失败，请点击「眼睛」后手动选中复制", "error");
      }
    } catch (e) {
      toast(e.message || "密钥获取失败", "error");
    } finally {
      copyBtn.disabled = false;
    }
  });

  return cell;
}

/* ---- 创建用户 ---- */

function openCreateUserModal() {
  const nameInput = el("input", { class: "input", placeholder: "例如 zhangsan（唯一标识，必填）", maxlength: "64" });
  const dispInput = el("input", { class: "input", placeholder: "例如 张三" });
  const noteInput = el("textarea", { class: "input", rows: "2", placeholder: "备注（可选）" });
  const ttl = buildTtlPicker({ value: 30 });
  const errLine = el("div", { class: "error-line hidden" });

  const body = el("div", null,
    el("div", { class: "form-row" }, el("label", null, "用户名", el("span", { class: "form-required" }, "*")), nameInput,
      el("div", { class: "form-hint" }, "用户登录时使用的唯一标识")),
    el("div", { class: "form-row" }, el("label", null, "显示名"), dispInput),
    el("div", { class: "form-row" }, el("label", null, "备注"), noteInput),
    el("div", { class: "form-row" }, el("label", null, "有效期"), ttl.node,
      el("div", { class: "form-hint" }, "token 有效期；到期后需重新登录。可稍后通过「续期」调整")),
    errLine);

  let closeFn = null;
  const submitBtn = el("button", { class: "btn btn-primary", type: "button" }, "创建");
  submitBtn.addEventListener("click", async () => {
    errLine.classList.add("hidden");
    const name = nameInput.value.trim();
    const ttlVal = ttl.getValue();
    if (!name) {
      errLine.textContent = "用户名不能为空";
      errLine.classList.remove("hidden");
      return;
    }
    if (typeof ttlVal === "number" && (!isFinite(ttlVal) || ttlVal <= 0)) {
      errLine.textContent = "请输入有效的有效期天数";
      errLine.classList.remove("hidden");
      return;
    }
    submitBtn.disabled = true;
    try {
      const res = await api("/users", {
        method: "POST",
        body: {
          name,
          display_name: dispInput.value.trim(),
          note: noteInput.value.trim(),
          token_ttl_days: ttlVal,
        },
      });
      if (closeFn) closeFn();
      toast(`已创建用户 ${(res.user && res.user.name) || name}`, "success");
      showKeyModal({ title: "用户创建成功", user: res.user, key: res.key, notice: res.notice });
      refreshUsers(false).catch(() => {});
    } catch (e) {
      errLine.textContent = e.message || "创建失败";
      errLine.classList.remove("hidden");
    } finally {
      submitBtn.disabled = false;
    }
  });

  const modal = openModal({
    title: "＋ 创建用户",
    body,
    footer: [
      el("button", { class: "btn", type: "button", onclick: () => closeFn && closeFn() }, "取消"),
      submitBtn,
    ],
  });
  closeFn = modal.close;
  setTimeout(() => { try { nameInput.focus(); } catch (e) {} }, 60);
}

/* ---- 一次性密钥展示（创建 / 重置密钥共用） ---- */

function showKeyModal({ title, user, key, notice }) {
  const copyBtn = el("button", { class: "btn btn-sm", type: "button" }, "📋 复制密钥");
  copyBtn.addEventListener("click", async () => {
    const ok = await copyText(key);
    if (ok) {
      copyBtn.textContent = "✓ 已复制";
      toast("密钥已复制到剪贴板", "success");
      setTimeout(() => { copyBtn.textContent = "📋 复制密钥"; }, 1600);
    } else {
      toast("复制失败，请手动选中密钥复制", "error");
    }
  });

  const userLine = user
    ? `用户：${user.name}${user.display_name ? `（${user.display_name}）` : ""} · 有效期：${fmtTtl(user.token_ttl_days)}`
    : "";

  const body = el("div", null,
    el("div", { class: "warn-box" }, "⚠️ 请立即复制密钥发给用户，关闭后无法再次查看"),
    userLine ? el("div", { class: "card-sub", style: "margin-bottom:10px;" }, userLine) : null,
    el("div", { class: "key-box" },
      el("div", { class: "key-label" }, "一次性明文密钥（仅此一次显示）"),
      el("div", { class: "key-text" }, key || "—"),
      el("div", { class: "key-actions" }, copyBtn)),
    notice ? el("div", { class: "hint-line" }, notice) : null);

  let closeFn = null;
  const okBtn = el("button", { class: "btn btn-primary", type: "button", onclick: () => closeFn && closeFn() }, "我已复制，关闭");
  const modal = openModal({ title, body, footer: [okBtn], closeOnMask: false });
  closeFn = modal.close;
}

/* ---- 续期 ---- */

function openRenewModal(u) {
  const ttl = buildTtlPicker({ value: u.token_ttl_days === null || u.token_ttl_days === undefined ? 30 : u.token_ttl_days });
  const errLine = el("div", { class: "error-line hidden" });
  const body = el("div", null,
    el("div", { class: "form-row" },
      el("label", null, `用户「${u.name}」的新有效期`),
      ttl.node,
      el("div", { class: "form-hint" }, "将延长该用户全部未撤销 token 的有效期；永久 = 不设置过期时间")),
    errLine);

  let closeFn = null;
  const okBtn = el("button", { class: "btn btn-primary", type: "button" }, "确认续期");
  okBtn.addEventListener("click", async () => {
    errLine.classList.add("hidden");
    const v = ttl.getValue();
    if (typeof v === "number" && (!isFinite(v) || v <= 0)) {
      errLine.textContent = "请输入有效的天数";
      errLine.classList.remove("hidden");
      return;
    }
    const ttlDays = (v === null) ? "forever" : v;
    okBtn.disabled = true;
    try {
      const res = await api(`/users/${enc(u.id)}/renew`, { method: "POST", body: { ttl_days: ttlDays } });
      if (closeFn) closeFn();
      toast(`已续期，更新 ${res.tokens_updated || 0} 个 token`, "success");
      refreshUsers(false).catch(() => {});
    } catch (e) {
      errLine.textContent = e.message || "续期失败";
      errLine.classList.remove("hidden");
    } finally {
      okBtn.disabled = false;
    }
  });

  const modal = openModal({
    title: `续期 — ${u.name}`,
    body,
    footer: [
      el("button", { class: "btn", type: "button", onclick: () => closeFn && closeFn() }, "取消"),
      okBtn,
    ],
  });
  closeFn = modal.close;
}

/* ---- 重置密钥 ---- */

async function resetKeyFlow(u) {
  const ok = await confirmDialog("重置密钥",
    `确定要重置用户「${u.name}」的密钥吗？\n\n旧密钥与全部旧凭据将立即失效，用户需使用新密钥重新登录。`,
    { danger: true, okText: "重置密钥" });
  if (!ok) return;
  try {
    const res = await api(`/users/${enc(u.id)}/reset_key`, { method: "POST" });
    toast("密钥已重置", "success");
    showKeyModal({ title: "新密钥（重置成功）", user: res.user || u, key: res.key, notice: res.notice });
    refreshUsers(false).catch(() => {});
  } catch (e) {
    toast(e.message || "重置失败", "error");
  }
}

/* ---- 权限快照 ---- */

function openPermissionsModal(u) {
  const body = el("div", null, el("div", { class: "empty-state" }, el("div", { class: "icon" }, "⏳"), el("div", null, "加载中…")));
  let closeFn = null;
  const okBtn = el("button", { class: "btn", type: "button", onclick: () => closeFn && closeFn() }, "关闭");
  const modal = openModal({ title: `权限快照 — ${u.name}`, body, width: "680px", footer: [okBtn] });
  closeFn = modal.close;

  api(`/users/${enc(u.id)}/permissions`).then(res => {
    clear(body);
    appendChildren(body, buildPermissionsBody(res));
  }).catch(e => {
    clear(body);
    body.append(el("div", { class: "error-line" }, e.message || "权限快照加载失败"));
  });
}

function buildPermissionsBody(res) {
  const out = [];
  const perms = res.permissions || {};
  const running = res.running_sessions || [];
  const hasPerms = perms && typeof perms === "object" && Object.keys(perms).length > 0;

  if (!hasPerms) {
    out.push(emptyState("该用户暂无上报的权限快照", "🗂"));
  } else {
    const info = el("div", { class: "kv-grid" });
    const add = (label, value) => info.append(el("div", { class: "kv-item" },
      el("div", { class: "kv-label" }, label), el("div", { class: "kv-value" }, value)));
    add("权限模式", perms.permission_mode || "—");
    add("YOLO 保留拒绝", perms.yolo_keep_deny ? "是" : "否");
    add("当前 Agent", perms.active_agent || "—");
    add("在线状态", res.online ? "在线" : "离线");
    if (Array.isArray(perms.surfaces) && perms.surfaces.length) add("surfaces", perms.surfaces.join(", "));
    out.push(el("div", { class: "section-title" }, "基础信息"), info);

    const rules = (perms.rules && typeof perms.rules === "object") ? perms.rules : {};
    for (const key of ["allow", "ask", "deny"]) {
      const arr = Array.isArray(rules[key]) ? rules[key] : [];
      const listNode = el("div", { class: "list" });
      if (!arr.length) {
        listNode.append(el("div", { class: "hint-line" }, "（空）"));
      } else {
        for (const r of arr) {
          const text = (typeof r === "string") ? r : (() => { try { return JSON.stringify(r); } catch (e) { return String(r); } })();
          listNode.append(el("div", { class: "list-item" },
            badge(key, key === "deny" ? "red" : key === "ask" ? "amber" : "green"),
            el("span", { class: "li-summary mono", style: "white-space:pre-wrap;word-break:break-all;" }, text)));
        }
      }
      out.push(el("div", { class: "section-title" }, `规则 · ${key}（${arr.length}）`), listNode);
    }

    const agents = Array.isArray(perms.agents) ? perms.agents : [];
    if (agents.length) {
      // v5.4.0+：整个 Agents 列表共享【一个】横向滚动条（此前每行一个，
      // 视觉杂乱）——行内内容不换行、宽度随内容撑开，容器统一滚动全量查看
      const list = el("div", { class: "list li-single-scroll" });
      for (const ag of agents) {
        const disabled = Array.isArray(ag.disabled_tools) ? ag.disabled_tools : [];
        const detail = (ag.active_chain ? `链条：${ag.active_chain} · ` : "") +
          (disabled.length ? `禁用工具：${disabled.join(", ")}` : "无禁用工具");
        list.append(el("div", { class: "list-item li-wide" },
          el("span", { class: "li-user mono" }, ag.name || "—"),
          el("span", { class: "li-detail" }, detail)));
      }
      out.push(el("div", { class: "section-title" }, `Agents（${agents.length}）`), list);
    }
  }

  if (running.length) {
    const list = el("div", { class: "list" });
    for (const s of running) {
      list.append(el("div", { class: "list-item" },
        badge("运行中", "green", "badge-running"),
        el("span", { class: "li-user" }, s.agent || "—"),
        el("span", { class: "li-summary mono", title: s.session_id || "" }, truncate(s.session_id || "—", 26)),
        el("span", { class: "li-time" }, s.title || "")));
    }
    out.push(el("div", { class: "section-title" }, `运行中会话（${running.length}）`), list);
  }
  return out;
}

/* ---- 禁用 / 启用 / 删除 ---- */

async function toggleUserDisabled(u) {
  const target = !u.disabled;
  if (target) {
    const ok = await confirmDialog("禁用用户",
      `确定要禁用用户「${u.name}」吗？\n\n禁用后其在线状态将被清除，且无法再登录或上报数据。`,
      { danger: true, okText: "禁用" });
    if (!ok) return;
  }
  try {
    await api(`/users/${enc(u.id)}`, { method: "PUT", body: { disabled: target } });
    toast(target ? `已禁用用户 ${u.name}` : `已启用用户 ${u.name}`, "success");
    refreshUsers(false).catch(() => {});
  } catch (e) {
    toast(e.message || "操作失败", "error");
  }
}

function openDeleteUserModal(u) {
  const purgeCb = el("input", { type: "checkbox" });
  const body = el("div", null,
    el("p", { style: "color:var(--text-1);font-size:13.5px;margin-bottom:12px;" },
      `确定要删除用户「${u.name}」吗？此操作不可恢复。`),
    el("label", { class: "checkbox-row" }, purgeCb, "同时删除其会话数据（purge）"));

  let closeFn = null;
  const delBtn = el("button", { class: "btn btn-danger", type: "button" }, "删除");
  delBtn.addEventListener("click", async () => {
    const purge = purgeCb.checked ? 1 : 0;
    delBtn.disabled = true;
    try {
      await api(`/users/${enc(u.id)}?purge=${purge}`, { method: "DELETE" });
      if (closeFn) closeFn();
      toast(`已删除用户 ${u.name}`, "success");
      refreshUsers(false).catch(() => {});
    } catch (e) {
      toast(e.message || "删除失败", "error");
      delBtn.disabled = false;
    }
  });

  const modal = openModal({
    title: "删除用户",
    body,
    footer: [
      el("button", { class: "btn", type: "button", onclick: () => closeFn && closeFn() }, "取消"),
      delBtn,
    ],
  });
  closeFn = modal.close;
}

/* ===================================================================
   10. 会话管理（#/sessions）
   =================================================================== */

const sessUI = { built: false, tbody: null, userSel: null, statusSel: null, qInput: null, pager: null, wsTimer: null };

function buildSessionsView() {
  if (sessUI.built) return;
  sessUI.built = true;
  const root = $("#view-sessions");
  clear(root);

  sessUI.userSel = el("select", { class: "select", id: "sess-user" }, el("option", { value: "" }, "全部用户"));
  sessUI.statusSel = el("select", { class: "select", id: "sess-status" },
    el("option", { value: "" }, "全部状态"),
    el("option", { value: "running" }, "running"),
    el("option", { value: "idle" }, "idle"));
  sessUI.qInput = el("input", { class: "input", id: "sess-q", placeholder: "标题 / 工作空间 / 会话ID…", style: "width:220px;" });
  sessUI.tbody = el("tbody", { id: "sessions-tbody" });
  sessUI.pager = el("div", { class: "pager", id: "sessions-pager" });

  sessUI.userSel.addEventListener("change", () => { state.sess.userId = sessUI.userSel.value; state.sess.page = 0; refreshSessions().catch(e => toast(e.message, "error")); });
  sessUI.statusSel.addEventListener("change", () => { state.sess.status = sessUI.statusSel.value; state.sess.page = 0; refreshSessions().catch(e => toast(e.message, "error")); });
  sessUI.qInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { state.sess.q = sessUI.qInput.value.trim(); state.sess.page = 0; refreshSessions().catch(err => toast(err.message, "error")); } });

  const headers = ["用户", "Agent", "标题", "模型", "工作空间", "消息数", "状态", "创建时间", "最后活跃"];
  root.append(
    el("div", { class: "page-header" },
      el("div", null,
        el("div", { class: "page-title" }, "会话管理"),
        el("div", { class: "page-desc" }, "点击任意行查看会话消息详情")),
      el("div", { class: "page-actions" },
        el("button", { class: "btn btn-sm", type: "button", onclick: () => exportSessionsCsv() }, "⬇ 导出 CSV"),
        el("button", { class: "btn btn-sm", type: "button", onclick: () => refreshSessions(true) }, "🔄 刷新"))),

    el("div", { class: "filter-bar" },
      el("span", { class: "filter-label" }, "用户"), sessUI.userSel,
      el("span", { class: "filter-label" }, "状态"), sessUI.statusSel,
      sessUI.qInput,
      el("button", { class: "btn btn-sm btn-primary", type: "button", onclick: () => { state.sess.q = sessUI.qInput.value.trim(); state.sess.page = 0; refreshSessions().catch(e => toast(e.message, "error")); } }, "🔍 搜索"),
      el("button", { class: "btn btn-sm", type: "button", onclick: () => { sessUI.qInput.value = ""; state.sess.q = ""; state.sess.page = 0; refreshSessions().catch(e => toast(e.message, "error")); } }, "清空")),

    el("div", { class: "table-wrap" },
      el("table", { class: "table" },
        el("thead", null, el("tr", null, ...headers.map(h => el("th", null, h)))),
        sessUI.tbody)),
    sessUI.pager);
}

async function loadSessionsView(params, seq) {
  buildSessionsView();
  try { await ensureUsers(); } catch (e) { /* 下拉留空 */ }
  if (params && params.user_id !== undefined) state.sess.userId = params.user_id || "";
  fillUserSelect(sessUI.userSel, { value: state.sess.userId });
  sessUI.statusSel.value = state.sess.status;
  sessUI.qInput.value = state.sess.q;
  try {
    await refreshSessions(false);
  } catch (e) {
    if (e.status !== 401) toast(e.message || "会话列表加载失败", "error");
  }
  // v5.4.0 迭代：实时更新——WS 事件即时刷新（boot 全局监听）+ 5s 轮询兜底
  Poll.add(async () => { await autoRefreshSessions(); }, 5000, false);
}

function sessionsQuery() {
  const s = state.sess;
  const qs = new URLSearchParams();
  if (s.userId) qs.set("user_id", s.userId);
  if (s.status) qs.set("status", s.status);
  if (s.q) qs.set("q", s.q);
  return qs;
}

/** 列表数据签名（自动刷新对比用：无变化则跳过重绘，避免闪烁） */
function sessionsSig(rows, total) {
  try {
    return `${total}|` + rows.map(r =>
      `${r.session_id}:${r.status}:${r.message_count}:${r.last_heartbeat}`).join(",");
  } catch (e) {
    return String(Math.random());
  }
}

async function refreshSessions(showToast) {
  const s = state.sess;
  const qs = sessionsQuery();
  qs.set("limit", String(PAGE_LIMIT));
  qs.set("offset", String(s.page * PAGE_LIMIT));
  const res = await api(`/sessions?${qs.toString()}`);
  const rows = res.sessions || [];
  s.total = res.total || 0;
  s.lastSig = sessionsSig(rows, s.total);
  renderSessionsTable(rows);
  renderSessionsPager();
  if (showToast) toast("已刷新会话列表", "success");
}

/** 自动刷新（WS 事件 / 轮询兜底触发）：新会话与"运行中"状态实时上屏 */
async function autoRefreshSessions() {
  if (state.route !== "sessions") return;
  const s = state.sess;
  const qs = sessionsQuery();
  qs.set("limit", String(PAGE_LIMIT));
  qs.set("offset", String(s.page * PAGE_LIMIT));
  const res = await api(`/sessions?${qs.toString()}`);
  const rows = res.sessions || [];
  const sig = sessionsSig(rows, res.total || 0);
  if (sig === s.lastSig) return;   // 无变化：不重绘
  s.lastSig = sig;
  s.total = res.total || 0;
  renderSessionsTable(rows);
  renderSessionsPager();
}

function renderSessionsTable(rows) {
  const tbody = sessUI.tbody;
  clear(tbody);
  if (!rows.length) {
    tbody.append(el("tr", null, el("td", { colspan: "9" }, emptyState("暂无会话数据", "💬"))));
    return;
  }
  for (const r of rows) {
    const running = r.status === "running";
    const statusBadge = running
      ? el("span", { class: "badge badge-running" }, "● 运行中")
      : el("span", { class: "badge badge-idle" }, "空闲");
    const tr = el("tr", { class: "clickable", title: "点击查看会话详情" },
      el("td", { class: "td-strong td-nowrap" }, r.user_name || r.user_id || "—"),
      el("td", null, r.agent || "—"),
      el("td", { class: "ellipsis", style: "max-width:220px;", title: r.title || "" }, r.title || "—"),
      el("td", { class: "td-mono" }, r.model || "—"),
      el("td", { class: "td-mono ellipsis", style: "max-width:200px;", title: r.workspace || "" }, r.workspace || "—"),
      el("td", { class: "td-nowrap" }, fmtInt(r.message_count)),
      el("td", null, statusBadge),
      el("td", { class: "td-nowrap", title: fmtAbs(r.created_at) }, fmtAbs(r.created_at)),
      el("td", { class: "td-nowrap", title: fmtAbs(r.last_heartbeat) }, fmtRel(r.last_heartbeat)));
    tr.addEventListener("click", () => openSessionDrawer(r.user_id, r.session_id, r));
    tbody.append(tr);
  }
}

function renderSessionsPager() {
  const s = state.sess;
  const pages = Math.max(1, Math.ceil(s.total / PAGE_LIMIT));
  const page = Math.min(s.page + 1, pages);
  clear(sessUI.pager);
  sessUI.pager.append(
    el("button", {
      class: "btn btn-sm", type: "button", disabled: s.page <= 0 ? "disabled" : null,
      onclick: () => { if (state.sess.page > 0) { state.sess.page -= 1; refreshSessions().catch(e => toast(e.message, "error")); } },
    }, "← 上一页"),
    el("span", { class: "pager-info" }, `第 ${page} / ${pages} 页 · 共 ${fmtInt(s.total)} 条`),
    el("button", {
      class: "btn btn-sm", type: "button", disabled: (s.page + 1 >= pages) ? "disabled" : null,
      onclick: () => { if (state.sess.page + 1 < pages) { state.sess.page += 1; refreshSessions().catch(e => toast(e.message, "error")); } },
    }, "下一页 →"));
}

async function exportSessionsCsv() {
  try {
    const qs = sessionsQuery();
    await downloadCsv(`/export/sessions.csv?${qs.toString()}`, "cbhcli_sessions.csv");
    toast("CSV 已开始下载", "success");
  } catch (e) {
    toast(e.message || "导出失败", "error");
  }
}

/* ---- 会话详情抽屉 ---- */

function openSessionDrawer(uid, sid, row) {
  closeDrawer();
  if (!uid || !sid) { toast("会话标识缺失", "error"); return; }

  const titleNode = el("div", { class: "drawer-title" }, (row && row.title) || "会话详情");
  const userNode = el("span", null, (row && (row.user_name || row.user_id)) || uid);
  const statusNode = el("span", null, "");
  const bodyNode = el("div", { class: "drawer-body" }, el("div", { class: "empty-state" }, el("div", { class: "icon" }, "⏳"), el("div", null, "加载中…")));
  const footLeft = el("span", null, "");
  const footRight = el("span", null, "");

  const drawer = el("div", { class: "drawer" },
    el("div", { class: "drawer-head" },
      el("div", { class: "drawer-head-main" },
        titleNode,
        el("div", { class: "drawer-meta" },
          el("span", { class: "badge blue" }, "👤"), userNode, statusNode,
          el("span", { class: "mono text-3", title: sid }, `ID：${truncate(sid, 22)}`))),
      el("div", { class: "drawer-head-actions" },
        el("button", { class: "btn btn-sm btn-danger", type: "button", onclick: () => deleteSessionFlow(uid, sid, row) }, "删除会话"),
        el("button", { class: "btn btn-sm btn-ghost", type: "button", title: "关闭（Esc）", onclick: closeDrawer }, "✕"))),
    bodyNode,
    el("div", { class: "drawer-foot" }, footLeft, footRight));

  const mask = el("div", { class: "drawer-mask", onclick: closeDrawer });
  $("#drawer-root").append(mask, drawer);

  state.drawer = {
    uid, sid, row,
    drawer, mask, bodyNode, titleNode, userNode, statusNode, footLeft, footRight,
    poll: null, sig: "",
    live: { rounds: [] },       // WS 实时轮（实时交互画面数据）
    liveRoot: null,             // 实时区 DOM 根
    refreshTimer: null,         // 轮结束后的快照补拉定时器
    wsListener: null,
  };
  // 订阅实时事件流（只渲染本会话；思考/正文/工具实时到达，不等轮结束）
  state.drawer.wsListener = LiveWS.addListener((msg) => {
    try { handleDrawerLiveEvent(msg); } catch (e) { /* 忽略 */ }
  });
  document.addEventListener("keydown", onDrawerKey);
  refreshSessionDetail().catch(() => {});
}

function setDrawerStatus(isRunning) {
  const d = state.drawer;
  if (!d) return;
  clear(d.statusNode);
  if (isRunning) {
    d.statusNode.append(el("span", { class: "badge badge-running" }, "● 运行中"));
    if (d.footRight) d.footRight.textContent = "自动刷新中（每 2 秒）";
  } else {
    d.statusNode.append(el("span", { class: "badge badge-idle" }, "空闲"));
    if (d.footRight) d.footRight.textContent = "会话未运行 · 已停止自动刷新";
  }
}

async function refreshSessionDetail() {
  const d = state.drawer;
  if (!d) return;
  let res;
  try {
    res = await api(`/sessions/${enc(d.uid)}/${enc(d.sid)}`);
  } catch (e) {
    if (state.drawer === d && !d.sig) {
      clear(d.bodyNode);
      d.bodyNode.append(emptyState(e.message || "会话详情加载失败", "⚠️"));
      if (d.footRight) d.footRight.textContent = "加载失败";
      d.sig = "error";
    }
    throw e;
  }
  if (state.drawer !== d) return;
  renderSessionDetail(d, res);

  const running = !!res.running;
  setDrawerStatus(running);
  if (running && !d.poll) {
    d.poll = Poll.add(() => refreshSessionDetail().catch(() => {}), POLL_DETAIL, false);
  }
  if (!running && d.poll) {
    Poll.remove(d.poll);
    d.poll = null;
  }
}

function renderSessionDetail(d, res) {
  const meta = res.meta || {};
  const data = res.data || null;

  const title = (data && data.title) || meta.title || d.row && d.row.title || "会话详情";
  d.titleNode.textContent = title || "会话详情";

  const userName = meta.user_name || d.row && d.row.user_name || d.uid;
  const userDisplay = meta.user_display || d.row && d.row.user_display || "";
  clear(d.userNode);
  d.userNode.append(document.createTextNode(userDisplay ? `${userName}（${userDisplay}）` : String(userName)));

  // 刷新内容（消息未变化时跳过重绘，避免滚动跳动）
  const msgs = (data && Array.isArray(data.messages)) ? data.messages : [];
  const last = msgs.length ? msgs[msgs.length - 1] : null;
  let lastSig = "";
  try { lastSig = last ? JSON.stringify(last).length : 0; } catch (e) { lastSig = Math.random(); }
  const sig = `${msgs.length}|${lastSig}|${data ? (data.updated_at || "") : ""}`;

  if (sig !== d.sig) {
    const bodyNode = d.bodyNode;
    const prevTop = bodyNode.scrollTop;
    const wasBottom = bodyNode.scrollTop + bodyNode.clientHeight >= bodyNode.scrollHeight - 60;
    d.sig = sig;

    // v5.4.0：快照已更新 -> 已"固化"进快照的实时轮移除（避免与历史重复）
    if (d.live && d.live.rounds.length) {
      d.live.rounds = d.live.rounds.filter(r => !r.done);
    }
    d.liveRoot = null;      // 原节点随 clear(bodyNode) 一并移除

    clear(bodyNode);
    if (!data) {
      bodyNode.append(emptyState("该会话暂无消息快照（客户端尚未上报）", "🗂"));
    } else {
      // 元信息行
      const metaLine = el("div", { class: "hint-line", style: "margin-bottom:12px;" },
        `Agent：${data.agent || meta.agent || "—"} · 模型：${data.model || meta.model || "—"} · `
        + `消息数：${fmtInt(data.message_count || msgs.length)} · 更新：${fmtAbs(data.updated_at || meta.updated_at)}`
        + (data.workspace ? ` · 工作空间：${data.workspace}` : ""));
      bodyNode.append(metaLine);
      bodyNode.append(msgs.length ? renderMessages(msgs) : emptyState("该会话暂无消息", "💬"));
    }
    // 重建实时区（未固化轮 / 快照尚未包含的轮，实时画面续接）
    renderLiveArea(d);
    // 保持滚动位置：原本贴底则继续贴底，否则维持原位置
    if (wasBottom) bodyNode.scrollTop = bodyNode.scrollHeight;
    else bodyNode.scrollTop = prevTop;
  }

  if (d.footLeft) {
    d.footLeft.textContent = `消息数 ${fmtInt((data && data.message_count) || msgs.length)} · 更新于 ${fmtAbs((data && data.updated_at) || meta.updated_at)}`;
  }
  if (d.footRight && res.running) d.footRight.textContent = `自动刷新中 · ${fmtClock(nowSec())}`;
}

/* ---- 实时交互画面（WS 事件流：思考 → 正文 → 工具，与 Web 端顺序一致） ---- */

/** 单条 WS 事件 -> 当前抽屉（按轮聚合；非本会话忽略）
 *  v5.4.0 迭代：增量渲染——每个 chunk 只向对应块追加文本节点，
 *  不重建 DOM（此前每 0.8s 全量重建导致"一顿一顿"） */
function handleDrawerLiveEvent(msg) {
  const d = state.drawer;
  if (!d || !d.live) return;
  if (msg.user_id !== d.uid || msg.session_id !== d.sid) return;
  const type = String(msg.event_type || "");
  const p = msg.payload || {};

  if (type === "session_round_start") {
    livePushRound(d, p.prompt || "");
  } else if (type === "stream_chunk") {
    const round = liveCurrentRound(d);
    const kind = (p.kind === "reasoning") ? "reasoning" : "content";
    const text = String(p.text || "");
    round[kind] = (round[kind] || "") + text;
    liveAppendText(d, round, kind, text);
  } else if (type === "tracer.tool_call") {
    const round = liveCurrentRound(d);
    const tool = {
      name: p.tool || "tool",
      arguments: p.arguments || "",
      output: p.output || "",
      error: p.error || "",
      success: p.success !== false,
      duration_ms: p.duration_ms || 0,
    };
    round.tools.push(tool);
    liveAppendTool(d, round, tool);
  } else if (type === "session_round_complete") {
    const round = d.live.rounds[d.live.rounds.length - 1];
    if (round) { round.done = true; liveMarkDone(d, round); }
    scheduleDetailRefresh(d, 1600);   // 等快照落盘后刷新并入历史
  } else if (type === "session_status") {
    setDrawerStatus(String(p.status || "") === "running");
  }
}

function livePushRound(d, userText) {
  const round = {
    userText: userText || "", reasoning: "", content: "",
    tools: [], done: false,
    _root: null, _reasonBody: null, _contentNode: null,
    _toolsWrap: null, _doneNode: null,
  };
  d.live.rounds.push(round);
  liveEnsureRoundRoot(d, round);
  liveScrollFollow(d);
  return round;
}

function liveCurrentRound(d) {
  if (!d.live.rounds.length) return livePushRound(d, "");
  return d.live.rounds[d.live.rounds.length - 1];
}

function scheduleDetailRefresh(d, delay) {
  if (d.refreshTimer) clearTimeout(d.refreshTimer);
  d.refreshTimer = setTimeout(() => {
    d.refreshTimer = null;
    if (state.drawer === d) refreshSessionDetail().catch(() => {});
  }, delay || 1500);
}

/** 实时区容器（懒创建；已存在且挂载则复用） */
function liveEnsureRoot(d) {
  if (d.liveRoot && d.liveRoot.isConnected) return d.liveRoot;
  d.liveRoot = el("div", { class: "live-area" },
    el("div", { class: "live-tag" }, "● 实时（WebSocket）"));
  d.bodyNode.append(d.liveRoot);
  return d.liveRoot;
}

/** 轮容器（懒创建；失效时重建并重置子块引用——用于快照重建后重挂） */
function liveEnsureRoundRoot(d, round) {
  if (round._root && round._root.isConnected) return round._root;
  round._root = el("div", { class: "live-round" });
  if (round.userText) {
    round._root.append(el("div", { class: "msg" },
      el("div", { class: "msg-role" }, "👤 用户（实时）"),
      el("div", { class: "msg-user" }, round.userText)));
  }
  round._reasonBody = null;
  round._contentNode = null;
  round._toolsWrap = null;
  round._doneNode = null;
  liveEnsureRoot(d).append(round._root);
  return round._root;
}

function liveScrollFollow(d) {
  const bn = d.bodyNode;
  if (bn.scrollTop + bn.clientHeight >= bn.scrollHeight - 120) {
    bn.scrollTop = bn.scrollHeight;
  }
}

/** 追加流式文本（思考/正文块按需创建；顺序 = 首次到达顺序：思考 → 正文） */
function liveAppendText(d, round, kind, text) {
  if (!text) return;
  liveEnsureRoundRoot(d, round);
  if (kind === "reasoning") {
    if (!round._reasonBody) {
      const card = el("div", { class: "collapse thinking open" });
      const head = el("div", { class: "collapse-head" },
        el("span", { class: "collapse-arrow" }, "▶"),
        el("span", null, "💭 思考过程"));
      head.addEventListener("click", () => card.classList.toggle("open"));
      round._reasonBody = el("div", { class: "collapse-body" });
      card.append(head, round._reasonBody);
      round._root.append(card);
    }
    round._reasonBody.append(text);
  } else {
    if (!round._contentNode) {
      round._contentNode = el("div", { class: "live-content" });
      round._root.append(el("div", { class: "msg" },
        el("div", { class: "msg-role" }, "🤖 AI 助手（实时）"),
        round._contentNode));
    }
    round._contentNode.append(text);
  }
  liveScrollFollow(d);
}

function liveAppendTool(d, round, t) {
  liveEnsureRoundRoot(d, round);
  if (!round._toolsWrap) {
    round._toolsWrap = el("div", { class: "live-tools" });
    round._root.append(round._toolsWrap);
  }
  const label = `🔧 ${t.name}${t.success ? "" : "（失败）"}` +
    (t.duration_ms ? ` · ${t.duration_ms}ms` : "");
  const card = el("div", { class: "collapse open live-tool" });
  const head = el("div", { class: "collapse-head" },
    el("span", { class: "collapse-arrow" }, "▶"),
    el("span", null, label));
  head.addEventListener("click", () => card.classList.toggle("open"));
  const body = el("div", { class: "collapse-body" },
    el("pre", { class: "tc-args" }, String(t.arguments || "{}")));
  const out = String(t.output || t.error || "");
  if (out) body.append(el("div", { class: "tool-result" }, out));
  card.append(head, body);
  round._toolsWrap.append(card);
  liveScrollFollow(d);
}

function liveMarkDone(d, round) {
  liveEnsureRoundRoot(d, round);
  if (!round._doneNode) {
    round._doneNode = el("div", { class: "live-done" }, "✓ 本轮结束（等待写入快照）");
    round._root.append(round._doneNode);
  }
  liveScrollFollow(d);
}

/** 快照刷新导致 bodyNode 被清空后：从数据全量重建实时区（DOM 引用已失效） */
function renderLiveArea(d) {
  if (!d || !d.live || !d.bodyNode) return;
  if (d.liveRoot) { d.liveRoot.remove(); d.liveRoot = null; }
  const rounds = d.live.rounds;
  if (!rounds.length) return;
  const bn = d.bodyNode;
  const follow = bn.scrollTop + bn.clientHeight >= bn.scrollHeight - 120;
  for (const r of rounds) r._root = null;   // 强制重建
  for (const r of rounds) {
    liveEnsureRoundRoot(d, r);
    if (r.reasoning) liveAppendText(d, r, "reasoning", r.reasoning);
    if (r.content) liveAppendText(d, r, "content", r.content);
    for (const t of r.tools) liveAppendTool(d, r, t);
    if (r.done) liveMarkDone(d, r);
  }
  if (follow) bn.scrollTop = bn.scrollHeight;
}

function renderMessages(msgs) {
  const wrap = el("div", { class: "msg-list" });
  for (const m of msgs) {
    const role = m && m.role ? m.role : "unknown";
    if (role === "user") wrap.append(renderUserMessage(m));
    else if (role === "assistant") wrap.append(renderAssistantMessage(m));
    else if (role === "tool") wrap.append(renderToolMessage(m));
    else wrap.append(renderSystemMessage(m, role));
  }
  return wrap;
}

function renderUserMessage(m) {
  const text = extractText(m.content) || "（空消息）";
  return el("div", { class: "msg" },
    el("div", { class: "msg-role" }, "👤 用户"),
    el("div", { class: "msg-user" }, text));
}

function renderAssistantMessage(m) {
  const box = el("div", { class: "msg msg-assistant" },
    el("div", { class: "msg-role" }, "🤖 AI 助手"));

  const content = extractText(m.content);
  const reasoning = (typeof m.reasoning_content === "string") ? m.reasoning_content : "";
  const calls = Array.isArray(m.tool_calls) ? m.tool_calls : null;

  // v5.4.0 顺序修复：思考 → 文字输出 → 工具调用
  // （此前思考被渲染在工具调用/文字之后，与实际生成顺序不符）
  if (reasoning) box.append(renderThinking(reasoning, !content));
  if (content) box.append(mdBlock(content));
  if (calls && calls.length) box.append(renderToolCalls(calls));
  if (!content && !reasoning && !(calls && calls.length)) {
    box.append(el("div", { class: "msg-empty" }, "（空回复）"));
  }
  return box;
}

function renderToolCalls(calls) {
  const card = el("div", { class: "collapse" });
  const head = el("div", { class: "collapse-head" },
    el("span", { class: "collapse-arrow" }, "▶"),
    el("span", null, `🔧 工具调用（${calls.length}）`));
  const body = el("div", { class: "collapse-body" });
  head.addEventListener("click", () => card.classList.toggle("open"));

  for (const c of calls) {
    const fn = (c && (c.function || c.fn)) || {};
    const name = fn.name || (c && c.name) || "unknown";
    const args = (fn.arguments !== undefined) ? fn.arguments : (c ? c.arguments : "");
    let argsText = "";
    if (typeof args === "string") {
      try { argsText = JSON.stringify(JSON.parse(args), null, 2); } catch (e) { argsText = args; }
    } else {
      try { argsText = JSON.stringify(args === undefined ? {} : args, null, 2); } catch (e) { argsText = String(args); }
    }
    body.append(el("div", { class: "tc-item" },
      el("div", { class: "tc-name" }, name),
      el("pre", { class: "tc-args" }, argsText || "{}")));
  }
  card.append(head, body);
  return card;
}

function renderThinking(text, openByDefault) {
  const card = el("div", { class: "collapse thinking" + (openByDefault ? " open" : "") });
  const head = el("div", { class: "collapse-head" },
    el("span", { class: "collapse-arrow" }, "▶"),
    el("span", null, "💭 思考过程"));
  const body = el("div", { class: "collapse-body" }, text);
  head.addEventListener("click", () => card.classList.toggle("open"));
  card.append(head, body);
  return card;
}

function renderToolMessage(m) {
  const text = extractText(m.content);
  return el("div", { class: "msg msg-tool" },
    el("div", null, `🔩 工具结果${m.tool_call_id ? ` · ${truncate(m.tool_call_id, 24)}` : ""}`),
    el("div", { class: "tool-result" }, truncate(text, 500) || "（空）"));
}

function renderSystemMessage(m, role) {
  const text = extractText(m.content);
  return el("div", { class: "msg" },
    el("div", { class: "msg-role" }, `⚙️ ${role === "system" ? "系统" : role}`),
    el("div", { class: "msg-system" }, truncate(text, 2000) || "（空）"));
}

async function deleteSessionFlow(uid, sid, row) {
  const title = (row && row.title) || sid;
  const ok = await confirmDialog("删除会话",
    `确定要删除会话「${truncate(title, 60)}」吗？\n\n会话记录与消息快照将一并删除，此操作不可恢复。`,
    { danger: true, okText: "删除会话" });
  if (!ok) return;
  try {
    await api(`/sessions/${enc(uid)}/${enc(sid)}`, { method: "DELETE" });
    toast("会话已删除", "success");
    closeDrawer();
    refreshSessions(false).catch(() => {});
  } catch (e) {
    toast(e.message || "删除失败", "error");
  }
}

/* ===================================================================
   11. 实时监控（#/monitor）
   =================================================================== */

const monUI = { built: false, grid: null, count: null, wsTimer: null };

function buildMonitorView() {
  if (monUI.built) return;
  monUI.built = true;
  const root = $("#view-monitor");
  clear(root);
  monUI.grid = el("div", { class: "mon-grid", id: "mon-grid" });
  monUI.count = el("div", { class: "card-sub", id: "mon-count" }, "");
  root.append(
      el("div", { class: "page-header" },
      el("div", null,
        el("div", { class: "page-title" }, "实时监控"),
        el("div", { class: "page-desc" }, "运行中会话 · 全程可见 · WebSocket 实时刷新（2s 轮询兜底）")),
      el("div", { class: "page-actions" }, monUI.count,
        el("button", { class: "btn btn-sm", type: "button", onclick: () => refreshMonitor(true) }, "🔄 刷新"))),    monUI.grid);
}

function loadMonitorView(seq) {
  buildMonitorView();
  refreshMonitor(false).catch(e => { if (e.status !== 401) toast(e.message || "实时数据加载失败", "error"); });
  // 实时监控 2s 轮询（隐藏暂停 / 切页清除）
  Poll.add(async () => {
    try { await refreshMonitor(false); }
    catch (e) { if (e.status !== 401) markService(false); }
  }, POLL_MONITOR, false);
}

async function refreshMonitor(showToast) {
  const res = await api("/live");
  if (state.route !== "monitor") return;
  renderMonitor(res);
  markService(true);
  if (showToast) toast("已刷新", "success");
}

function renderMonitor(res) {
  const grid = monUI.grid;
  if (!grid) return;
  const sessions = res.running_sessions || [];
  if (monUI.count) {
    monUI.count.textContent = `运行中 ${res.running_count || 0} · 在线 ${res.online_count || 0}`;
  }
  clear(grid);
  if (!sessions.length) {
    grid.append(el("div", { style: "grid-column:1 / -1;" },
      emptyState("当前没有运行中的会话", "📡")));
    return;
  }
  const now = nowSec();
  for (const s of sessions) {
    const started = Number(s.started_at) > 0 ? Number(s.started_at) : 0;
    const beat = Number(s.last_beat) > 0 ? Number(s.last_beat) : 0;
    const base = started || beat;
    const duration = base ? now - base : null;
    const ageNum = Number(s.age);
    const age = isFinite(ageNum) ? ageNum : (beat ? now - beat : null);
    const dotCls = (age !== null && age < 60) ? "green" : (age !== null && age < 240) ? "yellow" : "gray";

    const card = el("div", { class: "mon-card clickable", title: "点击查看实时交互画面" },
      el("div", { class: "mon-card-head" },
        el("div", { class: "mon-user" },
          el("span", { class: "dot green", style: "width:7px;height:7px;" }),
          el("span", { class: "ellipsis", title: s.display_name || "" }, s.display_name || s.name || s.user_id || "—")),
        el("span", { class: "badge badge-running" }, "● 运行中")),
      el("div", { class: "mon-title", title: s.title || "" }, s.title || "（无标题）"),
      el("div", { class: "mon-row" }, el("span", { class: "lbl" }, "Agent"), el("span", { class: "val" }, s.agent || "—")),
      el("div", { class: "mon-row" }, el("span", { class: "lbl" }, "工作空间"),
        el("span", { class: "val", title: s.workspace || "" }, s.workspace || "—")),
      el("div", { class: "mon-row" }, el("span", { class: "lbl" }, "已运行"),
        el("span", { class: "val" }, duration === null ? "—" : fmtDuration(duration))),
      el("div", { class: "mon-foot" },
        el("span", { class: "mon-row" },
          el("span", { class: "dot " + dotCls, style: "width:7px;height:7px;" }),
          el("span", null, `最后心跳 ${fmtRel(s.last_beat)}`)),
        el("span", { class: "mono" }, s.surface || "")));
    // v5.4.0：点击卡片 → 打开会话详情抽屉（WebSocket 实时交互画面）
    card.addEventListener("click", () => {
      openSessionDrawer(s.user_id, s.session_id, {
        title: s.title || "",
        user_name: s.display_name || s.name || s.user_id || "",
      });
    });
    grid.append(card);
  }
}

/* ===================================================================
   12. 审计日志（#/audit）
   =================================================================== */

const TIME_RANGES = [
  ["1h", "最近 1 小时", 3600],
  ["24h", "最近 24 小时", 86400],
  ["7d", "最近 7 天", 604800],
  ["all", "全部", 0],
];

const auditUI = { built: false, eventsTbody: null, loginsTbody: null, typeSel: null, userSel: null, loginUserSel: null, rangeBtns: [], eventsPager: null, loginsPager: null, tabBtns: [], panels: {} };

function buildAuditView() {
  if (auditUI.built) return;
  auditUI.built = true;
  const root = $("#view-audit");
  clear(root);

  /* ---- 事件日志面板 ---- */
  auditUI.typeSel = el("select", { class: "select", id: "audit-type", style: "max-width:240px;" },
    el("option", { value: "" }, "全部类型"));
  auditUI.userSel = el("select", { class: "select", id: "audit-user", style: "max-width:200px;" },
    el("option", { value: "" }, "全部用户"));

  auditUI.typeSel.addEventListener("change", () => {
    state.audit.type = auditUI.typeSel.value; state.audit.page = 0;
    refreshAuditEvents().catch(e => toast(e.message, "error"));
  });
  auditUI.userSel.addEventListener("change", () => {
    state.audit.userId = auditUI.userSel.value; state.audit.page = 0;
    refreshAuditEvents().catch(e => toast(e.message, "error"));
  });

  const seg = el("span", { class: "seg" });
  for (const [key, label, seconds] of TIME_RANGES) {
    const btn = el("button", { type: "button" }, label);
    btn.dataset.range = key;
    btn.addEventListener("click", () => {
      state.audit.range = seconds; state.audit.page = 0;
      syncRangeButtons();
      refreshAuditEvents().catch(e => toast(e.message, "error"));
    });
    auditUI.rangeBtns.push(btn);
    seg.append(btn);
  }
  syncRangeButtons();

  auditUI.eventsTbody = el("tbody", { id: "audit-events-tbody" });
  auditUI.eventsPager = el("div", { class: "pager", id: "audit-events-pager" });
  const eventsPanel = el("div", null,
    el("div", { class: "filter-bar" },
      el("span", { class: "filter-label" }, "事件类型"), auditUI.typeSel,
      el("span", { class: "filter-label" }, "用户"), auditUI.userSel,
      el("span", { class: "filter-label" }, "时间范围"), seg,
      el("span", { class: "filter-spacer" }),
      el("button", { class: "btn btn-sm", type: "button", onclick: () => exportEventsCsv() }, "⬇ 导出 CSV"),
      el("button", { class: "btn btn-sm", type: "button", onclick: () => refreshAuditEvents(true) }, "🔄 刷新")),
    el("div", { class: "table-wrap" },
      el("table", { class: "table" },
        el("thead", null, el("tr", null,
          ...["时间", "用户", "类型", "会话ID", "摘要"].map(h => el("th", null, h)))),
        auditUI.eventsTbody)),
    auditUI.eventsPager);

  /* ---- 登录记录面板 ---- */
  auditUI.loginUserSel = el("select", { class: "select", id: "audit-login-user", style: "max-width:200px;" },
    el("option", { value: "" }, "全部用户"));
  auditUI.loginUserSel.addEventListener("change", () => {
    state.loginRec.userId = auditUI.loginUserSel.value; state.loginRec.page = 0;
    refreshLogins().catch(e => toast(e.message, "error"));
  });
  auditUI.loginsTbody = el("tbody", { id: "audit-logins-tbody" });
  auditUI.loginsPager = el("div", { class: "pager", id: "audit-logins-pager" });
  const loginsPanel = el("div", null,
    el("div", { class: "filter-bar" },
      el("span", { class: "filter-label" }, "用户"), auditUI.loginUserSel,
      el("span", { class: "filter-spacer" }),
      el("button", { class: "btn btn-sm", type: "button", onclick: () => refreshLogins(true) }, "🔄 刷新")),
    el("div", { class: "table-wrap" },
      el("table", { class: "table" },
        el("thead", null, el("tr", null,
          ...["时间", "用户", "IP", "成功", "原因"].map(h => el("th", null, h)))),
        auditUI.loginsTbody)),
    auditUI.loginsPager);

  auditUI.panels = { events: eventsPanel, logins: loginsPanel };

  /* ---- Tabs ---- */
  const tabs = el("div", { class: "tabs" });
  const mkTab = (key, label) => {
    const t = el("div", { class: "tab" + (state.audit.tab === key ? " active" : "") }, label);
    t.addEventListener("click", () => {
      state.audit.tab = key;
      syncAuditTabs();
      if (key === "logins") refreshLogins(false).catch(e => toast(e.message, "error"));
    });
    auditUI.tabBtns.push({ key, node: t });
    return t;
  };
  tabs.append(mkTab("events", "事件日志"), mkTab("logins", "登录记录"));

  root.append(
    el("div", { class: "page-header" },
      el("div", null,
        el("div", { class: "page-title" }, "审计日志"),
        el("div", { class: "page-desc" }, "客户端事件上报与登录记录（服务端时间）"))),
    tabs,
    eventsPanel,
    loginsPanel);
  syncAuditTabs();
}

function syncAuditTabs() {
  for (const t of auditUI.tabBtns) t.node.classList.toggle("active", t.key === state.audit.tab);
  for (const key of Object.keys(auditUI.panels)) {
    auditUI.panels[key].classList.toggle("hidden", key !== state.audit.tab);
  }
}

function syncRangeButtons() {
  const seconds = state.audit.range;
  for (const btn of auditUI.rangeBtns) {
    const rangeDef = TIME_RANGES.find(r => r[0] === btn.dataset.range);
    btn.classList.toggle("active", !!rangeDef && rangeDef[2] === seconds);
  }
}

async function loadAuditView(params, seq) {
  buildAuditView();
  try { await ensureUsers(); } catch (e) { /* ignore */ }
  fillUserSelect(auditUI.userSel, { value: state.audit.userId });
  fillUserSelect(auditUI.loginUserSel, { value: state.loginRec.userId });
  try {
    await ensureEventTypes();
    fillEventTypes();
  } catch (e) { /* ignore */ }
  try {
    if (state.audit.tab === "logins") await refreshLogins(false);
    else await refreshAuditEvents(false);
  } catch (e) {
    if (e.status !== 401) toast(e.message || "审计日志加载失败", "error");
  }
}

async function ensureEventTypes(force) {
  if (state.eventTypes && !force) return state.eventTypes;
  const res = await api("/event_types");
  state.eventTypes = res.types || [];
  return state.eventTypes;
}

function fillEventTypes() {
  const sel = auditUI.typeSel;
  if (!sel) return;
  const current = state.audit.type;
  clear(sel);
  sel.append(el("option", { value: "" }, "全部类型"));
  for (const t of (state.eventTypes || [])) {
    const label = `${t.type}（${t.c}）`;
    sel.append(el("option", { value: t.type }, label));
  }
  sel.value = current || "";
}

function auditEventsQuery(withPage) {
  const a = state.audit;
  const qs = new URLSearchParams();
  if (a.type) qs.set("type", a.type);
  if (a.userId) qs.set("user_id", a.userId);
  if (a.range > 0) {
    const ts = Math.floor(nowSec());
    qs.set("from_ts", String(ts - a.range));
    qs.set("to_ts", String(ts));
  }
  if (withPage) {
    qs.set("limit", String(PAGE_LIMIT));
    qs.set("offset", String(a.page * PAGE_LIMIT));
  }
  return qs;
}

async function refreshAuditEvents(showToast) {
  const a = state.audit;
  const res = await api(`/events?${auditEventsQuery(true).toString()}`);
  a.total = res.total || 0;
  renderEventsTable(res.events || []);
  renderEventsPager();
  if (showToast) toast("已刷新事件日志", "success");
}

function renderEventsTable(events) {
  const tbody = auditUI.eventsTbody;
  clear(tbody);
  if (!events.length) {
    tbody.append(el("tr", null, el("td", { colspan: "5" }, emptyState("暂无数据", "📭"))));
    return;
  }
  for (const ev of events) {
    tbody.append(el("tr", null,
      el("td", { class: "td-mono td-nowrap", title: fmtAbs(ev.ts) }, fmtClock(ev.ts)),
      el("td", { class: "td-nowrap" }, ev.user_name || ev.user_id || "—"),
      el("td", null, badge(ev.type || "—", eventBadgeClass(ev.type))),
      el("td", { class: "td-mono", title: ev.session_id || "" }, truncate(ev.session_id || "—", 18)),
      el("td", { class: "ellipsis", style: "max-width:430px;", title: summarizePayload(ev.payload) }, summarizePayload(ev.payload) || "—")));
  }
}

function renderEventsPager() {
  const a = state.audit;
  const pages = Math.max(1, Math.ceil(a.total / PAGE_LIMIT));
  const page = Math.min(a.page + 1, pages);
  clear(auditUI.eventsPager);
  auditUI.eventsPager.append(
    el("button", {
      class: "btn btn-sm", type: "button", disabled: a.page <= 0 ? "disabled" : null,
      onclick: () => { if (state.audit.page > 0) { state.audit.page -= 1; refreshAuditEvents().catch(e => toast(e.message, "error")); } },
    }, "← 上一页"),
    el("span", { class: "pager-info" }, `第 ${page} / ${pages} 页 · 共 ${fmtInt(a.total)} 条`),
    el("button", {
      class: "btn btn-sm", type: "button", disabled: (a.page + 1 >= pages) ? "disabled" : null,
      onclick: () => { if (state.audit.page + 1 < pages) { state.audit.page += 1; refreshAuditEvents().catch(e => toast(e.message, "error")); } },
    }, "下一页 →"));
}

async function exportEventsCsv() {
  try {
    await downloadCsv(`/export/events.csv?${auditEventsQuery(false).toString()}`, "cbhcli_events.csv");
    toast("CSV 已开始下载", "success");
  } catch (e) {
    toast(e.message || "导出失败", "error");
  }
}

async function refreshLogins(showToast) {
  const lr = state.loginRec;
  const qs = new URLSearchParams();
  if (lr.userId) qs.set("user_id", lr.userId);
  qs.set("limit", String(PAGE_LIMIT));
  qs.set("offset", String(lr.page * PAGE_LIMIT));
  const res = await api(`/logins?${qs.toString()}`);
  lr.total = res.total || 0;
  renderLoginsTable(res.logins || []);
  renderLoginsPager();
  if (showToast) toast("已刷新登录记录", "success");
}

function renderLoginsTable(logins) {
  const tbody = auditUI.loginsTbody;
  clear(tbody);
  if (!logins.length) {
    tbody.append(el("tr", null, el("td", { colspan: "5" }, emptyState("暂无登录记录", "🔐"))));
    return;
  }
  for (const r of logins) {
    const ok = !!r.success;
    tbody.append(el("tr", null,
      el("td", { class: "td-mono td-nowrap", title: fmtAbs(r.ts) }, fmtAbs(r.ts)),
      el("td", { class: "td-nowrap" }, r.user_name || r.user_id || "—"),
      el("td", { class: "td-mono" }, r.ip || "—"),
      el("td", null, ok ? el("span", { class: "badge green" }, "✅ 成功") : el("span", { class: "badge red" }, "❌ 失败")),
      el("td", { class: "ellipsis", style: "max-width:380px;", title: r.reason || "" }, r.reason || "—")));
  }
}

function renderLoginsPager() {
  const lr = state.loginRec;
  const pages = Math.max(1, Math.ceil(lr.total / PAGE_LIMIT));
  const page = Math.min(lr.page + 1, pages);
  clear(auditUI.loginsPager);
  auditUI.loginsPager.append(
    el("button", {
      class: "btn btn-sm", type: "button", disabled: lr.page <= 0 ? "disabled" : null,
      onclick: () => { if (state.loginRec.page > 0) { state.loginRec.page -= 1; refreshLogins().catch(e => toast(e.message, "error")); } },
    }, "← 上一页"),
    el("span", { class: "pager-info" }, `第 ${page} / ${pages} 页 · 共 ${fmtInt(lr.total)} 条`),
    el("button", {
      class: "btn btn-sm", type: "button", disabled: (lr.page + 1 >= pages) ? "disabled" : null,
      onclick: () => { if (state.loginRec.page + 1 < pages) { state.loginRec.page += 1; refreshLogins().catch(e => toast(e.message, "error")); } },
    }, "下一页 →"));
}

/* ===================================================================
   13. 设置（#/settings）
   =================================================================== */

const setUI = { built: false };

function buildSettingsView() {
  if (setUI.built) return;
  setUI.built = true;
  const root = $("#view-settings");
  clear(root);

  setUI.health = el("div", { class: "kv-grid", id: "set-health" },
    el("div", { class: "kv-item" }, el("div", { class: "kv-label" }, "状态"), el("div", { class: "kv-value" }, "加载中…")));

  setUI.strictOnline = el("input", { type: "checkbox", id: "set-strict-online" });
  setUI.reportLevel = el("select", { class: "select", id: "set-report-level" },
    el("option", { value: "full" }, "full（完整上报）"),
    el("option", { value: "summary" }, "summary（摘要上报）"));
  setUI.ttlDays = el("input", { class: "input", id: "set-ttl", type: "number", min: "0", step: "1", placeholder: "留空 = 永久", style: "width:160px;" });
  setUI.graceMinutes = el("input", { class: "input", id: "set-grace", type: "number", min: "0", step: "1", style: "width:160px;" });
  setUI.snapshotMaxMb = el("input", { class: "input", id: "set-snap", type: "number", min: "0", step: "1", style: "width:160px;" });
  setUI.settingsMsg = el("div", { class: "ok-line hidden" });

  const saveBtn = el("button", { class: "btn btn-primary", type: "button", onclick: () => saveSettings(saveBtn) }, "保存设置");

  setUI.pwOld = el("input", { class: "input", id: "pw-old", type: "password", autocomplete: "current-password", placeholder: "当前主密码" });
  setUI.pwNew = el("input", { class: "input", id: "pw-new", type: "password", autocomplete: "new-password", placeholder: "新主密码（至少 4 位）" });
  setUI.pwConfirm = el("input", { class: "input", id: "pw-confirm", type: "password", autocomplete: "new-password", placeholder: "确认新主密码" });
  setUI.pwMsg = el("div", { class: "error-line hidden" });
  const pwBtn = el("button", { class: "btn", type: "button", onclick: () => changeMasterPassword(pwBtn) }, "修改主密码");

  setUI.info = el("div", { class: "kv-grid", id: "set-info" });

  root.append(
    el("div", { class: "page-header" },
      el("div", null,
        el("div", { class: "page-title" }, "设置"),
        el("div", { class: "page-desc" }, "服务自检 / 默认上报策略 / 主密码")),
      el("div", { class: "page-actions" },
        el("button", { class: "btn btn-sm", type: "button", onclick: () => { loadSettingsView({}, state.navSeq); toast("已重新自检", "success"); } }, "🔄 自检"))),

    el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("div", { class: "card-title" }, "🩺 服务自检"),
        el("div", { class: "card-actions" },
          el("button", { class: "btn btn-sm", type: "button", onclick: () => selfCheckSettings() }, "重新自检"))),
      setUI.health),

    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", { class: "card-title" }, "⚙️ 服务设置")),
      el("div", { class: "form-grid" },
        el("div", { class: "form-row" },
          el("label", null, "严格在线模式（strict_online）"),
          el("label", { class: "checkbox-row" }, setUI.strictOnline, "启用后，客户端离线超过宽限时间将被拦截"),
          el("div", { class: "form-hint" }, "严格在线模式下，超过宽限时间未心跳的用户请求会被拒绝")),
        el("div", { class: "form-row" },
          el("label", null, "上报级别（report_level）"), setUI.reportLevel),
        el("div", { class: "form-row" },
          el("label", null, "新用户默认有效期（default_token_ttl_days）"), setUI.ttlDays,
          el("div", { class: "form-hint" }, "单位：天；留空 = 永久")),
        el("div", { class: "form-row" },
          el("label", null, "严格模式宽限（strict_grace_minutes）"), setUI.graceMinutes,
          el("div", { class: "form-hint" }, "单位：分钟")),
        el("div", { class: "form-row" },
          el("label", null, "快照上限（snapshot_max_mb）"), setUI.snapshotMaxMb,
          el("div", { class: "form-hint" }, "单位：MB")),
        el("div", null)),
      el("div", { class: "btn-row" }, saveBtn),
      setUI.settingsMsg),

    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", { class: "card-title" }, "🔑 修改主密码")),
      el("div", { class: "form-grid" },
        el("div", { class: "form-row" }, el("label", null, "当前密码"), setUI.pwOld),
        el("div", null),
        el("div", { class: "form-row" }, el("label", null, "新密码"), setUI.pwNew),
        el("div", { class: "form-row" }, el("label", null, "确认新密码"), setUI.pwConfirm)),
      el("div", { class: "btn-row" }, pwBtn),
      setUI.pwMsg),

    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", { class: "card-title" }, "📁 数据目录信息")),
      setUI.info));
}

async function loadSettingsView(params, seq) {
  buildSettingsView();
  const mySeq = state.navSeq;
  try {
    const results = await Promise.all([api("/settings"), api("/health").catch(() => null)]);
    if (state.route !== "settings" || state.navSeq !== mySeq) return;
    const st = results[0];
    const hl = results[1];
    fillSettingsForm(st.settings || {});
    renderSettingsInfo(st);
    if (hl) renderSettingsHealth(hl);
  } catch (e) {
    if (e.status !== 401) toast(e.message || "设置加载失败", "error");
  }
}

function renderSettingsHealth(hl) {
  clear(setUI.health);
  appendChildren(setUI.health, buildHealthItems(hl, 0));
}

async function selfCheckSettings() {
  try {
    const hl = await api("/health");
    renderSettingsHealth(hl);
    if (hl.version) setVersion(hl.version);
    toast("自检完成", "success");
  } catch (e) {
    toast(e.message || "自检失败", "error");
  }
}

function fillSettingsForm(s) {
  setUI.strictOnline.checked = !!s.strict_online;
  setUI.reportLevel.value = s.report_level === "summary" ? "summary" : "full";
  setUI.ttlDays.value = (s.default_token_ttl_days === null || s.default_token_ttl_days === undefined) ? "" : String(s.default_token_ttl_days);
  setUI.graceMinutes.value = (s.strict_grace_minutes === null || s.strict_grace_minutes === undefined) ? "" : String(s.strict_grace_minutes);
  setUI.snapshotMaxMb.value = (s.snapshot_max_mb === null || s.snapshot_max_mb === undefined) ? "" : String(s.snapshot_max_mb);
}

function renderSettingsInfo(st) {
  clear(setUI.info);
  const add = (label, value) => setUI.info.append(el("div", { class: "kv-item" },
    el("div", { class: "kv-label" }, label), el("div", { class: "kv-value" }, value)));
  add("数据目录", st.data_dir || "—");
  add("服务版本", st.version ? `cbhcli ${st.version}` : "—");
  add("主密码校验源", st.master_key_source || "—");
}

function readNumberInput(input, { allowEmpty = false, min = 0 } = {}) {
  const raw = String(input.value === undefined || input.value === null ? "" : input.value).trim();
  if (raw === "") return allowEmpty ? null : NaN;
  const n = Number(raw);
  if (!isFinite(n) || n < min) return NaN;
  return n;
}

async function saveSettings(btn) {
  const ttl = readNumberInput(setUI.ttlDays, { allowEmpty: true, min: 1 });
  const grace = readNumberInput(setUI.graceMinutes, { allowEmpty: false, min: 0 });
  const snap = readNumberInput(setUI.snapshotMaxMb, { allowEmpty: false, min: 0 });
  setUI.settingsMsg.classList.add("hidden");
  if (isNaN(ttl)) { showSettingsMsg("默认有效期必须为 ≥1 的整数，或留空表示永久", true); return; }
  if (isNaN(grace) || !Number.isInteger(grace) || grace < 0) { showSettingsMsg("宽限时间必须为 ≥0 的整数分钟", true); return; }
  if (isNaN(snap) || !Number.isInteger(snap) || snap < 0) { showSettingsMsg("快照上限必须为 ≥0 的整数 MB", true); return; }

  btn.disabled = true;
  try {
    const res = await api("/settings", {
      method: "PUT",
      body: {
        strict_online: !!setUI.strictOnline.checked,
        report_level: setUI.reportLevel.value,
        default_token_ttl_days: ttl,
        strict_grace_minutes: grace,
        snapshot_max_mb: snap,
      },
    });
    fillSettingsForm(res.settings || {});
    showSettingsMsg("设置已保存", false);
    toast("设置已保存", "success");
  } catch (e) {
    showSettingsMsg(e.message || "保存失败", true);
    toast(e.message || "保存失败", "error");
  } finally {
    btn.disabled = false;
  }
}

function showSettingsMsg(text, isError) {
  const node = setUI.settingsMsg;
  node.classList.remove("hidden");
  node.className = isError ? "error-line" : "ok-line";
  node.textContent = text;
  setTimeout(() => { node.classList.add("hidden"); }, 5000);
}

async function changeMasterPassword(btn) {
  const oldPw = setUI.pwOld.value;
  const newPw = setUI.pwNew.value;
  const confirmPw = setUI.pwConfirm.value;
  const msg = setUI.pwMsg;
  const showMsg = (text, isError) => {
    msg.className = isError ? "error-line" : "ok-line";
    msg.classList.remove("hidden");
    msg.textContent = text;
  };
  msg.classList.add("hidden");

  if (!oldPw || !newPw || !confirmPw) { showMsg("请完整填写三项密码字段", true); return; }
  if (newPw.length < 4) { showMsg("新密码过短（至少 4 位）", true); return; }
  if (newPw !== confirmPw) { showMsg("两次输入的新密码不一致", true); return; }

  btn.disabled = true;
  try {
    // 注意：本接口 401 invalid_password 表示「当前主密码错误」，会话仍有效，不登出
    const res = await api("/settings/password", {
      method: "POST",
      body: { old_password: oldPw, new_password: newPw },
      on401: "error",
    });
    setUI.pwOld.value = "";
    setUI.pwNew.value = "";
    setUI.pwConfirm.value = "";
    showMsg((res && res.message) || "主密码已更新（下次登录生效）", false);
    toast("主密码已更新（下次登录生效）", "success");
  } catch (e) {
    if (e.status === 401) showMsg(e.message || "当前主密码错误", true);
    else showMsg(e.message || "修改失败", true);
    toast(e.message || "修改失败", "error");
  } finally {
    btn.disabled = false;
  }
}

/* ===================================================================
   14. 启动引导
   =================================================================== */

function boot() {
  initLogin();
  startClock();

  const logoutBtn = $("#btn-logout");
  if (logoutBtn) logoutBtn.addEventListener("click", () => { doLogout().catch(() => {}); });

  // 全局实时事件监听（v5.4.0）：
  // · 监控页：事件到达即时刷新（节流 600ms；2s 轮询兜底）
  // · 会话页：新会话/运行中状态实时上屏（节流 700ms；5s 轮询兜底）
  // · 会话详情抽屉：openSessionDrawer 各自注册监听（实时交互画面）
  LiveWS.addListener((msg) => {
    if (state.route === "monitor") {
      if (monUI.wsTimer) return;
      monUI.wsTimer = setTimeout(() => {
        monUI.wsTimer = null;
        refreshMonitor(false).catch(() => {});
      }, 600);
    } else if (state.route === "sessions") {
      if (sessUI.wsTimer) return;
      sessUI.wsTimer = setTimeout(() => {
        sessUI.wsTimer = null;
        autoRefreshSessions().catch(() => {});
      }, 700);
    }
  });

  if (getToken()) {
    showAppShell();
    startServiceWatch();
    LiveWS.connect();
    const raw = (location.hash || "").replace(/^#\/?/, "");
    if (!raw || raw === "login") location.hash = "#/dashboard";
    else handleRoute();
  } else {
    showLoginView("");
    if (location.hash !== "#/login") location.hash = "#/login";
  }
}

window.addEventListener("hashchange", handleRoute);

boot();
