"""QQ Bot WebSocket 网关连接管理

负责:
- 获取 AccessToken
- 获取网关地址
- 建立 WebSocket 连接
- 鉴权 (IDENTIFY)
- 心跳维持
- 断线重连 (RESUME)
- 事件接收与分发

鉴权流程（两步）:
  1. POST https://bots.qq.com/app/getAppAccessToken → access_token
  2. GET  https://api.sgroup.qq.com/gateway/bot (Authorization: QQBot {access_token}) → WebSocket URL
  3. WebSocket Identify: QQBot {access_token}

参考 QQ 开放平台文档:
  https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/reference.html
"""
import json
import logging
import threading
import time
import requests
import websocket
from typing import Optional, Callable

from cbhcli_pkg.qqbot.qqbot_config import QQBotConfig
from cbhcli_pkg.qqbot.protocol import (
    QQBotProtocol, WSPayload, OpCode
)

logger = logging.getLogger(__name__)

# API 地址
ACCESS_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_API_URL = "https://api.sgroup.qq.com/gateway/bot"
GATEWAY_SANDBOX_URL = "https://sandbox.api.sgroup.qq.com/gateway/bot"

# 默认重连配置
DEFAULT_RECONNECT_DELAY = 3
MAX_RECONNECT_DELAY = 60
RECONNECT_BACKOFF = 1.5
HTTP_TIMEOUT = 30  # HTTP 请求超时（从10提升到30秒）
HTTP_RETRY_COUNT = 3  # HTTP 请求重试次数


class QQBotGateway:
    """QQ Bot WebSocket 网关连接

    负责与 QQ 开放平台建立 WebSocket 长连接，
    处理鉴权、心跳和事件分发。

    使用示例:
        gw = QQBotGateway(config, on_event=my_handler)
        gw.connect()
        gw.run()  # 阻塞运行
    """

    def __init__(
        self,
        config: QQBotConfig,
        on_event: Optional[Callable] = None,
        on_ready: Optional[Callable] = None,
        on_disconnect: Optional[Callable] = None,
    ):
        """
        Args:
            config: QQ Bot 配置
            on_event: 事件回调 (payload: WSPayload) -> None
            on_ready: 连接就绪回调 (session_id: str) -> None
            on_disconnect: 断开连接回调 (reason: str) -> None
        """
        self.config = config
        self._on_event = on_event
        self._on_ready = on_ready
        self._on_disconnect = on_disconnect

        # WebSocket 连接
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None

        # 会话状态
        self._session_id: Optional[str] = None
        self._last_seq: Optional[int] = None
        self._heartbeat_interval: int = 45000  # ms，默认45秒
        self._heartbeat_timer: Optional[threading.Timer] = None
        self._connected = False
        self._running = False

        # 鉴权
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

        # 重连状态
        self._reconnect_count = 0
        self._should_reconnect = True
        self._reconnect_lock = threading.Lock()
        self._reconnecting = False

    # ════════════════════════════════════════════════
    # 公共接口
    # ════════════════════════════════════════════════

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def connect(self) -> bool:
        """建立 WebSocket 连接并完成鉴权

        1. 获取 access_token
        2. 获取网关地址
        3. 建立 WebSocket
        4. 等待 HELLO → 发送 IDENTIFY

        Returns:
            True 如果连接成功
        """
        if self._connected:
            logger.warning("已在连接中，先断开旧连接再重连")
            self._connected = False
            self._cancel_heartbeat()
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._ws = None

        try:
            # 1. 获取 access_token
            if not self._get_access_token():
                logger.error("获取 access_token 失败")
                return False

            # 2. 获取网关地址
            gw_url = self._get_gateway_url()
            if not gw_url:
                logger.error("获取网关地址失败")
                return False

            logger.info(f"连接到网关: {gw_url}")

            # 3. 建立 WebSocket
            self._should_reconnect = True
            self._ws = websocket.WebSocketApp(
                gw_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            # 4. 在独立线程中运行
            self._ws_thread = threading.Thread(
                target=self._ws.run_forever,
                kwargs={
                    'ping_interval': 0,
                    'ping_timeout': None,
                },
                daemon=True
            )
            self._ws_thread.start()

            # 等待连接就绪（最多45秒）
            timeout = 45
            start = time.time()
            while not self._connected and (time.time() - start) < timeout:
                time.sleep(0.1)

            if not self._connected:
                logger.error("连接超时")
                return False

            return True

        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        self._should_reconnect = False
        self._running = False
        self._cancel_heartbeat()

        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._connected = False
        self._session_id = None
        logger.info("已断开连接")

    def run(self):
        """阻塞运行（在主线程调用）

        通常由 QQBotService 在独立线程中调用。
        """
        self._running = True
        while self._running:
            time.sleep(1)

    # ════════════════════════════════════════════════
    # 内部方法
    # ════════════════════════════════════════════════

    def _get_access_token(self) -> bool:
        """获取 QQ Bot access_token

        POST https://bots.qq.com/app/getAppAccessToken
        带重试机制，网络波动时不会一次失败就放弃。
        """
        for attempt in range(HTTP_RETRY_COUNT):
            try:
                resp = requests.post(
                    ACCESS_TOKEN_URL,
                    json={
                        "appId": self.config.appId,
                        "clientSecret": self.config.appSecret,
                    },
                    timeout=HTTP_TIMEOUT
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._access_token = data.get('access_token', '')
                    expires_in = int(data.get('expires_in', 7200))
                    self._token_expires_at = time.time() + expires_in - 60
                    logger.info(f"access_token 获取成功 (有效期 {expires_in}s)")
                    return True
                else:
                    logger.error(f"获取 access_token 失败: HTTP {resp.status_code} {resp.text[:200]}")
                    if attempt < HTTP_RETRY_COUNT - 1:
                        time.sleep(2 * (attempt + 1))
                    continue
            except requests.RequestException as e:
                logger.error(f"获取 access_token 请求失败 (第{attempt+1}次): {e}")
                if attempt < HTTP_RETRY_COUNT - 1:
                    time.sleep(2 * (attempt + 1))
        return False

    def _ensure_access_token(self) -> bool:
        """确保 access_token 有效"""
        if self._access_token and time.time() < self._token_expires_at:
            return True
        return self._get_access_token()

    def _get_gateway_url(self) -> Optional[str]:
        """获取 WebSocket 网关地址 (QQBot access_token)

        注意: QQ 官方 API 要求 Authorization 头使用 "QQBot {access_token}" 前缀
        （与 api_client.py 保持一致），"Bearer {access_token}" 会被拒绝:
        401 {"message":"请求头Authorization参数格式错误","code":11241}

        带重试机制，网络波动时不会一次失败就放弃。
        """
        if not self._ensure_access_token():
            return None

        base_url = GATEWAY_SANDBOX_URL if self.config.sandbox else GATEWAY_API_URL

        for attempt in range(HTTP_RETRY_COUNT):
            try:
                resp = requests.get(
                    base_url,
                    headers={
                        "Authorization": f"QQBot {self._access_token}",
                        "X-Union-Appid": self.config.appId,
                    },
                    timeout=HTTP_TIMEOUT
                )
                if resp.status_code == 200:
                    data = resp.json()
                    gw_url = data.get('url', '')
                    logger.info(f"网关地址: {gw_url[:60]}...")
                    return gw_url
                elif resp.status_code == 401:
                    logger.info("Token 可能过期，刷新后重试...")
                    if self._get_access_token():
                        continue  # 重试
                    return None
                else:
                    logger.error(f"获取网关地址失败: HTTP {resp.status_code} {resp.text[:200]}")
                    if attempt < HTTP_RETRY_COUNT - 1:
                        time.sleep(2 * (attempt + 1))
                    continue
            except requests.RequestException as e:
                logger.error(f"网关请求失败 (第{attempt+1}次): {e}")
                if attempt < HTTP_RETRY_COUNT - 1:
                    time.sleep(2 * (attempt + 1))
        return None

    # ---- 协议处理 ----

    def _handle_hello(self, payload: WSPayload):
        """处理 HELLO + 发送 IDENTIFY"""
        if isinstance(payload.d, dict) and 'heartbeat_interval' in payload.d:
            self._heartbeat_interval = payload.d['heartbeat_interval']

        self._start_heartbeat()

        if not self._ensure_access_token():
            logger.error("无法获取 access_token，鉴权失败")
            return

        identify = QQBotProtocol.identify(
            token=f"QQBot {self._access_token}",
            intents=self.config.intents,
        )
        self._send(identify)
        logger.info(f"已发送 IDENTIFY (intents={self.config.intents})")

    def _on_open(self, ws):
        """WebSocket 连接建立回调"""
        logger.info("WebSocket 连接已建立，等待 HELLO...")

    def _on_message(self, ws, message: str):
        """WebSocket 消息接收回调"""
        try:
            payload = QQBotProtocol.decode(message)
        except json.JSONDecodeError:
            logger.debug(f"无法解析的消息: {message[:200]}")
            return

        op = payload.op

        if op == OpCode.HELLO:
            self._handle_hello(payload)

        elif op == OpCode.HEARTBEAT_ACK:
            logger.debug("心跳 ACK")

        elif op == OpCode.DISPATCH:
            self._handle_dispatch(payload)

        elif op == OpCode.RECONNECT:
            logger.warning("服务端请求重连 (OpCode 7)")
            # 防止重复触发：标记并主动断开旧连接
            if self._reconnecting:
                logger.debug("已在重连中，忽略 OpCode 7")
                return
            self._connected = False
            self._cancel_heartbeat()
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass
            self._do_reconnect()

        elif op == OpCode.INVALID_SESSION:
            self._handle_invalid_session(payload)

        else:
            logger.debug(f"未知 OpCode: {op}")

    def _on_error(self, ws, error):
        """WebSocket 错误回调"""
        logger.error(f"WebSocket 错误: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """WebSocket 关闭回调"""
        # 只处理当前 WebSocket 的关闭，忽略已替换的旧连接残留回调
        if ws is not self._ws:
            logger.debug("旧 WebSocket 关闭回调，忽略")
            return

        reason = f"code={close_status_code}, msg={close_msg}"
        logger.info(f"WebSocket 连接关闭: {reason}")

        was_connected = self._connected
        self._connected = False
        self._cancel_heartbeat()

        # 如果之前正在重连中，连接关闭说明重连尝试失败，清除标志
        was_reconnecting = self._reconnecting
        self._reconnecting = False

        if self._on_disconnect:
            self._on_disconnect(reason)

        # 自动重连
        # - was_connected: 正常运行中断线 → 需要重连
        # - was_reconnecting: RESUME/重连尝试中的连接断开 → 需要继续重连
        if self._should_reconnect and (was_connected or was_reconnecting):
            self._schedule_reconnect()

    # ──── 协议处理 ────

    def _handle_dispatch(self, payload: WSPayload):
        """处理 DISPATCH 事件"""
        # 更新序列号
        if payload.s is not None:
            self._last_seq = payload.s

        event_type = payload.t or "UNKNOWN"

        # 检查是否为 READY 事件
        if event_type == "READY":
            self._connected = True
            self._session_id = payload.d.get('session_id', '') if payload.d else ''
            self._reconnect_count = 0
            self._reconnecting = False
            logger.info(f"✓ 连接就绪! session_id={self._session_id[:20]}...")
            if self._on_ready:
                self._on_ready(self._session_id)
            return

        # 恢复成功
        if event_type == "RESUMED":
            self._connected = True
            self._reconnect_count = 0
            self._reconnecting = False
            logger.info("✓ 连接恢复成功")
            return

        # 将事件分发给回调
        if self._on_event:
            try:
                self._on_event(payload)
            except Exception as e:
                logger.error(f"事件处理异常: {e}", exc_info=True)

    def _handle_invalid_session(self, payload: WSPayload):
        """处理无效会话——重置状态，做完整重连"""
        logger.warning("会话无效，需要重新鉴权")
        self._session_id = None
        self._last_seq = None
        self._connected = False
        self._cancel_heartbeat()

        # 断开旧 WebSocket，然后做完整重连
        if self._should_reconnect:
            with self._reconnect_lock:
                if self._reconnecting:
                    logger.debug("已在重连中，跳过 INVALID_SESSION 处理")
                    return
                self._reconnecting = True
                old_ws = self._ws
                self._ws = None

            if old_ws:
                try:
                    old_ws.close()
                except Exception:
                    pass

            try:
                # 刷新 token
                if not self._get_access_token():
                    logger.error("无法获取 access_token，放弃重新鉴权")
                    self._reconnecting = False
                    if self._should_reconnect:
                        self._schedule_reconnect()
                    if self._on_disconnect:
                        self._on_disconnect("鉴权失败：token 获取失败")
                    return
                # 完整重连（不做 RESUME，因为 session 已无效）
                self._reconnect_count = 0
                self.connect()
            finally:
                self._reconnecting = False

    # ──── 心跳管理 ────

    def _start_heartbeat(self):
        """启动心跳定时器"""
        self._cancel_heartbeat()
        # 使用略短于服务端要求的间隔（安全余量）
        interval = max(self._heartbeat_interval - 5000, 5000) / 1000.0
        self._heartbeat_timer = threading.Timer(interval, self._send_heartbeat)
        self._heartbeat_timer.daemon = True
        self._heartbeat_timer.start()
        logger.debug(f"心跳定时器已启动 (间隔={interval:.0f}s)")

    def _cancel_heartbeat(self):
        """取消心跳定时器"""
        if self._heartbeat_timer:
            self._heartbeat_timer.cancel()
            self._heartbeat_timer = None

    def _send_heartbeat(self):
        """发送心跳并安排下一次"""
        try:
            if self._ws:
                hb = QQBotProtocol.heartbeat(self._last_seq)
                self._ws.send(QQBotProtocol.encode(hb))
                logger.debug("发送心跳")
        except Exception as e:
            logger.error(f"心跳发送失败: {e}")
            # 心跳失败说明连接可能已断开，主动触发重连
            if self._should_reconnect and not self._reconnecting:
                logger.warning("心跳失败，触发重连...")
                self._connected = False
                self._schedule_reconnect()
            return

        # 安排下一次心跳
        if self._connected:
            self._start_heartbeat()

    # ──── 重连 ────

    def _schedule_reconnect(self):
        """安排延迟重连"""
        delay = min(
            DEFAULT_RECONNECT_DELAY * (RECONNECT_BACKOFF ** self._reconnect_count),
            MAX_RECONNECT_DELAY
        )
        logger.info(f"将在 {delay:.0f}s 后重连 (第 {self._reconnect_count + 1} 次)")

        timer = threading.Timer(delay, self._do_reconnect)
        timer.daemon = True
        timer.start()

    def _do_reconnect(self):
        """执行重连（带锁保护，防止并发重连）"""
        if not self._should_reconnect:
            return

        # ── 锁内：设置重连标志 + 清理旧连接状态 ──
        with self._reconnect_lock:
            if self._reconnecting:
                logger.debug("已在重连中，跳过并发重连请求")
                return
            self._reconnecting = True
            self._connected = False
            self._cancel_heartbeat()
            old_ws = self._ws
            self._ws = None

        # ── 锁外：断开旧 WebSocket（避免阻塞回调线程）──
        if old_ws:
            try:
                old_ws.close()
            except Exception:
                pass

        logger.info(f"开始重连... (第 {self._reconnect_count + 1} 次)")

        # ── 刷新 access_token（防止 token 过期导致 RESUME/IDENTIFY 失败）──
        if not self._get_access_token():
            logger.error("重连时获取 access_token 失败，稍后重试...")
            self._reconnecting = False
            self._reconnect_count += 1
            self._schedule_reconnect()
            return

        # ── 前2次重连尝试 RESUME ──
        if self._session_id and self._last_seq is not None and self._reconnect_count < 2:
            try:
                gw_url = self._get_gateway_url()
                if gw_url:
                    logger.info("尝试 RESUME 恢复会话...")
                    self._ws = websocket.WebSocketApp(
                        gw_url,
                        on_open=lambda ws: self._send_resume(),
                        on_message=self._on_message,
                        on_error=self._on_error,
                        on_close=self._on_close,
                    )
                    self._ws_thread = threading.Thread(
                        target=self._ws.run_forever,
                        kwargs={'ping_interval': 0, 'ping_timeout': None},
                        daemon=True,
                    )
                    self._ws_thread.start()
                    self._reconnect_count += 1
                    logger.info("RESUME 请求已发送，等待服务端响应...")
                    # 不设置 _reconnecting=False，等待 RESUMED/READY 事件来清除
                    return
            except Exception as e:
                logger.warning(f"RESUME 重连失败: {e}，回退到完整重连")

        # ── RESUME 不可用或超限 → 完整重连（IDENTIFY）──
        self._session_id = None
        self._last_seq = None

        # 永不放弃：超过 10 次后重置计数，继续重连
        if self._reconnect_count > 10:
            logger.warning(f"重连次数 {self._reconnect_count}，重置计数继续重连...")
            self._reconnect_count = 0

        self._reconnect_count += 1
        try:
            success = self.connect()
            if not success:
                # connect() 失败后继续调度重连，而不是放弃
                logger.warning(f"完整重连失败 (第{self._reconnect_count}次)，将继续重试...")
                self._schedule_reconnect()
        finally:
            self._reconnecting = False

    def _send_resume(self):
        """发送 RESUME 消息"""
        if not self._get_access_token():  # 强制刷新 token 再 RESUME
            logger.error("无法获取 access_token，放弃 RESUME")
            # RESUME 失败，回退到完整重连
            self._reconnecting = False
            self._session_id = None
            self._last_seq = None
            if self._should_reconnect:
                self._schedule_reconnect()
            return
        token = f"QQBot {self._access_token}"
        resume = QQBotProtocol.resume(
            token=token,
            session_id=self._session_id,
            seq=self._last_seq or 0
        )
        self._send(resume)
        logger.info(f"已发送 RESUME (session={self._session_id[:20]}..., seq={self._last_seq})")

    # ──── 发送 ────

    def _send(self, payload: WSPayload):
        """发送 WebSocket 消息"""
        if self._ws:
            raw = QQBotProtocol.encode(payload)
            self._ws.send(raw)
