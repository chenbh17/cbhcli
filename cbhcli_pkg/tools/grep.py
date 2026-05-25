"""Grep工具 - 基于正则表达式搜索文件内容"""
import re
import os
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class GrepTool(BaseTool):
    """正则表达式内容搜索工具

    在指定文件或目录中搜索匹配正则表达式的内容行，
    避免读取全部文件内容，直接定位需要的信息。
    """

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "基于正则表达式搜索文件内容。"
            "在指定文件或目录中搜索匹配的行，返回文件名、行号和匹配内容。"
            "支持递归搜索目录、忽略大小写、显示上下文行等选项。"
            "适合在不读取全部文件的情况下快速定位关键内容。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "正则表达式搜索模式"
                },
                "path": {
                    "type": "string",
                    "description": "搜索路径，可以是文件或目录。目录时递归搜索。默认当前目录"
                },
                "include": {
                    "type": "string",
                    "description": "文件名过滤模式（glob格式），如 '*.py', '*.js'。可选"
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "是否忽略大小写，默认 false"
                },
                "context_lines": {
                    "type": "integer",
                    "description": "显示匹配行的前后各N行上下文，默认 0"
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数，默认 50"
                }
            },
            "required": ["pattern"]
        }

    def execute(
        self,
        pattern: str,
        path: str = ".",
        include: str = "",
        ignore_case: bool = False,
        context_lines: int = 0,
        max_results: int = 50,
        **kwargs
    ) -> ToolResult:
        """执行正则搜索"""
        try:
            flags = re.IGNORECASE if ignore_case else 0
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return ToolResult(
                    success=False, output="",
                    error=f"无效的正则表达式: {e}"
                )

            target = Path(path).expanduser().resolve()
            if not target.exists():
                return ToolResult(
                    success=False, output="",
                    error=f"路径不存在: {path}"
                )

            # 收集要搜索的文件列表
            files = []
            if target.is_file():
                files = [target]
            else:
                files = self._collect_files(target, include)

            results = []
            total_matches = 0

            for fpath in files:
                if total_matches >= max_results:
                    break
                matches = self._search_file(
                    fpath, regex, context_lines,
                    max_results - total_matches, target
                )
                if matches:
                    results.extend(matches)
                    total_matches += len(matches)

            if not results:
                return ToolResult(
                    success=True,
                    output=f"未找到匹配 '{pattern}' 的内容"
                )

            output_lines = [f"搜索模式: {pattern}"]
            output_lines.append(f"匹配数: {total_matches}" + (
                f" (已截断，上限 {max_results})" if total_matches >= max_results else ""
            ))
            output_lines.append("")
            output_lines.extend(results)

            return ToolResult(success=True, output="\n".join(output_lines))

        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"搜索出错: {str(e)}"
            )

    def _collect_files(self, directory: Path, include: str) -> list:
        """递归收集目录下的文件"""
        skip_dirs = {
            '.git', '__pycache__', 'node_modules', '.venv', 'venv',
            '.tox', '.mypy_cache', '.pytest_cache', 'dist', 'build',
            '.egg-info', '.eggs'
        }

        files = []
        for root, dirs, filenames in os.walk(directory):
            # 跳过隐藏目录和常见无关目录
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]

            for fname in filenames:
                # 跳过二进制文件的常见扩展名
                if fname.endswith(('.pyc', '.pyo', '.so', '.o', '.a',
                                   '.exe', '.dll', '.bin', '.dat',
                                   '.png', '.jpg', '.gif', '.pdf',
                                   '.zip', '.tar', '.gz', '.whl')):
                    continue

                if include:
                    if not Path(fname).match(include):
                        continue

                files.append(Path(root) / fname)

        return sorted(files)

    def _search_file(
        self, fpath: Path, regex, context_lines: int,
        remaining: int, base_dir: Path
    ) -> list:
        """在单个文件中搜索"""
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception:
            return []

        # 计算相对路径显示
        try:
            rel = fpath.relative_to(base_dir)
        except ValueError:
            rel = fpath

        matches = []
        matched_line_nums = set()

        # 找出所有匹配行
        for i, line in enumerate(lines):
            if regex.search(line):
                matched_line_nums.add(i)

        if not matched_line_nums:
            return []

        # 构建输出（包含上下文）
        output_lines = set()
        for lnum in matched_line_nums:
            start = max(0, lnum - context_lines)
            end = min(len(lines), lnum + context_lines + 1)
            for j in range(start, end):
                output_lines.add(j)

        # 按行号排序输出
        result = [f"--- {rel} ---"]
        prev_line = -2
        count = 0

        for lnum in sorted(output_lines):
            if count >= remaining:
                break
            # 间隔标记
            if lnum > prev_line + 1 and prev_line >= 0:
                result.append("  ...")
            prev_line = lnum

            line_text = lines[lnum].rstrip()
            marker = ">" if lnum in matched_line_nums else " "
            result.append(f"{marker} {lnum + 1:4d} | {line_text}")

            if lnum in matched_line_nums:
                count += 1

        return result
