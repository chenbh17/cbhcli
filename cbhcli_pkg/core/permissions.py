"""权限规则引擎 — Harness 治理层

四档权限模板（Shift+Tab 循环切换，/mode 命令选择）：
  L0 readonly  只读模式   —— 一切修改操作直接拒绝（连 ask 都没有），AI 只能看
  L1 standard  标准模式   —— 完整规则集：deny 红线 + ask 人审 + allow 免打扰（默认）
  L2 auto      自动模式   —— allow 面扩大（cwd 内写/常见开发命令），deny 红线仍生效
  L3 yolo      最高权限   —— 全部直接执行零确认；deny 降级为红色警告（留痕不拦截）
                            配置 yolo_keep_deny=true 可强制 L3 下 deny 仍硬阻断

规则语法（借鉴 Claude Code permissions）：
  "read(*)"                        整个工具
  "read"                           简写，等价于 "read(*)"
  "terminal(git status:*)"         terminal 命令前缀匹配（:* 结尾表示前缀）
  "terminal(git push --force:*)"   同上
  "edit(/project/**)"              文件路径 fnmatch 匹配（* 与 ** 均可跨目录）
  "python(*)"                      python 工具仅支持 (*)

判定优先级：deny > ask > allow > 工具默认值
  - 只读工具（read/grep/glob/Todo/memory_search/knowledge_base/ask_user/image/process）
    默认 allow
  - 其余工具默认 ask
  - L3 yolo 模式下一切规则短路（直接执行），命中 deny 时降级为 warn

配置文件：~/.cbhcli/permissions.json（全局，所有 Agent 共享）
  {
    "default_mode": "standard",
    "yolo_keep_deny": false,
    "rules": {
      "allow": ["terminal(pytest:*)"],
      "ask":   ["terminal(git push:*)"],
      "deny":  ["terminal(rm -rf /:*)"]
    }
  }
  用户规则作为增量叠加到 L1/L2 模板上（L0 锁定不受用户规则影响，L3 短路）。
"""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Optional


# =============================================================================
# 常量
# =============================================================================

MODES = ["readonly", "standard", "auto", "yolo"]

MODE_META = {
    "readonly": {"label": "只读", "icon": "🔒", "color": "blue",
                 "desc": "只读模式：AI 只能查看/分析，一切修改操作被禁止"},
    "standard": {"label": "标准", "icon": "🟢", "color": "green",
                 "desc": "标准模式：危险操作逐个确认，红线规则硬阻断"},
    "auto":     {"label": "自动", "icon": "🟡", "color": "yellow",
                 "desc": "自动模式：工作区内写操作/开发命令自动放行，红线仍生效"},
    "yolo":     {"label": "YOLO", "icon": "🔴", "color": "red",
                 "desc": "最高权限：全部操作零确认直接执行，deny 降级为警告"},
}

# 动作
ALLOW = "allow"
ASK = "ask"
DENY = "deny"
WARN = "warn"      # 仅 yolo 模式命中 deny 时返回（放行但红色警告留痕）

# 只读工具（任何模式下默认 allow，readonly 模式下也可用的工具集）
READONLY_TOOLS = {
    "read", "grep", "glob", "Todo", "ask_user",
    "memory_search", "knowledge_base", "image", "process",
}

# readonly 模式下 terminal 允许的只读命令前缀
READONLY_COMMANDS = (
    "ls", "pwd", "cat", "echo", "head", "tail", "find", "grep", "wc",
    "tree", "which", "ps", "df", "du", "whoami", "uname", "date",
    "file", "stat", "less", "more", "sort", "uniq", "diff", "env",
    "git status", "git diff", "git log", "git show", "git branch",
    "git remote", "git tag", "git blame", "git stash list",
    "python --version", "python3 --version", "pip list", "pip show",
)

# =============================================================================
# 内置模板规则（readonly 由特殊逻辑处理，不在此表）
# =============================================================================

# deny 红线 —— 灾难性操作物理禁止（standard/auto 硬阻断，yolo 降级警告）
_BUILTIN_DENY = [
    r"terminal(rm -rf /:*)",
    r"terminal(rm -rf /*)",
    r"terminal(rm -rf ~:*)",
    r"terminal(rm -rf ~/*)",
    r"terminal(sudo rm -rf /:*)",
    "terminal(dd *of=/dev/*)",
    "terminal(mkfs:*)",
    "terminal(shutdown:*)",
    "terminal(reboot:*)",
    "terminal(:(){ :|:& };:*)",          # fork 炸弹
    "terminal(chmod -R 777 /:*)",
    "terminal(*> /dev/sd*:*)",
    "write(**/.env*)",
    "edit(**/.env*)",
    "write(**/.git/**)",
    "edit(**/.git/**)",
    "write(**/id_rsa*)",
    "edit(**/id_rsa*)",
    "write(**/*.pem)",
    "edit(**/*.pem)",
]

# ask —— 不可逆 / 影响外部世界，必须人看一眼
_BUILTIN_ASK = [
    "terminal(git push:*)",
    "terminal(git reset --hard:*)",
    "terminal(git clean:*)",
    "terminal(rm:*)",
    "terminal(sudo:*)",
    "terminal(pip uninstall:*)",
    "terminal(pip3 uninstall:*)",
    "terminal(curl:*)",
    "terminal(wget:*)",
    "terminal(ssh:*)",
    "terminal(scp:*)",
    "terminal(kill:*)",
    "terminal(pkill:*)",
    "terminal(chmod:*)",
    "terminal(chown:*)",
    "kill_process(*)",
    "skills_create(*)",
]

# allow —— 纯只读命令，standard/auto 都免确认
_BUILTIN_ALLOW = [
    "terminal(git status:*)",
    "terminal(git diff:*)",
    "terminal(git log:*)",
    "terminal(git show:*)",
    "terminal(git branch:*)",
    "terminal(ls:*)",
    "terminal(pwd)",
    "terminal(cat:*)",
    "terminal(echo:*)",
    "terminal(head:*)",
    "terminal(tail:*)",
    "terminal(find:*)",
    "terminal(grep:*)",
    "terminal(wc:*)",
    "terminal(tree:*)",
    "terminal(which:*)",
    "terminal(ps:*)",
    "terminal(df:*)",
    "terminal(du:*)",
    "terminal(whoami)",
    "terminal(date)",
    "terminal(python --version)",
    "terminal(python3 --version)",
    "terminal(pip list:*)",
]

# auto 模式额外放行（在 standard 基础上）
_AUTO_EXTRA_ALLOW = [
    "terminal(git add:*)",
    "terminal(git commit:*)",
    "terminal(git checkout:*)",
    "terminal(git switch:*)",
    "terminal(git stash:*)",
    "terminal(git pull:*)",
    "terminal(git fetch:*)",
    "terminal(pytest:*)",
    "terminal(python:*)",
    "terminal(python3:*)",
    "terminal(pip install:*)",
    "terminal(pip3 install:*)",
    "terminal(make:*)",
    "terminal(npm:*)",
    "terminal(npx:*)",
    "terminal(node:*)",
    "terminal(cargo:*)",
    "terminal(go:*)",
    "terminal(touch:*)",
    "terminal(mkdir:*)",
    "terminal(cp:*)",
    "terminal(mv:*)",
    "terminal(cd:*)",
    "terminal(export:*)",
    "terminal(source:*)",
    "terminal(black:*)",
    "terminal(ruff:*)",
    "terminal(flake8:*)",
    "terminal(mypy:*)",
    "python(*)",
]

_CONFIG_FILE = Path.home() / ".cbhcli" / "permissions.json"


def build_mode_note(mode: str) -> str:
    """生成注入系统提示的权限模式说明（CLI app 与 Web server 共用）"""
    if mode == "readonly":
        return (
            "\n\n## 当前权限模式: 只读 (readonly)\n"
            "用户已启用只读模式：write/edit/python 及 terminal 写命令等修改"
            "操作将被系统直接拒绝。请只做分析、阅读和回答；如确需修改，"
            "向用户说明原因并请其切换权限模式（Shift+Tab 或 /mode standard）。"
        )
    if mode == "auto":
        return (
            "\n\n## 当前权限模式: 自动 (auto)\n"
            "工作目录内的文件修改和常见开发命令将自动放行；危险操作仍需"
            "用户确认；红线操作（rm -rf /、写入 .env 等）被系统禁止。"
        )
    if mode == "yolo":
        return (
            "\n\n## 当前权限模式: YOLO (最高权限)\n"
            "全部工具调用将不经确认直接执行（红线规则仅警告）。"
            "请仍保持谨慎，尽量避免不可逆操作。"
        )
    return ""  # standard：无需说明


# =============================================================================
# 规则解析与匹配
# =============================================================================

_RULE_RE = re.compile(r"^([A-Za-z_][\w]*)(?:\((.*)\))?$")


def parse_rule(rule: str) -> tuple[str, str]:
    """解析规则字符串 → (tool_name, pattern)，无括号时 pattern 为 '*'"""
    rule = rule.strip()
    m = _RULE_RE.match(rule)
    if not m:
        return rule, "*"
    tool = m.group(1)
    pattern = m.group(2) if m.group(2) is not None else "*"
    return tool, pattern


def _command_of(arguments: dict) -> str:
    return str(arguments.get("command", "") or "").strip()


def _path_of(arguments: dict) -> str:
    p = str(arguments.get("file_path", "") or arguments.get("path", "") or "")
    if not p:
        return ""
    try:
        return str(Path(p).expanduser().resolve())
    except Exception:
        return str(Path(p).expanduser())


def match_rule(rule: str, tool_name: str, arguments: dict) -> bool:
    """判断单条规则是否命中本次调用"""
    r_tool, pattern = parse_rule(rule)

    # MCP 工具前缀匹配：mcp_* 规则可匹配所有 MCP 工具
    if r_tool == "mcp_" and tool_name.startswith("mcp_"):
        pass
    elif r_tool != tool_name:
        return False

    if pattern in ("*", ""):
        return True

    if tool_name == "terminal":
        cmd = _command_of(arguments)
        if not cmd:
            return False
        if pattern.endswith(":*"):
            # 前缀匹配：git status:* 匹配 "git status" 及 "git status xxx"
            prefix = pattern[:-2]
            return cmd == prefix or cmd.startswith(prefix + " ")
        # 含通配符 → fnmatch；否则精确匹配
        if any(c in pattern for c in "*?["):
            return fnmatch.fnmatch(cmd, pattern)
        return cmd == pattern

    if tool_name in ("read", "write", "edit"):
        path = _path_of(arguments)
        if not path:
            return False
        # 支持 ~ 开头与相对路径模式：统一展开后 fnmatch
        pat = str(Path(pattern).expanduser()) if pattern.startswith("~") else pattern
        return fnmatch.fnmatch(path, pat)

    if tool_name == "python":
        code = str(arguments.get("code", "") or "")
        return fnmatch.fnmatch(code, pattern)

    # 其他工具：仅支持 (*) 通配
    return False


# =============================================================================
# 权限引擎
# =============================================================================

class PermissionEngine:
    """权限规则引擎：按当前模式 + 规则集判定工具调用的处置动作"""

    def __init__(self, config_file: Optional[Path] = None):
        self.config_file = config_file or _CONFIG_FILE
        cfg = self._load_config()
        self.yolo_keep_deny: bool = bool(cfg.get("yolo_keep_deny", False))
        self._user_rules: dict[str, list[str]] = {
            "allow": list(cfg.get("rules", {}).get("allow", [])),
            "ask":   list(cfg.get("rules", {}).get("ask", [])),
            "deny":  list(cfg.get("rules", {}).get("deny", [])),
        }
        default_mode = cfg.get("default_mode", "standard")
        self.mode: str = default_mode if default_mode in MODES else "standard"
        self._default_mode: str = self.mode

    # --------------------------------------------------------------
    # 配置读写
    # --------------------------------------------------------------

    def _load_config(self) -> dict:
        try:
            if self.config_file.exists():
                return json.loads(self.config_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_config(self):
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            data = self._load_config()
            data["rules"] = self._user_rules
            data["yolo_keep_deny"] = self.yolo_keep_deny
            data["default_mode"] = self._default_mode
            self.config_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    # --------------------------------------------------------------
    # 模式管理
    # --------------------------------------------------------------

    def set_mode(self, mode: str):
        if mode in MODES:
            self.mode = mode

    def cycle_mode(self) -> str:
        """循环切换到下一个模式，返回新模式"""
        idx = (MODES.index(self.mode) + 1) % len(MODES)
        self.mode = MODES[idx]
        return self.mode

    def reset_to_default(self):
        """会话重置时回落到默认模式"""
        self.mode = self._default_mode

    def set_default_mode(self, mode: str):
        if mode in MODES:
            self._default_mode = mode
            self._save_config()

    def mode_meta(self) -> dict:
        return MODE_META[self.mode]

    # --------------------------------------------------------------
    # 用户规则管理
    # --------------------------------------------------------------

    def add_rule(self, category: str, rule: str) -> bool:
        if category not in self._user_rules:
            return False
        if rule not in self._user_rules[category]:
            self._user_rules[category].append(rule)
            self._save_config()
        return True

    def remove_rule(self, category: str, rule: str) -> bool:
        if category in self._user_rules and rule in self._user_rules[category]:
            self._user_rules[category].remove(rule)
            self._save_config()
            return True
        return False

    def get_user_rules(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._user_rules.items()}

    # --------------------------------------------------------------
    # 核心判定
    # --------------------------------------------------------------

    def _rules_for_mode(self) -> tuple[list[str], list[str], list[str]]:
        """返回当前模式生效的 (deny, ask, allow) 规则列表"""
        if self.mode == "auto":
            deny = _BUILTIN_DENY + self._user_rules["deny"]
            ask = _BUILTIN_ASK + self._user_rules["ask"]
            allow = (_BUILTIN_ALLOW + _AUTO_EXTRA_ALLOW + self._user_rules["allow"])
            return deny, ask, allow
        # standard（readonly/yolo 不走这里）
        deny = _BUILTIN_DENY + self._user_rules["deny"]
        ask = _BUILTIN_ASK + self._user_rules["ask"]
        allow = _BUILTIN_ALLOW + self._user_rules["allow"]
        return deny, ask, allow

    def check(self, tool_name: str, arguments: dict) -> tuple[str, Optional[str]]:
        """判定工具调用处置动作

        Returns:
            (action, matched_rule)
            action: ALLOW / ASK / DENY / WARN
            matched_rule: 命中的规则原文（无命中为 None）
        """
        # ---- L3 yolo：一切短路直接执行，deny 降级警告 ----
        if self.mode == "yolo":
            if not self.yolo_keep_deny:
                for rule in _BUILTIN_DENY + self._user_rules["deny"]:
                    if match_rule(rule, tool_name, arguments):
                        return WARN, rule
                return ALLOW, None
            # yolo_keep_deny：deny 仍硬阻断，其余放行
            for rule in _BUILTIN_DENY + self._user_rules["deny"]:
                if match_rule(rule, tool_name, arguments):
                    return DENY, rule
            return ALLOW, None

        # ---- L0 readonly：只允许只读工具与只读命令 ----
        if self.mode == "readonly":
            if tool_name in READONLY_TOOLS:
                return ALLOW, None
            if tool_name == "terminal":
                cmd = _command_of(arguments)
                for prefix in READONLY_COMMANDS:
                    if cmd == prefix or cmd.startswith(prefix + " "):
                        return ALLOW, None
                return DENY, "readonly: 只读模式禁止非白名单命令"
            return DENY, f"readonly: 只读模式禁止 {tool_name} 工具"

        # ---- L1/L2：deny > ask > allow > 默认 ----
        deny_rules, ask_rules, allow_rules = self._rules_for_mode()

        for rule in deny_rules:
            if match_rule(rule, tool_name, arguments):
                return DENY, rule
        for rule in ask_rules:
            if match_rule(rule, tool_name, arguments):
                return ASK, rule
        for rule in allow_rules:
            if match_rule(rule, tool_name, arguments):
                return ALLOW, rule

        # 默认：只读工具放行，其余人工确认
        if tool_name in READONLY_TOOLS:
            return ALLOW, None
        return ASK, None

    # --------------------------------------------------------------
    # always 规则的自动提炼（确认框选 always 时调用）
    # --------------------------------------------------------------

    @staticmethod
    def suggest_allow_rule(tool_name: str, arguments: dict) -> str:
        """根据本次调用提炼一条 allow 规则

        terminal: 取命令前两个 token 作为前缀（git commit -m → git commit:*）
        write/edit/read: 取文件父目录 /** 
        其他: 工具级 (*)
        """
        if tool_name == "terminal":
            cmd = _command_of(arguments)
            tokens = cmd.split()
            if len(tokens) >= 2:
                return f"terminal({' '.join(tokens[:2])}:*)"
            if tokens:
                return f"terminal({tokens[0]}:*)"
            return "terminal(*)"
        if tool_name in ("write", "edit", "read"):
            path = _path_of(arguments)
            if path:
                parent = str(Path(path).parent)
                return f"{tool_name}({parent}/**)"
            return f"{tool_name}(*)"
        return f"{tool_name}(*)"
