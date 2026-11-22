#!/usr/bin/env python3
"""附件解析 + 图片 MIME 门禁（v5.5.2 粘贴图片 400 修复）

用法：
    /usr/bin/python3 tests/ui/check_attachments.py

背景（用户实测）：粘贴图片后主模型报 400 "image format is illegal"。
根因：attachments 把图片存成**带 data URI 前缀**的 base64，而 Message.to_dict
又包一层 `data:image/jpeg;base64,` → 双重前缀畸形 URL；且 MIME 硬编码 jpeg
对 PNG 不正确。修复：images 统一存**裸 base64**（与 image 工具/Web 一致），
to_dict 按魔数嗅探真实 MIME。

覆盖：
    1. parse_attachments 返回裸 base64（无 data: 前缀）
    2. _image_data_uri 嗅探 PNG/JPEG/GIF/WebP，单前缀
    3. Message.to_dict 多模态 URL 正确（无双重前缀）
    4. 已带前缀的输入透传不二次包裹
    5. [图片:]/[文件:] 引用 + 裸路径解析
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from cbhcli_pkg.core import attachments as A  # noqa: E402
from cbhcli_pkg.core.session import Message, _image_data_uri  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC")


def main() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    ud = A.upload_dir()
    ud.mkdir(parents=True, exist_ok=True)
    png = ud / "gate_att.png"
    png.write_bytes(PNG)
    txt = Path(tempfile.gettempdir()) / "gate_att.txt"
    txt.write_text("hi")
    try:
        # 1. 裸 base64
        t, imgs = A.parse_attachments("[图片: gate_att.png]")
        check("解析出 1 张图", len(imgs) == 1)
        check("images 为裸 base64（无 data: 前缀）",
              bool(imgs) and not imgs[0].startswith("data:"))

        # 2. MIME 嗅探
        check("PNG 嗅探", _image_data_uri(imgs[0]).startswith("data:image/png;base64,"))
        jpg = base64.b64encode(b"\xff\xd8\xff\xe0jpg").decode()
        check("JPEG 嗅探", _image_data_uri(jpg).startswith("data:image/jpeg;base64,"))
        gif = base64.b64encode(b"GIF89agif").decode()
        check("GIF 嗅探", _image_data_uri(gif).startswith("data:image/gif;base64,"))
        webp = base64.b64encode(b"RIFF\x00\x00\x00\x00WEBPvp8").decode()
        check("WebP 嗅探", _image_data_uri(webp).startswith("data:image/webp;base64,"))

        # 3. to_dict 无双重前缀
        m = Message(role="user", content=t, images=imgs)
        url = m.to_dict()["content"][1]["image_url"]["url"]
        check("to_dict 单前缀 PNG", url.startswith("data:image/png;base64,"))
        check("to_dict 无双重前缀", "base64,data:" not in url)

        # 4. 已带前缀透传
        pre = "data:image/png;base64," + imgs[0]
        check("已带前缀不二次包裹", _image_data_uri(pre) == pre)

        # 5. 引用 + 裸路径
        t2, i2 = A.parse_attachments(f"[文件: gate_att.txt ({txt})]")
        check("文件引用保留", i2 == [] and "[文件: gate_att.txt (" in t2)
        t3, i3 = A.parse_attachments(str(png))
        check("裸图片路径→引用+图", len(i3) == 1 and t3 == "[图片: gate_att.png]")
        t4, i4 = A.parse_attachments("普通文本 /no/such/file 结束")
        check("不存在路径不动", i4 == [] and t4 == "普通文本 /no/such/file 结束")

        # 6. 多张图片 + 混合（显式引用）
        png2 = ud / "gate_att2.png"
        png2.write_bytes(PNG)
        tm, im = A.parse_attachments(
            f"[图片: gate_att.png]\n[图片: gate_att2.png]\n[文件: gate_att.txt ({txt})]")
        check("多图混合(2图1文件)", len(im) == 2 and "[文件: gate_att.txt (" in tm)
        parts = [p for p in Message(role="user", content=tm, images=im).to_dict()["content"]
                 if p.get("type") == "image_url"]
        check("to_dict 两个 image_url", len(parts) == 2)

        # 7. 含空格裸路径（多条 + 混合）
        sp1 = ud / "gate space 1.png"
        sp1.write_bytes(PNG)
        ts, is_ = A.parse_attachments(f"{sp1} {png2} {txt}")
        check("含空格裸路径(2图1文件)", len(is_) == 2 and "[文件: gate_att.txt (" in ts)

        # 8. 引号包裹 / file:// / 转义空格
        tq, iq = A.parse_attachments(f"'{sp1}' '{txt}'")
        check("单引号包裹(图+文件)", len(iq) == 1 and "[文件: gate_att.txt (" in tq)
        from urllib.parse import quote
        tu, iu = A.parse_attachments("file://" + quote(str(sp1)))
        check("file:// URI", len(iu) == 1)
        te, ie = A.parse_attachments(str(sp1).replace(" ", "\\ "))
        check("转义空格路径", len(ie) == 1)

        # 9. 非文件引号文本不动
        tn, inn = A.parse_attachments("他说 '你好' 然后 \"世界\"")
        check("非文件引号不动", inn == [] and tn == "他说 '你好' 然后 \"世界\"")
        png2.unlink(missing_ok=True)
        sp1.unlink(missing_ok=True)
    finally:
        png.unlink(missing_ok=True)
        txt.unlink(missing_ok=True)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
