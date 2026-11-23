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

#: emoji 类字素簇的显示宽度（**终端自适应**，v5.5.2 修复）。
#: 默认 2（多数终端：VTE/kitty/WezTerm/Windows Terminal/iTerm2）。
#: 启动时 `probe_emoji_width()` 用 CPR 实测终端对 🔧 的实际渲染列数：
#: xterm.js（JupyterLab/VS Code 终端）渲染 emoji 为 **1 列** → 置 1。
#: 为什么必须自适应：prompt_toolkit 差分重绘按"模型宽度"推进光标，
#: 若模型=2 而终端实际=1，行内光标跟踪逐步失配 → 该行后续所有单元格
#: （含右侧滚动条）写到错误列 → 滚动条多重残影/双轨道/文字尾巴错乱。
_EMOJI_WIDTH = 2


def _is_emoji_cp(cp: int) -> bool:
    """emoji 呈现类码点（这些簇的宽度随终端而变，需自适应）"""
    return (
        0x1F000 <= cp <= 0x1FAFF      # 表情/符号/补充（🔧✅📦💭👍…）
        or 0x2600 <= cp <= 0x27BF     # 杂项符号/装饰符号（☀✅➡…）
        or 0x2B00 <= cp <= 0x2BFF     # 杂项符号与箭头（⭐…）
        or 0x2300 <= cp <= 0x23FF     # 杂项技术（⌚⏰⏺…）
        or 0xFE00 <= cp <= 0xFE0F     # 变体选择符
    )


def set_emoji_width(width: int) -> None:
    """设置 emoji 类宽度并清空 prompt_toolkit 宽度缓存（使其立即生效）"""
    global _EMOJI_WIDTH
    width = 1 if int(width) <= 1 else 2
    if width == _EMOJI_WIDTH:
        return
    _EMOJI_WIDTH = width
    try:
        import prompt_toolkit.utils as _ptu
        cache = getattr(_ptu, "_CHAR_SIZES_CACHE", None)
        if cache is not None:
            cache.clear()
    except Exception:
        pass


def _cp_width_narrow(cp: int) -> int:
    """mode1（xterm.js 类终端）单个码点的渲染宽度。

    xterm.js **不做字素簇合并**，按码点逐个渲染求和：emoji/肤色/区域指示符
    各占 1 列，ZWJ/变体选择符/组合符占 0 列。实测（CDP CPR）：
    🔧=1、✅=1、❤️(VS16)=1、👨‍💻(ZWJ)=2、👍🏻(肤色)=2、🇨🇳(旗帜)=2、
    👨‍👩‍👧‍👦(家庭)=4、1️⃣(keycap)=1、🏳️‍🌈=2。
    """
    if cp == 0x200D or 0xFE00 <= cp <= 0xFE0F or _is_combining(cp):
        return 0
    if _is_emoji_cp(cp):
        return 1
    return max(0, wcwidth(chr(cp)))


def _cluster_width(cluster: str) -> int:
    """单个字素簇的显示宽度（emoji 类按 _EMOJI_WIDTH 自适应）"""
    if _EMOJI_WIDTH == 1:
        # xterm.js 类终端：逐码点求和（不合并字素簇），见 _cp_width_narrow
        return sum(_cp_width_narrow(ord(c)) for c in cluster)
    if len(cluster) == 1:
        w = max(0, wcwidth(cluster))
        if w == 2 and _is_emoji_cp(ord(cluster)):
            return _EMOJI_WIDTH
        return w
    if '️' in cluster:
        return _EMOJI_WIDTH
    if any(_is_regional_indicator(ord(c)) for c in cluster):
        return 2   # 旗帜：多数终端渲染 2 列，保持
    w = max(max(0, wcwidth(c)) for c in cluster)
    if w == 2 and any(_is_emoji_cp(ord(c)) for c in cluster):
        return _EMOJI_WIDTH
    return w


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
        if len(cluster) == 1 and cluster == '\t':
            total = (total + 4) & ~3
            continue
        total += _cluster_width(cluster)
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


def render_width(margin: int = 1) -> int:
    """CLI 输出渲染可用宽度（显示列）

    v5.5.1：全屏 TUI 主区文字宽度 = 终端列数 − 2（最右 2 列预留，防止宽字符
    压到最后一列触发终端自动换行/滚屏）。**所有 rich 渲染**（Markdown 正文、
    代码预览、子Agent状态板、欢迎面板、思考窗）都必须按这个宽度出图，
    否则多出的 1~2 列会被主区折行折到下一行最左侧 —— 表现为"文字输出错行"。

    Args:
        margin: 额外预留列数（rich 用 cell_len、主区用字素簇 display_width，
            边界判定可能有细微差异，默认再留 1 列）
    """
    margin = max(0, int(margin))
    try:
        from cbhcli_pkg.core.ui.shell import get_active_ui
        ui = get_active_ui()
        if ui is not None:
            return max(20, ui.text_width() - margin)
    except Exception:
        pass
    try:
        import os
        return max(20, os.get_terminal_size().columns - margin)
    except Exception:
        return max(20, 79)


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
            # 统一走 display_width（含 emoji 终端自适应宽度，v5.5.2）
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


def _read_cpr(fd: int, timeout: float):
    """读一次 CPR 响应 `\\x1b[{row};{col}R`，返回 (row, col) 或 None"""
    import select
    import re
    buf = b""
    deadline = __import__("time").monotonic() + timeout
    while True:
        remain = deadline - __import__("time").monotonic()
        if remain <= 0:
            return None
        r, _, _ = select.select([fd], [], [], remain)
        if not r:
            return None
        try:
            chunk = __import__("os").read(fd, 64)
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
        m = re.search(rb"\x1b\[(\d+);(\d+)R", buf)
        if m:
            return int(m.group(1)), int(m.group(2))
        if len(buf) > 256:
            return None


def _width_debug(msg: str) -> None:
    """CBHCLI_WIDTH_DEBUG=1 时把探测过程写日志（排查宽度失配用）"""
    import os
    if not os.environ.get("CBHCLI_WIDTH_DEBUG"):
        return
    try:
        import time
        with open("/tmp/cbhcli_width_probe.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _query_cpr(fd: int, out, timeout: float):
    out.write("\x1b[6n"); out.flush()
    return _read_cpr(fd, timeout)


def _drain_cpr(fd: int, silence: float = 0.1) -> None:
    """读尽 stdin 直到静默，排空**在途的陈旧 CPR 响应**。

    ⚠️ v5.5.3 修复根因：启动时 readline/前序程序可能已发出光标查询，其响应
    仍在 pty/websocket 管道里（tcflush 只清内核缓冲，清不掉在途字节）。旧实现
    把第一个读到的 CPR 当基线——若那是陈旧响应，base/after 同为旧位置 →
    delta=0 → 探测失败保持默认 2 → xterm.js 下 emoji 宽度失配 → 滚动条残影。

    必须"读到静默"而非"发查询凑对"：后者自己发的查询响应仍会留在管道里，
    污染后续 after 读数（实测连续探测时好时坏）。读尽后再单查询单读，收发平衡。
    """
    import os
    import select
    import time
    deadline = time.monotonic() + silence
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0:
            return
        r, _, _ = select.select([fd], [], [], remain)
        if not r:
            return
        try:
            if not os.read(fd, 4096):
                return
        except OSError:
            return
        deadline = time.monotonic() + silence  # 有数据就续窗，直到静默


def _measure_emoji(fd: int, out, timeout: float):
    """在当前光标处写 🔧 测宽，返回 1/2 或 None（失败/换行）。测后空格还原。"""
    _drain_cpr(fd)                       # 先排空在途陈旧响应
    base = _query_cpr(fd, out, timeout)  # 单查询单读，收发平衡
    _width_debug(f"base={base}")
    if not base:
        return None
    out.write("🔧"); out.flush()
    after = _query_cpr(fd, out, timeout)
    _width_debug(f"after={after}")
    width = None
    if after and after[0] == base[0] and after[1] > base[1]:
        delta = after[1] - base[1]
        if delta in (1, 2):
            width = delta
    # 还原：回基线写空格覆盖探测字符（宽 2 终端占 2 列，写 2 空格），停回基线
    out.write(f"\x1b[{base[0]};{base[1]}H")
    out.write("  ")
    out.write(f"\x1b[{base[0]};{base[1]}H")
    out.flush()
    return width


def probe_emoji_width(timeout: float = 0.6) -> int:
    """实测终端对 emoji 的渲染列数（CPR 光标位置差），并设置 _EMOJI_WIDTH。

    在**进入全屏/交互界面之前**调用（此时 stdin 仍是普通终端）：
    写一个 🔧，比较写入前后的光标列号 → 差值即终端实际渲染宽度。
    xterm.js（JupyterLab/VS Code 终端）= 1；多数现代终端 = 2。
    探测用的字符会被空格覆盖还原，不污染界面。任何异常 → 保持默认 2。

    v5.5.3 鲁棒性增强：
    - 排空陈旧 CPR 响应取稳定基线（修复启动竞态导致的探测失败）；
    - 当前位置测量失败（换行/无响应）→ 回到行首重试一次；
    - 超时 0.4s → 0.6s（websocket 终端 CPR 往返更慢）。
    """
    import sys
    import os
    try:
        if not sys.stdout.isatty() or not sys.stdin.isatty():
            return _EMOJI_WIDTH
        fd = sys.stdin.fileno()
        import termios
        import tty
        old = termios.tcgetattr(fd)
        out = sys.stdout
        try:
            tty.setraw(fd)
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            except Exception:
                pass
            width = _measure_emoji(fd, out, timeout)
            if width is None:
                # 当前位置可能靠右边缘导致换行 → 回行首重试
                out.write("\r"); out.flush()
                width = _measure_emoji(fd, out, timeout)
            if width is None:
                _width_debug("probe failed, keep default")
                return _EMOJI_WIDTH
            _width_debug(f"probe width={width}")
            set_emoji_width(width)
            return width
        finally:
            # 清空探测期间可能残留的 CPR 响应字节，避免污染后续输入
            # （否则 stray `...R` 会被 shell/pt 当作输入——实测 "1R2Rpython3"）
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            except Exception:
                pass
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        return _EMOJI_WIDTH
