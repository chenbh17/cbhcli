"""死循环检测器 — Harness 可靠性层

检测两类死循环并在不中断任务的前提下熔断：

A. 工具调用循环（ToolCallTracker）
   - 精确重复：同工具+同参数连续调用 N 次（反复 read 同一文件等）
   - 周期震荡：A→B→A→B 周期 p∈[2,5] 的模式完整重复 ≥2 轮
   处置三级升级：
     ① warn   —— 不拦截，警告附加到工具结果尾部返回模型
     ② block  —— 跳过执行，伪造 ToolResult 告知模型必须换策略
     ③ abort  —— 单请求内干预达上限，跳出 ReAct 循环告知用户

B. 文本生成循环（TextLoopDetector）
   - 流式输出中尾部块（默认 150 字符）在已生成内容中出现 ≥3 次 → 判定复读
   - 用于 reasoning/content 流：截断输出，提示模型简明继续
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import Optional

# 工具循环阈值
LOOP_WARN_THRESHOLD = 3        # 同签名连续出现几次 → 软警告
LOOP_BLOCK_THRESHOLD = 4       # 警告后仍重复（第4次起）→ 硬阻断
LOOP_MAX_INTERVENTIONS = 3     # 单请求内最多干预（block）几次 → 放弃任务
LOOP_CYCLE_MAX_PERIOD = 5      # 周期震荡检测的最大周期
LOOP_TRACK_WINDOW = 20         # 签名滑动窗口大小

# 文本循环阈值
TEXT_LOOP_BLOCK_SIZE = 150     # 复读判定块大小（字符）
TEXT_LOOP_MAX_REPEAT = 3       # 块出现几次 → 判定复读
TEXT_LOOP_MIN_LEN = 600        # 文本少于此长度不做检测（避免误报）

# 干预时不计入循环的工具（Todo 等计划工具同参数重复属正常）
_LOOP_EXEMPT_TOOLS = {"Todo", "ask_user"}


def _signature(tool_name: str, arguments: dict) -> str:
    """生成调用签名（规范化 JSON 后取短哈希）"""
    try:
        canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    except Exception:
        canonical = str(arguments)
    digest = hashlib.md5(f"{tool_name}|{canonical}".encode("utf-8")).hexdigest()
    return f"{tool_name}:{digest[:12]}"


class ToolCallTracker:
    """工具调用循环跟踪器（每个用户请求一个实例）"""

    def __init__(self):
        self._sigs: deque[str] = deque(maxlen=LOOP_TRACK_WINDOW)
        self._names: deque[str] = deque(maxlen=LOOP_TRACK_WINDOW)
        self.interventions = 0   # block 干预次数

    # --------------------------------------------------------------
    def check(self, tool_name: str, arguments: dict) -> tuple[str, Optional[str]]:
        """记录并检查本次调用

        Returns:
            (verdict, message)
            verdict: "ok" | "warn" | "block" | "abort"
            message: warn/block 时应反馈给模型的文本
        """
        if tool_name in _LOOP_EXEMPT_TOOLS:
            return "ok", None

        sig = _signature(tool_name, arguments)

        # ---- 精确重复统计（连续相同签名的个数）----
        consecutive = 0
        for s in reversed(self._sigs):
            if s == sig:
                consecutive += 1
            else:
                break

        # ---- 周期震荡检测（尾部是否存在周期 p 模式完整重复 ≥2 轮）----
        cycle_detected = False
        seq = list(self._names)
        for p in range(2, LOOP_CYCLE_MAX_PERIOD + 1):
            if len(seq) >= p * 2:
                tail = seq[-p * 2:]
                if tail[:p] == tail[p:]:
                    cycle_detected = True
                    break

        # 记录本次
        self._sigs.append(sig)
        self._names.append(tool_name)

        # ---- 判定 ----
        total_repeats = consecutive + 1  # 含本次

        if total_repeats >= LOOP_BLOCK_THRESHOLD:
            self.interventions += 1
            if self.interventions >= LOOP_MAX_INTERVENTIONS:
                return "abort", None
            return "block", (
                f"🛑 [系统熔断] 你已连续 {total_repeats} 次以相同参数调用 {tool_name}，"
                f"系统已阻止本次执行。\n"
                f"禁止再以相同参数调用该工具。必须换一种方式继续任务：\n"
                f"  1. 用 read/grep 重新检查实际状态，找出之前调用为何无效\n"
                f"  2. 更换实现方案或参数\n"
                f"  3. 若确实无法推进，直接向用户说明困难并请求指示"
            )

        if total_repeats >= LOOP_WARN_THRESHOLD or (cycle_detected and len(seq) >= 4):
            if cycle_detected and total_repeats < LOOP_WARN_THRESHOLD:
                hint = f"检测到你在多个工具间来回震荡（周期循环）"
            else:
                hint = f"你已连续 {total_repeats} 次以相同参数调用 {tool_name}"
            return "warn", (
                f"⚠️ [系统检测] {hint}，未取得新进展，疑似陷入循环。\n"
                f"请立即改变策略：① 用 read/grep 查看最新状态 ② 换一种方案 "
                f"③ 向用户说明困难。若继续重复，系统将强制熔断。"
            )

        return "ok", None


class TextLoopDetector:
    """文本复读检测器（流式）

    维护规范化后的累计文本；每次 feed 检查尾部块是否在历史内容中
    重复出现 ≥ TEXT_LOOP_MAX_REPEAT 次。
    """

    def __init__(self, block_size: int = TEXT_LOOP_BLOCK_SIZE,
                 max_repeat: int = TEXT_LOOP_MAX_REPEAT):
        self.block_size = block_size
        self.max_repeat = max_repeat
        self._text: list[str] = []
        self._len = 0
        self.triggered = False

    @staticmethod
    def _normalize(s: str) -> str:
        return re.sub(r"\s+", " ", s)

    def feed(self, chunk: str) -> bool:
        """喂入流式文本块，返回是否检测到复读"""
        if self.triggered:
            return True
        self._text.append(chunk)
        self._len += len(chunk)
        if self._len < TEXT_LOOP_MIN_LEN:
            return False

        full = self._normalize("".join(self._text))
        if len(full) < self.block_size * self.max_repeat:
            return False

        tail = full[-self.block_size:]
        # 统计尾部块在全文中出现次数（含尾部自身）
        count = full.count(tail)
        if count >= self.max_repeat:
            self.triggered = True
            return True
        return False

    def truncated_text(self) -> str:
        """返回截断复读尾部后的文本"""
        full = "".join(self._text)
        if not self.triggered:
            return full
        normalized = self._normalize(full)
        tail = normalized[-self.block_size:]
        # 找到第一次重复出现的位置，从那里截断（保留一个块）
        first = normalized.find(tail)
        if first > 0:
            # 按原文比例近似截断（规范化后长度与原文不同，做保守截断）
            ratio = first / max(1, len(normalized))
            cut = int(len(full) * ratio) + self.block_size
            return full[:cut]
        return full
