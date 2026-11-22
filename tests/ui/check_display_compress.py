#!/usr/bin/env python3
"""压缩后显示保留门禁（v5.5.1 迭代：display_messages 解耦）

用法：
    /usr/bin/python3 tests/ui/check_display_compress.py

覆盖：
    1. add_message / 直接 messages.append（ai_handler 路径）都镜像进显示层
    2. replace_messages（压缩）只换上下文，显示层保留压缩前完整对话
    3. 压缩后继续对话，两层同步追加
    4. reset / 整表赋值（/resume 路径）后镜像引用不孤儿
    5. save_session/load_session_full 往返保留 display_messages；
       旧格式文件（无该字段）回退上下文消息
    6. 调用方未传 display 时，幂等保存不丢已有显示历史
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from cbhcli_pkg.core.session import Session, Message  # noqa: E402
from cbhcli_pkg.core.session_history import (  # noqa: E402
    SessionHistoryManager, build_session_data,
)


def main() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    s = Session("t")
    s.add_message("system", "SYS")
    s.add_message("user", "第一问")
    s.add_message("assistant", "回答1")
    # ai_handler 路径：直接 append
    s.messages.append(Message(role="user", content="第二问"))
    s.messages.append(Message(role="assistant", content="回答2"))
    check("add_message/直接 append 都镜像进显示层",
          len(s.messages) == 5 and len(s.display_messages) == 5)

    # 模拟压缩（replace_messages）
    s.replace_messages([
        Message(role="system", content="SYS"),
        Message(role="system", content="[历史对话摘要]早期内容…"),
        Message(role="user", content="第二问"),
        Message(role="assistant", content="回答2"),
    ])
    check("压缩后上下文变短", len(s.messages) == 4)
    check("压缩后显示层保留完整对话", len(s.display_messages) == 5)
    check("显示层内容仍是压缩前原文",
          [m.content for m in s.display_messages][:3] == ["SYS", "第一问", "回答1"])

    s.add_message("user", "第三问")
    check("压缩后继续对话两层同步追加",
          len(s.messages) == 5 and len(s.display_messages) == 6)

    # reset 重绑
    s.reset()
    s.add_message("user", "新会话")
    check("reset 后镜像仍有效", len(s.display_messages) == 1)

    # /resume 路径：先整表赋值再 append
    s.display_messages = []
    s.messages = [Message(role="system", content="SYS")]
    s.messages.append(Message(role="user", content="X"))
    check("整表赋值后镜像不孤儿", len(s.display_messages) == 1)

    # 落盘往返
    with tempfile.TemporaryDirectory() as td:
        mgr = SessionHistoryManager(Path(td))
        fp = mgr.save_session(
            s.get_context_messages(), session_id="sess1",
            display_messages=s.get_display_messages())
        full = mgr.load_session_full(os.path.basename(fp))
        check("落盘包含 display_messages",
              isinstance(full.get("display_messages"), list)
              and len(full["display_messages"]) == 1)
        s2 = Session("t")
        s2.set_display_messages(full["display_messages"])
        check("加载恢复显示层", s2.display_messages[0].content == "X")

        # 旧格式（无 display 字段）
        fp_old = mgr.save_session(
            s.get_context_messages(), session_id="sess_old")
        full_old = mgr.load_session_full(os.path.basename(fp_old))
        check("不传 display 时不写该字段（旧格式兼容）",
              "display_messages" not in full_old)

        # 幂等保存：调用方未传 display 时保留已有显示历史
        mgr.save_session(s.get_context_messages(), session_id="sess1")
        full2 = mgr.load_session_full(os.path.basename(fp))
        check("幂等保存不丢已有显示历史",
              len(full2.get("display_messages") or []) == 1)

    # build_session_data：审计快照不带 display（保持旧格式）
    data = build_session_data(s.get_context_messages(), session_id="x")
    check("快照构造默认不带 display", "display_messages" not in data)
    data2 = build_session_data(s.get_context_messages(), session_id="x",
                               display_messages=s.get_display_messages())
    check("快照构造可选带 display", len(data2["display_messages"]) == 1)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
