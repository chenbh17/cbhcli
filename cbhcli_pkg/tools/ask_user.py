"""AskUserQuestion工具 - 向用户提问以澄清需求"""
import sys
from cbhcli_pkg.core.prompt_utils import ask_text
from cbhcli_pkg.tools.registry import BaseTool, ToolResult

# 内联颜色常量（避免从 core.constants 导入产生循环引用）
_C_AI_HINT = "\033[34m"
_C_AI_TEXT = "\033[37m"
_C_DIM = "\033[2m"
_C_RESET = "\033[0m"


class AskUserQuestionTool(BaseTool):
    """向用户提问工具

    当任务需求不明确时，Agent 可使用此工具向用户提问，
    提供若干选项供用户选择（支持多选），最后一个选项固定为自定义输入。
    """

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "向用户提问以澄清需求或获取决策。"
            "提供若干选项供选择，用户可以选择一个或多个选项（逗号分隔），"
            "也可以选择最后一个选项自行输入回答。"
            "适合在关键决策点、需求不明确时使用，避免猜测用户意图。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问用户的问题"
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "供用户选择的选项列表（无需包含'自定义输入'，会自动添加）"
                },
                "allow_multiple": {
                    "type": "boolean",
                    "description": "是否允许多选（用逗号分隔），默认 false"
                }
            },
            "required": ["question", "options"]
        }

    def execute(
        self,
        question: str,
        options: list,
        allow_multiple: bool = False,
        **kwargs
    ) -> ToolResult:
        """向用户展示问题和选项，等待回答"""
        if not question:
            return ToolResult(
                success=False, output="",
                error="问题内容不能为空"
            )
        if not options or len(options) < 1:
            return ToolResult(
                success=False, output="",
                error="至少需要提供1个选项"
            )

        # 构建选项列表（自动追加"自定义输入"选项）
        all_options = list(options) + ["自定义输入"]

        # 显示问题
        print(f"\n{_C_AI_HINT}{'─' * 50}")
        print(f"❓ {question}{_C_RESET}")
        if allow_multiple:
            print(f"{_C_DIM}   (可多选，用逗号分隔，如: 1,3){_C_RESET}")
        print()

        # 显示选项
        for i, opt in enumerate(all_options, 1):
            print(f"  {_C_AI_TEXT}{i}. {opt}{_C_RESET}")
        print(f"{_C_AI_HINT}{'─' * 50}{_C_RESET}")

        # 获取用户输入
        try:
            choice = ask_text(f"\n{_C_AI_HINT}请选择: {_C_RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            return ToolResult(
                success=False, output="",
                error="用户取消了回答"
            )

        if not choice:
            return ToolResult(
                success=False, output="",
                error="用户未输入选择"
            )

        # 解析用户选择
        custom_idx = len(all_options)  # "自定义输入"的编号
        selected = []
        custom_input = ""

        parts = [p.strip() for p in choice.split(',')]
        needs_custom = False

        for part in parts:
            try:
                idx = int(part)
                if idx < 1 or idx > len(all_options):
                    return ToolResult(
                        success=False, output="",
                        error=f"无效选项编号: {idx}（范围 1-{len(all_options)}）"
                    )
                if idx == custom_idx:
                    needs_custom = True
                else:
                    selected.append(all_options[idx - 1])
            except ValueError:
                # 非数字输入，视为直接文本回答
                return ToolResult(
                    success=True,
                    output=f"用户回答: {choice}"
                )

        # 如果选择了"自定义输入"，提示用户输入
        if needs_custom:
            try:
                print(f"\n{_C_AI_HINT}请输入你的回答: {_C_RESET}", end='')
                custom_input = ask_text().strip()
            except (EOFError, KeyboardInterrupt):
                return ToolResult(
                    success=False, output="",
                    error="用户取消了输入"
                )
            if custom_input:
                selected.append(f"[自定义] {custom_input}")

        if not selected:
            return ToolResult(
                success=False, output="",
                error="未选择任何有效选项"
            )

        # 构建结果
        if len(selected) == 1:
            answer = selected[0]
        else:
            answer = "、".join(selected)

        return ToolResult(
            success=True,
            output=f"用户回答: {answer}"
        )
