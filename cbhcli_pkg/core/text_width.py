"""终端文本显示宽度计算 — 字素簇感知

解决问题：
prompt_toolkit 的 get_cwidth() 及常规的逐字符 wcwidth 求和，
对 ZWJ 序列（👨‍💻）、VS16 emoji（❤️）、旗帜（🇨🇳）、肤色修饰（👍🏻）
等字素簇计算错误（终端实际渲染均为 2 列），导致：
- 输入框退格重叠/错位（prompt_toolkit 布局）
- edit 工具预览表格对不齐（自绘表格）

本模块提供统一的字素簇感知宽度函数，并可将 prompt_toolkit 的
全局宽度缓存替换为字素簇感知版本（一次替换全局生效）。
"""
from __future__ import annotations

from wcwidth import wcwidth


def _is_regional_indicator(cp: int) -> bool:
    """区域指示符（旗帜emoji组成字符）U+1F1E6 ~ U+1F1FF"""
    return 0x1F1E6 <= cp <= 0x1F1FF


def _is_combining(cp: int) -> bool:
    """组合附加符号（附着在基字符上，不占宽度）"""
    return (
        0x0300 <= cp <= 0x036F      # Combining Diacritical Marks
        or 0x1AB0 <= cp <= 0x1AFF   # Combining Diacritical Marks Extended
        or 0x1DC0 <= cp <= 0x1DFF   # Combining Diacritical Marks Supplement
        or 0x20D0 <= cp <= 0x20FF   # Combining Diacritical Marks for Symbols
        or 0xFE20 <= cp <= 0xFE2F   # Combining Half Marks
    )


def grapheme_clusters(text: str) -> list[str]:
    """将字符串切分为字素簇（用户感知的最小字符单位）

    处理规则：
    - ZWJ (U+200D) 及其后一个字符并入当前簇（👨‍💻 = 一簇）
    - 变体选择符 U+FE00~FE0F 并入（❤️ = 一簇）
    - 肤色修饰 U+1F3FB~1F3FF 并入（👍🏻 = 一簇）
    - 组合附加符号并入（é = e + U+0301 一簇）
    - 连续两个区域指示符合并为一面旗帜（🇨🇳 = 一簇）
    """
    clusters: list[str] = []
    cluster = ""
    for ch in text:
        cp = ord(ch)
        if not cluster:
            cluster = ch
            continue
        prev_cp = ord(cluster[-1])
        join = (
            cp == 0x200D                       # ZWJ 本身并入
            or prev_cp == 0x200D               # ZWJ 后的字符并入
            or 0xFE00 <= cp <= 0xFE0F          # 变体选择符
            or 0x1F3FB <= cp <= 0x1F3FF        # 肤色修饰符
            or _is_combining(cp)               # 组合附加符号
            or (_is_regional_indicator(cp)     # 旗帜对：两个区域指示符
                and _is_regional_indicator(ord(cluster[0]))
                and len(cluster) == 1)
        )
        if join:
            cluster += ch
        else:
            clusters.append(cluster)
            cluster = ch
    if cluster:
        clusters.append(cluster)
    return clusters


def display_width(text: str) -> int:
    """计算字符串的终端显示宽度（字素簇感知）

    - 单字符：直接 wcwidth
    - 含 VS16 (U+FE0F) 的簇：强制 emoji 呈现 = 2 列
    - 含区域指示符的簇：旗帜 = 2 列
    - 其他多码点簇（ZWJ/肤色/组合符）：取簇内最大单字符宽度（通常=2）
    - tab 按 4 列对齐（避免表格错位）
    """
    total = 0
    for cluster in grapheme_clusters(text):
        if len(cluster) == 1:
            if cluster == '\t':
                total = (total + 4) & ~3
                continue
            total += max(0, wcwidth(cluster))
        elif '\ufe0f' in cluster:
            total += 2
        elif any(_is_regional_indicator(ord(c)) for c in cluster):
            total += 2
        else:
            total += max(max(0, wcwidth(c)) for c in cluster)
    return total


def pad_to_width(text: str, target_width: int, fill: str = ' ') -> str:
    """将文本按显示宽度填充到目标宽度（尾部填充）"""
    current = display_width(text)
    if current >= target_width:
        return text
    return text + fill * (target_width - current)


def truncate_to_width(text: str, max_width: int, ellipsis: str = "...") -> str:
    """按显示宽度截断文本，超宽时追加省略号（省略号计入总宽度）"""
    if display_width(text) <= max_width:
        return text
    keep = max_width - len(ellipsis)
    if keep <= 0:
        return ellipsis[:max_width]
    result = ""
    width = 0
    for cluster in grapheme_clusters(text):
        cw = display_width(cluster)
        if width + cw > keep:
            break
        result += cluster
        width += cw
    return result + ellipsis


def install_prompt_toolkit_patch():
    """替换 prompt_toolkit 全局字符宽度缓存为字素簇感知版本

    prompt_toolkit.utils.get_cwidth() 内部查询模块级全局 _CHAR_SIZES_CACHE，
    所有布局/光标/补全菜单宽度计算都经过它。替换该缓存实例后全局生效，
    无需修改 prompt_toolkit 本身。
    """
    import prompt_toolkit.utils as _ptu

    if getattr(_ptu, '_cbhcli_grapheme_patched', False):
        return

    class _ClusterAwareCache(_ptu._CharSizesCache):
        """字素簇感知的宽度缓存（保持与原版相同的长字符串轮换策略）"""

        def __missing__(self, string: str) -> int:
            if len(string) == 1:
                result = max(0, wcwidth(string))
            else:
                result = display_width(string)

            self[string] = result

            # 长字符串缓存轮换（与原版一致）
            if len(string) > self.LONG_STRING_MIN_LEN:
                long_strings = self._long_strings
                long_strings.append(string)
                if len(long_strings) > self.MAX_LONG_STRINGS:
                    key_to_remove = long_strings.popleft()
                    if key_to_remove in self:
                        del self[key_to_remove]
            return result

    _ptu._CHAR_SIZES_CACHE = _ClusterAwareCache()
    _ptu._cbhcli_grapheme_patched = True
