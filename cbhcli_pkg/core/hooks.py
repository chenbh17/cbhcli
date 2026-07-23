"""Hooks 钩子系统 — Harness 生命周期编排层

在 Agent 生命周期的固定节点自动执行用户自定义命令（硬规则，不依赖模型自觉）：

  事件            触发时机                       退出码语义
  ─────────────  ────────────────────────────  ─────────────────────────
  SessionStart    会话开始/重置后                 0=正常 stdout 打印给用户(dim)
  UserPromptSubmit 用户提交输入后、发给模型前      0=正常 stdout 追加为用户上下文
  PreToolUse      工具执行前                      0=放行 2=拦截(stderr反馈模型) 其他=警告
  PostToolUse     工具执行后                      0=正常 stdout 追加为模型反馈
  SubagentStop    子Agent任务结束时               0=正常 stdout 打印给用户(dim)
  Stop            AI 回复完成后                   0=正常 stdout 打印给用户(dim)

配置文件（仅信任位置加载，防恶意仓库投毒）：
  全局:  ~/.cbhcli/hooks.json
  Agent: ~/.cbhcli/agents/<agent>/hooks.json（覆盖合并到全局之后）

格式：
  {
    "PreToolUse": [
      {"matcher": "terminal", "command": "python3 ~/.cbhcli/hooks/guard.py"},
      {"matcher": "write|edit", "command": "sh ~/.cbhcli/hooks/protect.sh"}
    ],
    "Stop": [{"command": "notify-send cbhcli 完成"}]
  }

通信协议（与 Claude Code 一致）：
  - 钩子通过 stdin 收到 JSON:
    {"event","tool_name","arguments","result","agent","cwd","session_id"}
  - 同时注入环境变量: CBHCLI_EVENT / CBHCLI_TOOL_NAME / CBHCLI_AGENT / CBHCLI_SESSION_ID
  - 超时 10 秒强杀；钩子异常只警告，绝不阻塞主流程
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from cbhcli_pkg.core.constants import C_DIM, C_ERROR, C_RESET

EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse",
    "PostToolUse", "SubagentStop", "Stop",
)

HOOK_TIMEOUT = 10  # 秒

# 拦截退出码
EXIT_BLOCK = 2


class HookDecision:
    """钩子执行汇总结果"""

    def __init__(self):
        self.blocked = False
        self.block_reason = ""
        self.outputs: list[str] = []   # 各钩子 stdout（注入上下文/反馈用）
        self.warnings: list[str] = []  # 非零非2退出码的 stderr（打印给用户）

    def add_output(self, text: str):
        text = (text or "").strip()
        if text:
            self.outputs.append(text)

    def merged_output(self) -> str:
        return "\n".join(self.outputs)


class HookManager:
    """钩子管理器：加载配置、按事件执行钩子命令"""

    def __init__(self, workspace_path: Optional[Path] = None,
                 agent_name: str = "main"):
        self.workspace_path = workspace_path
        self.agent_name = agent_name
        self._hooks: dict[str, list[dict]] = {}
        self.reload()

    # --------------------------------------------------------------
    # 配置加载
    # --------------------------------------------------------------

    def _config_files(self) -> list[Path]:
        files = [Path.home() / ".cbhcli" / "hooks.json"]
        if self.workspace_path:
            files.append(Path(self.workspace_path) / "hooks.json")
        return files

    @staticmethod
    def _is_trusted(path: Path) -> bool:
        """安全检查：配置文件必须属于当前用户（防恶意仓库投毒）"""
        try:
            st = path.stat()
            return st.st_uid == os.getuid()
        except Exception:
            return False

    def reload(self):
        """重新加载钩子配置（全局 + Agent 级合并）"""
        merged: dict[str, list[dict]] = {e: [] for e in EVENTS}
        for cfg_file in self._config_files():
            if not cfg_file.exists() or not self._is_trusted(cfg_file):
                continue
            try:
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"{C_ERROR}⚠️  hooks 配置解析失败 {cfg_file}: {e}{C_RESET}")
                continue
            for event in EVENTS:
                entries = data.get(event, [])
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict) and entry.get("command"):
                            merged[event].append({
                                "matcher": entry.get("matcher", "*"),
                                "command": str(entry["command"]),
                                "source": str(cfg_file),
                            })
        self._hooks = merged

    def get_hooks(self) -> dict[str, list[dict]]:
        return {k: list(v) for k, v in self._hooks.items() if v}

    def has_hooks(self, event: str) -> bool:
        return bool(self._hooks.get(event))

    # --------------------------------------------------------------
    # 钩子执行
    # --------------------------------------------------------------

    @staticmethod
    def _matcher_ok(matcher: str, tool_name: str) -> bool:
        if not matcher or matcher == "*":
            return True
        for part in matcher.split("|"):
            part = part.strip()
            if part and (part == tool_name or fnmatch_match(tool_name, part)):
                return True
        return False

    def run(self, event: str, tool_name: str = "",
            arguments: Optional[dict] = None, result: str = "",
            session_id: str = "") -> HookDecision:
        """执行某事件匹配的所有钩子，返回汇总决策

        任何钩子异常/超时都不会中断主流程，仅记录警告。
        """
        decision = HookDecision()
        entries = self._hooks.get(event) or []
        if not entries:
            return decision

        # 需要工具匹配的事件先过滤
        if event in ("PreToolUse", "PostToolUse"):
            entries = [e for e in entries if self._matcher_ok(e["matcher"], tool_name)]
            if not entries:
                return decision

        payload = {
            "event": event,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "result": (result or "")[:4000],
            "agent": self.agent_name,
            "cwd": os.getcwd(),
            "session_id": session_id,
        }

        env = os.environ.copy()
        env.update({
            "CBHCLI_EVENT": event,
            "CBHCLI_TOOL_NAME": tool_name,
            "CBHCLI_AGENT": self.agent_name,
            "CBHCLI_SESSION_ID": session_id,
        })

        for entry in entries:
            try:
                proc = subprocess.run(
                    entry["command"],
                    shell=True,
                    input=json.dumps(payload, ensure_ascii=False),
                    capture_output=True,
                    text=True,
                    timeout=HOOK_TIMEOUT,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                decision.warnings.append(f"钩子超时({HOOK_TIMEOUT}s): {entry['command']}")
                continue
            except Exception as e:
                decision.warnings.append(f"钩子执行异常: {entry['command']}: {e}")
                continue

            if proc.returncode == 0:
                decision.add_output(proc.stdout)
            elif proc.returncode == EXIT_BLOCK and event == "PreToolUse":
                decision.blocked = True
                decision.block_reason = (proc.stderr or proc.stdout or
                                         "被 PreToolUse 钩子拦截").strip()
                return decision  # 拦截即短路
            else:
                warn = (proc.stderr or "").strip()
                if warn:
                    decision.warnings.append(f"[{event}] {warn}")

        return decision

    # --------------------------------------------------------------
    # 便捷方法
    # --------------------------------------------------------------

    def run_pre_tool_use(self, tool_name: str, arguments: dict,
                         session_id: str = "") -> HookDecision:
        return self.run("PreToolUse", tool_name, arguments, session_id=session_id)

    def run_post_tool_use(self, tool_name: str, arguments: dict,
                          result: str, session_id: str = "") -> HookDecision:
        return self.run("PostToolUse", tool_name, arguments, result,
                        session_id=session_id)

    def run_simple(self, event: str, session_id: str = "",
                   extra_args: Optional[dict] = None) -> HookDecision:
        """SessionStart/UserPromptSubmit/Stop/SubagentStop 等无工具事件"""
        return self.run(event, arguments=extra_args or {}, session_id=session_id)


def fnmatch_match(name: str, pattern: str) -> bool:
    """fnmatch 包装（matcher 支持 mcp_fs_* 这类通配）"""
    import fnmatch
    return fnmatch.fnmatch(name, pattern)
