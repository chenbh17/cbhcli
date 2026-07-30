"""终端 resize 防抖修复 — 解决窗口大小连续变化时输入框重复显示

问题根因：
prompt_toolkit 的 Application._on_resize 每次 SIGWINCH 都执行
"erase + CPR请求 + 重绘"。拖动窗口边缘时会连续触发几十次 SIGWINCH：
- 每次 erase 依赖上次渲染的光标位置，终端 reflow 后该位置已漂移
- 多个 CPR 请求/响应异步交错，尺寸与光标行号错配
- 中间状态的重绘与 erase 互相踩踏 → 每次 resize 残留一行 ❯，越拖越多

修复思路（参考主流 CLI 的 resize 处理）：
对 SIGWINCH 做防抖——连续事件合并为最后一次，拖动过程中不做
erase/redraw（旧布局暂时保留，终端自行 reflow），拖动结束后做一次
干净的 erase+redraw，从根本上消除中间态错乱。
"""
from __future__ import annotations

import asyncio

# 防抖间隔（秒）：拖动停止 100ms 后才执行真正的 resize 重绘
_RESIZE_DEBOUNCE_SEC = 0.10


def install_resize_fix():
    """安装 resize 防抖补丁（幂等）"""
    from prompt_toolkit.application import Application

    if getattr(Application, '_cbhcli_resize_patched', False):
        return

    _orig_on_resize = Application._on_resize

    def _debounced_on_resize(self):
        """防抖版 _on_resize：合并连续 SIGWINCH，仅执行最后一次"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 不在事件循环中（异常场景），直接执行原始逻辑
            _orig_on_resize(self)
            return

        # 取消上一次未执行的定时器
        handle = getattr(self, '_cbhcli_resize_handle', None)
        if handle is not None:
            handle.cancel()

        def _do_resize():
            self._cbhcli_resize_handle = None
            try:
                _orig_on_resize(self)
            except Exception:
                pass  # 应用已退出等异常场景，忽略

        self._cbhcli_resize_handle = loop.call_later(
            _RESIZE_DEBOUNCE_SEC, _do_resize)

    Application._on_resize = _debounced_on_resize
    Application._cbhcli_resize_patched = True
