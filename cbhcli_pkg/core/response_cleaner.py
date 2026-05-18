"""AI响应清理"""
import re
import unicodedata


class ResponseCleaner:
    """清理AI响应中的多余空白

    仅做轻量清理（多余换行），不修改实际内容。
    工具调用完全通过 OpenAI Function Calling 处理，
    不再需要从 content 中清理标签或 JSON。
    """

    _NEWLINE_PATTERN = re.compile(r'\n{3,}')

    @classmethod
    def clean(cls, text: str) -> str:
        """清理文本中的多余换行

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
        text = cls._NEWLINE_PATTERN.sub('\n\n', text)
        return text.strip()

    @classmethod
    def clean_incremental(cls, raw_buffer: str, last_output_len: int) -> str:
        """增量清理，只返回新增的干净文本

        Args:
            raw_buffer: 原始累积内容
            last_output_len: 已输出的干净文本长度

        Returns:
            新增的干净文本
        """
        clean = cls.clean(raw_buffer)
        if len(clean) > last_output_len:
            return clean[last_output_len:]
        return ""


# ===================================================================
#  Markdown 表格对齐
# ===================================================================

def _display_width(text: str) -> int:
    """计算字符串的显示宽度（中文等宽字符占2，其余占1）"""
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ('F', 'W'):
            width += 2
        else:
            width += 1
    return width


def _pad_to_width(text: str, target_width: int) -> str:
    """将文本用尾部空格填充到指定显示宽度"""
    current_width = _display_width(text)
    if current_width >= target_width:
        return text
    return text + ' ' * (target_width - current_width)


def align_markdown_tables(text: str) -> str:
    """对齐 Markdown 表格中的所有列，使竖线在等宽字体下对齐。

    遍历文本中的每个 Markdown 表格，计算每列的最大显示宽度，
    然后用尾部空格将所有单元格填充到该宽度。

    Args:
        text: 包含 Markdown 表格的文本

    Returns:
        对齐后的文本
    """
    # 匹配 markdown 表格：以 | 开头的连续行（至少3行：表头、分隔行、数据行）
    # 表格行：以 | 开头（允许前面有空白），包含至少一个 |
    table_pattern = re.compile(
        r'((?:^[ \t]*\|.+\|[ \t]*$\n?){3,})',  # 至少3行表格行
        re.MULTILINE
    )

    def align_table(match):
        block = match.group(1)
        lines = [l for l in block.split('\n') if l.strip()]

        if len(lines) < 3:
            return match.group(1)

        # 解析每行的单元格
        parsed_rows = []
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith('|'):
                return match.group(1)  # 不是表格行，原样返回

            # 去掉首尾的 |
            inner = stripped[1:]
            if inner.endswith('|'):
                inner = inner[:-1]

            cells = [c.strip() for c in inner.split('|')]

            # 检查是否是分隔行（如 ---|---|---）
            is_separator = all(re.match(r'^[-:]+$', c) for c in cells if c)

            parsed_rows.append({
                'cells': cells,
                'is_separator': is_separator,
                'original_line': line,
            })

        # 验证：至少有表头行和分隔行
        if len(parsed_rows) < 2:
            return match.group(1)

        # 检查第二行是否是分隔行
        if not parsed_rows[1]['is_separator']:
            return match.group(1)

        # 统一列数（以第一行为准）
        num_cols = len(parsed_rows[0]['cells'])
        for row in parsed_rows:
            # 补齐不足的列
            while len(row['cells']) < num_cols:
                row['cells'].append('')
            # 截断多余的列
            row['cells'] = row['cells'][:num_cols]

        # 计算每列的最大显示宽度
        col_widths = [0] * num_cols
        for row in parsed_rows:
            for i, cell in enumerate(row['cells']):
                if not row['is_separator']:
                    w = _display_width(cell)
                    col_widths[i] = max(col_widths[i], w)
                else:
                    # 分隔行也要考虑宽度（至少3个-）
                    w = max(_display_width(cell), 3)
                    col_widths[i] = max(col_widths[i], w)

        # 重新构建每行
        result_lines = []
        for row in parsed_rows:
            if row['is_separator']:
                # 分隔行：用 - 填充到对应宽度
                new_cells = []
                for i, cell in enumerate(row['cells']):
                    # 保留对齐方式（:--- 左对齐, ---: 右对齐, :---: 居中）
                    stripped = cell.strip()
                    align = 'left'
                    if stripped.startswith(':') and stripped.endswith(':'):
                        align = 'center'
                    elif stripped.endswith(':'):
                        align = 'right'

                    # 用 - 填充到目标宽度
                    target = col_widths[i]
                    dash_count = max(target, 3)
                    if align == 'center':
                        new_cell = ':' + '-' * (dash_count - 2) + ':'
                    elif align == 'right':
                        new_cell = '-' * (dash_count - 1) + ':'
                    else:
                        new_cell = '-' * dash_count
                    new_cells.append(new_cell)
            else:
                # 普通行：用空格填充到对应宽度
                new_cells = []
                for i, cell in enumerate(row['cells']):
                    new_cells.append(_pad_to_width(cell, col_widths[i]))

            result_lines.append('| ' + ' | '.join(new_cells) + ' |')

        return '\n'.join(result_lines)

    return table_pattern.sub(align_table, text)


class MarkdownTableBuffer:
    """流式输出时缓冲 Markdown 表格行，等表格结束后一次性输出对齐版本。

    工作原理：
    1. 逐字符接收 AI 流式输出
    2. 检测到表格行开始时，缓冲该行而不是立即输出
    3. 检测到表格结束时（遇到非表格行或流结束），对齐缓冲的表格行并一次性输出
    4. 非表格内容直接透传输出

    状态机：
    - IDLE: 不在表格中，直接输出
    - IN_TABLE: 在表格中，缓冲行
    """

    # 表格行正则：以 | 开头（允许前导空白），包含至少一个 |
    _TABLE_LINE_RE = re.compile(r'^[ \t]*\|.+\|[ \t]*$')
    # 分隔行正则
    _SEPARATOR_RE = re.compile(r'^[ \t]*\|[-:]+(\|[-:]+)*\|[ \t]*$')

    def __init__(self, output_func=None):
        """初始化

        Args:
            output_func: 输出函数，默认为 sys.stdout.write
        """
        import sys
        self._output_func = output_func or sys.stdout.write
        self._buffer = []           # 缓冲的表格行
        self._in_table = False      # 是否在表格中
        self._line_buffer = ""      # 当前行的缓冲（逐字符累积）
        self._table_line_count = 0  # 当前表格已缓冲的行数
        self._pending_newlines = 0  # 表格行暂存的换行符数量

    def _is_table_line(self, line: str) -> bool:
        """判断一行是否是 Markdown 表格行"""
        return bool(self._TABLE_LINE_RE.match(line))

    def _is_separator_line(self, line: str) -> bool:
        """判断一行是否是表格分隔行"""
        return bool(self._SEPARATOR_RE.match(line))

    def _flush_table(self):
        """将缓冲的表格行对齐后输出"""
        if not self._buffer:
            return

        # 将缓冲的行拼接成文本，用 align_markdown_tables 对齐
        table_text = '\n'.join(self._buffer)
        aligned_text = align_markdown_tables(table_text)

        # 输出对齐后的表格
        self._output_func(aligned_text)

        # 清空缓冲
        self._buffer = []
        self._table_line_count = 0

    def _flush_line_buffer(self):
        """将行缓冲中的内容作为非表格行输出"""
        if self._line_buffer:
            self._output_func(self._line_buffer)
            self._line_buffer = ""

    def feed(self, content: str) -> str:
        """接收流式内容，处理表格对齐后返回应累积到 ai_response 的内容。

        非表格内容直接透传输出，表格内容缓冲后对齐输出。
        返回值是经过对齐处理后的内容（用于 ai_response 累积）。

        Args:
            content: 流式接收的一个chunk

        Returns:
            经过对齐处理后的内容（用于 ai_response 累积）
        """
        result = ""

        for char in content:
            if char == '\n':
                # 行结束，判断当前行
                complete_line = self._line_buffer
                self._line_buffer = ""

                if self._is_table_line(complete_line):
                    # 是表格行
                    if not self._in_table:
                        # 刚进入表格，开始缓冲
                        self._in_table = True
                    self._buffer.append(complete_line)
                    self._table_line_count += 1
                    # 换行符暂存，等表格结束时一起输出
                    self._pending_newlines += 1
                else:
                    # 不是表格行
                    if self._in_table:
                        # 表格结束，先输出对齐后的表格
                        if self._table_line_count >= 3 and self._has_separator():
                            aligned = align_markdown_tables('\n'.join(self._buffer))
                            # 精确移除表格行暂存的换行符
                            result = result[:-self._pending_newlines] if self._pending_newlines <= len(result) and result.endswith('\n' * self._pending_newlines) else result.rstrip('\n')
                            result += aligned + '\n'
                            self._output_func(aligned + '\n')
                        else:
                            # 不是有效表格，原样输出缓冲内容
                            for line in self._buffer:
                                result += line + '\n'
                                self._output_func(line + '\n')

                        self._buffer = []
                        self._table_line_count = 0
                        self._pending_newlines = 0
                        self._in_table = False

                    # 输出非表格行
                    result += complete_line + '\n'
                    self._output_func(complete_line + '\n')
            else:
                self._line_buffer += char

        return result

    def _has_separator(self) -> bool:
        """检查缓冲的表格行中是否有分隔行"""
        for line in self._buffer:
            if self._is_separator_line(line):
                return True
        return False

    def flush(self) -> str:
        """流结束时，输出所有剩余缓冲内容。

        Returns:
            剩余的对齐后内容（用于 ai_response 累积）
        """
        result = ""

        # 先处理行缓冲中的不完整行
        if self._line_buffer:
            if self._in_table:
                self._buffer.append(self._line_buffer)
                self._table_line_count += 1
            else:
                self._output_func(self._line_buffer)
                result += self._line_buffer
            self._line_buffer = ""

        # 处理表格缓冲
        if self._buffer:
            if self._table_line_count >= 3 and self._has_separator():
                aligned = align_markdown_tables('\n'.join(self._buffer))
                self._output_func(aligned)
                result += aligned
            else:
                # 不是有效表格，原样输出
                for line in self._buffer:
                    self._output_func(line + '\n')
                    result += line + '\n'

            self._buffer = []
            self._table_line_count = 0
            self._in_table = False

        return result

    def reset(self):
        """重置缓冲区状态（用于新一轮AI响应）"""
        self._buffer = []
        self._in_table = False
        self._line_buffer = ""
        self._table_line_count = 0
        self._pending_newlines = 0