"""Glob工具 - 按模式匹配搜索文件"""
import os
import fnmatch
import re
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


def _expand_braces(pattern: str) -> list:
    """递归展开 brace expansion，如 {py,json} → ['py', 'json']

    支持嵌套：a{b,c{d,e}}f → ['abf', 'acdf', 'acef']
    不含花括号时返回单元素列表。
    """
    # 匹配最内层花括号（不含嵌套花括号）
    match = re.search(r'\{([^{}]+)\}', pattern)
    if not match:
        return [pattern]

    options = match.group(1).split(',')
    prefix = pattern[:match.start()]
    suffix = pattern[match.end():]

    results = []
    seen = set()
    for opt in options:
        new_pattern = prefix + opt.strip() + suffix
        for r in _expand_braces(new_pattern):
            if r not in seen:
                seen.add(r)
                results.append(r)
    return results


class GlobTool(BaseTool):
    """文件模式匹配搜索工具

    按 glob 模式（如 **/*.py）递归搜索文件，
    返回匹配的文件路径列表。
    """

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "按模式匹配搜索文件路径。"
            "支持 glob 语法如 '**/*.py'、'src/**/*.ts'、'*.md' 等。"
            "返回匹配的文件路径列表，按修改时间排序。"
            "适合在不知道确切文件名时快速定位文件。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob 模式，如 '**/*.py', 'src/**/*.ts', '*.md'"
                },
                "path": {
                    "type": "string",
                    "description": "搜索起始目录，默认当前目录"
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数，默认 100"
                }
            },
            "required": ["pattern"]
        }

    def execute(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 100,
        **kwargs
    ) -> ToolResult:
        """执行 glob 文件搜索"""
        try:
            base = Path(path).expanduser().resolve()
            if not base.exists():
                return ToolResult(
                    success=False, output="",
                    error=f"目录不存在: {path}"
                )
            if not base.is_dir():
                return ToolResult(
                    success=False, output="",
                    error=f"不是目录: {path}"
                )

            skip_dirs = {
                '.git', '__pycache__', 'node_modules', '.venv', 'venv',
                '.tox', '.mypy_cache', '.pytest_cache', 'dist', 'build',
                '.egg-info', '.eggs'
            }

            # 展开 brace expansion（如 **/*.{py,json} → **/*.py, **/*.json）
            patterns = _expand_braces(pattern)

            # 使用 Path.glob (支持 **)
            matched = []
            seen = set()  # 去重（多个展开模式可能匹配同一文件）
            for pat in patterns:
                try:
                    for p in base.glob(pat):
                        if p.is_file():
                            # 跳过在排除目录中的文件
                            parts = p.relative_to(base).parts
                            if any(part in skip_dirs for part in parts):
                                continue
                            resolved = p.resolve()
                            if resolved not in seen:
                                seen.add(resolved)
                                matched.append(p)
                except Exception as e:
                    return ToolResult(
                        success=False, output="",
                        error=f"Glob 模式错误 ({pat}): {e}"
                    )

            if not matched:
                return ToolResult(
                    success=True,
                    output=f"未找到匹配 '{pattern}' 的文件"
                )

            # 按修改时间降序排序（最近修改的在前）
            matched.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            total = len(matched)
            truncated = total > max_results
            matched = matched[:max_results]

            output_lines = [f"搜索模式: {pattern}"]
            output_lines.append(f"匹配文件数: {total}" + (
                f" (显示前 {max_results} 个)" if truncated else ""
            ))
            output_lines.append("")

            for p in matched:
                try:
                    rel = p.relative_to(base)
                except ValueError:
                    rel = p
                # 显示文件大小
                size = p.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size / 1024 / 1024:.1f}MB"

                output_lines.append(f"  {rel}  ({size_str})")

            return ToolResult(success=True, output="\n".join(output_lines))

        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"搜索出错: {str(e)}"
            )
