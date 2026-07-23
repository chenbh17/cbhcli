"""Todo工具 - 任务计划列表管理

AI检测到复杂任务时调用此工具创建计划列表，
每完成一个步骤再调用此工具更新状态，直到全部完成。
"""
import sys
from cbhcli_pkg.tools.registry import BaseTool, ToolResult

# 内联颜色常量（避免循环引用）
_C_TOOL_GREEN = "\033[32m"
_C_DIM = "\033[2m"
_C_RESET = "\033[0m"
_C_YELLOW = "\033[33m"
_C_CYAN = "\033[36m"

# 状态图标
_STATUS_ICONS = {
    "completed": "\033[32m✓\033[0m",    # 绿色 ✓
    "in_progress": "\033[33m◐\033[0m",  # 黄色 ◐
    "pending": " ",                      # 空白
}


class TodoTool(BaseTool):
    """任务计划列表管理工具

    管理一个全局的 Todo 列表。每次调用时传入完整的列表，
    工具会保存状态、在终端格式化显示，并返回当前状态给 AI。
    """

    def __init__(self):
        self._todos: list[dict] = []

    @property
    def name(self) -> str:
        return "Todo"

    @property
    def description(self) -> str:
        return (
            "管理任务计划列表。当任务涉及多个步骤时，先调用此工具创建计划，"
            "每完成一个步骤再调用此工具更新进度。"
            "每次调用需传入完整的 todos 列表（包含所有条目及其最新状态）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "任务描述"
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "任务状态: pending(待办), in_progress(进行中), completed(已完成)"
                            }
                        },
                        "required": ["content", "status"]
                    },
                    "description": "完整的任务列表，每次调用需传入全部条目"
                }
            },
            "required": ["todos"]
        }

    def execute(self, todos: list = None, **kwargs) -> ToolResult:
        """更新并显示任务列表"""
        if todos is None:
            todos = []

        if not todos:
            return ToolResult(
                success=False, output="",
                error="todos 列表不能为空，请提供至少一个任务条目"
            )

        # 校验并规范化
        normalized = []
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                return ToolResult(
                    success=False, output="",
                    error=f"第 {i+1} 个条目格式错误，需要 {{content, status}} 对象"
                )
            content = item.get("content", "").strip()
            status = item.get("status", "pending")
            if not content:
                return ToolResult(
                    success=False, output="",
                    error=f"第 {i+1} 个条目的 content 不能为空"
                )
            if status not in ("pending", "in_progress", "completed"):
                status = "pending"
            normalized.append({"content": content, "status": status})

        # 保存状态
        self._todos = normalized

        # 统计
        total = len(normalized)
        completed = sum(1 for t in normalized if t["status"] == "completed")
        in_progress = sum(1 for t in normalized if t["status"] == "in_progress")
        pending = sum(1 for t in normalized if t["status"] == "pending")

        # 终端显示
        self._display(normalized, total)

        # 返回状态文本给 AI
        status_lines = []
        for t in normalized:
            icon = {"completed": "[✓]", "in_progress": "[◐]", "pending": "[ ]"}[t["status"]]
            status_lines.append(f"  {icon} {t['content']}")

        summary = f"Todo ({total} 项: {completed} 完成, {in_progress} 进行中, {pending} 待办)\n"
        summary += "\n".join(status_lines)

        return ToolResult(success=True, output=summary)

    def _display(self, todos: list[dict], total: int):
        """在终端格式化显示任务列表"""
        completed = sum(1 for t in todos if t["status"] == "completed")

        print(f"\n{_C_TOOL_GREEN}● Todo{_C_RESET} ({total} todos)", flush=True)

        for i, t in enumerate(todos):
            icon = _STATUS_ICONS.get(t["status"], " ")
            content = t["content"]
            prefix = "  "

            # 不同状态用不同颜色
            if t["status"] == "completed":
                print(f"{prefix}[{icon}] {_C_DIM}{content}{_C_RESET}")
            elif t["status"] == "in_progress":
                print(f"{prefix}[{icon}] {_C_YELLOW}{content}{_C_RESET}")
            else:
                print(f"{prefix}[{icon}] {content}")

        sys.stdout.flush()

    def get_todos(self) -> list[dict]:
        """获取当前 Todo 列表（供外部读取）"""
        return list(self._todos)

    def get_current_step(self) -> str:
        """获取当前 in_progress 的步骤描述，没有则返回空字符串"""
        for t in self._todos:
            if t["status"] == "in_progress":
                return t["content"]
        return ""

    def is_all_completed(self) -> bool:
        """是否全部完成"""
        return bool(self._todos) and all(
            t["status"] == "completed" for t in self._todos
        )
