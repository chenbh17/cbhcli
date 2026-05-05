"""AI响应清理"""
import re


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
