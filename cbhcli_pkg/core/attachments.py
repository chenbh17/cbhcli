"""用户消息附件解析（v5.5.2：CLI 粘贴文件/图片 = Web 上传逻辑）

把用户输入里的附件引用解析为「图片 base64 + 文件引用」，与 Web 端
`/api/chat/upload` + 发送时的 `[图片: name]` / `[文件: name (path)]` 格式对齐：

- `[图片: name]`        → 在 web_uploads 里找 name，读 base64 进 Message.images
- `[文件: name (path)]` → 保留引用文本（模型可用 read/image 工具处理该路径）
- 裸文件路径（粘贴）    → 图片扩展名当图片附件，其它当文件附件

解析在 ai_handler 创建用户消息时调用：图片进 multimodal content（Message.to_dict
已支持 images），文件保留引用文本。任何读取失败都降级为纯文本，不阻断对话。
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import List, Tuple
from urllib.parse import unquote, urlparse

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff"}
_IMG_REF = re.compile(r"\[图片:\s*(.+?)\]")
_FILE_REF = re.compile(r"\[文件:\s*(.+?)\s*\((.+?)\)\]")
# 裸路径：以 / 或 ~ 或盘符开头、或 ./ ../ 开头，不含空格的连续串
_BARE_PATH = re.compile(r"(?:~[\w./\-]*|/[\w./\-]+|[A-Za-z]:\\[\w.\\\-]+|\.{1,2}/[\w./\-]+)")
# file:// URI（文件管理器复制常见格式，%20 等需解码）
_FILE_URI = re.compile(r"file://[^\s'\"<>]+")
# 引号包裹的路径（单/双引号，可含空格）——文件管理器粘贴常见格式
_QUOTED_PATH = re.compile(r"'([^'\n]+)'|\"([^\"\n]+)\"")
# 反斜杠转义空格的路径（shell 风格 /path/截图\ 2026.png）
_ESCAPED_PATH = re.compile(r"(?:~[\w./\-]*|/[\w./\-]+|\.{1,2}/[\w./\-]+)(?:\\\s[\w./\-]+)+")
# 含空格的裸路径（无引号）：以 / 或 ~ 起，惰性吃到「已知扩展名」，
# 用 (?!\s[/~]) 边界避免跨进下一条路径；扩展名限定常见类型降低误判。
_EXT_ALT = (r"(?:png|jpe?g|gif|bmp|webp|svg|ico|tiff|txt|md|pdf|csv|xlsx?|docx?|"
            r"pptx?|py|json|zip|tar|gz|log|html?|xml|ya?ml|mp4|mp3|wav)")
_SPACE_PATH = re.compile(r"(?:/|~)(?:(?!\s[/~])[\w.\-/ ])*?\." + _EXT_ALT + r"(?!\w)",
                         re.IGNORECASE)


def upload_dir() -> Path:
    return Path.home() / ".cbhcli" / "web_uploads"


def _resolve_image(name: str) -> Path | None:
    """按文件名在 web_uploads 定位图片（含 _1/_2 去重后缀的精确名优先）"""
    p = upload_dir() / name
    if p.is_file():
        return p
    # 模糊：stem 匹配（上传去重可能加了后缀）
    stem = Path(name).stem
    for cand in sorted(upload_dir().glob(f"{stem}*")):
        if cand.is_file() and cand.suffix.lower() in _IMAGE_EXTS:
            return cand
    return None


def _load_image_b64(path: Path) -> str | None:
    """读图片为**裸 base64**（不含 data URI 前缀）。

    约定：Message.images 存裸 base64（与 image 工具/Web 一致），
    由 Message.to_dict 统一加 `data:image/<mime>;base64,` 前缀。
    若此处带前缀会被 to_dict 二次包裹 → 畸形 URL → API 报
    "image format is illegal"（v5.5.2 用户实测教训）。
    """
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > 10 * 1024 * 1024:
            return None
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return None


def parse_attachments(text: str) -> Tuple[str, List[str]]:
    """解析附件，返回 (保留引用后的文本, 图片 base64 列表)。

    - `[图片: name]`：解析为 base64（失败则保留原文本）
    - `[文件: name (path)]`：保留引用文本
    - 裸图片路径：替换为 `[图片: name]` 引用 + base64
    - 裸文件路径：替换为 `[文件: name (path)]` 引用
    """
    if not text:
        return text, []
    images: List[str] = []

    # 1. 显式 [图片: name]
    def _img_sub(m):
        name = m.group(1).strip()
        p = _resolve_image(name)
        if p:
            b64 = _load_image_b64(p)
            if b64:
                images.append(b64)
                return m.group(0)
        return m.group(0)

    text = _IMG_REF.sub(_img_sub, text)

    # 统一：候选路径字符串 → 附件引用（图片进 images，文件保留引用）；非文件返回 None
    def _to_ref(candidate: str):
        try:
            p = Path(candidate).expanduser()
            if not p.is_file():
                return None
        except Exception:
            return None
        if p.suffix.lower() in _IMAGE_EXTS:
            b64 = _load_image_b64(p)
            if b64:
                images.append(b64)
                return f"[图片: {p.name}]"
            return None
        return f"[文件: {p.name} ({p})]"

    def _in_ref(m):
        # 紧邻左括号 → 位于已生成的 [文件: name (path)] 内，跳过避免重复处理
        return text[:m.start()].rstrip().endswith("(")

    # 2. file:// URI（解码 %20 等）
    def _uri_sub(m):
        if _in_ref(m):
            return m.group(0)
        try:
            path = unquote(urlparse(m.group(0)).path)
        except Exception:
            return m.group(0)
        return _to_ref(path) or m.group(0)

    text = _FILE_URI.sub(_uri_sub, text)

    # 3. 引号包裹的路径（含空格）
    def _quoted_sub(m):
        if _in_ref(m):
            return m.group(0)
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        return _to_ref(inner) or m.group(0)

    text = _QUOTED_PATH.sub(_quoted_sub, text)

    # 4. 反斜杠转义空格的路径
    def _escaped_sub(m):
        if _in_ref(m):
            return m.group(0)
        return _to_ref(m.group(0).replace("\\ ", " ")) or m.group(0)

    text = _ESCAPED_PATH.sub(_escaped_sub, text)

    # 5. 含空格裸路径（无引号，按已知扩展名+路径边界识别）
    def _space_sub(m):
        if _in_ref(m):
            return m.group(0)
        return _to_ref(m.group(0)) or m.group(0)

    text = _SPACE_PATH.sub(_space_sub, text)

    # 6. 裸路径（无空格；跳过 [文件: name (path)] 括号里的路径）
    def _bare_sub(m):
        if _in_ref(m):
            return m.group(0)
        return _to_ref(m.group(0)) or m.group(0)

    text = _BARE_PATH.sub(_bare_sub, text)
    return text, images
