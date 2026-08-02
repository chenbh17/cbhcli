"""文件检查点 — Harness 执行环境层（回滚能力）

write/edit 工具执行前自动备份目标文件，支持 /undo 回滚。

存储结构（Agent 工作空间内）：
  ~/.cbhcli/agents/<agent>/backups/
    manifest.jsonl          备份清单（每行一条：{id, ts, tool, path, backup, existed}）
    <id>_<文件名>           备份内容

策略：
  - 只备份 write/edit 的目标文件；文件不存在时记录 existed=false（undo 时删除新文件）
  - 保留最近 MAX_BACKUPS 条，超出自动清理最旧的
  - 一切异常静默（备份失败不阻塞写操作）
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

MAX_BACKUPS = 50


class CheckpointManager:
    """文件备份/回滚管理器"""

    def __init__(self, workspace_path: Optional[Path] = None):
        self._dir: Optional[Path] = None
        self._manifest: Optional[Path] = None
        if workspace_path:
            try:
                self._dir = Path(workspace_path) / "backups"
                self._dir.mkdir(parents=True, exist_ok=True)
                self._manifest = self._dir / "manifest.jsonl"
            except Exception:
                self._dir = None
                self._manifest = None

    @property
    def available(self) -> bool:
        return self._dir is not None and self._manifest is not None

    # --------------------------------------------------------------
    # 备份
    # --------------------------------------------------------------

    def backup(self, file_path: str, tool_name: str) -> bool:
        """write/edit 执行前备份目标文件

        Returns:
            是否成功备份（文件不存在也算成功，记录 existed=false）
        """
        if not self.available:
            return False
        try:
            target = Path(file_path).expanduser().resolve()
            entry = {
                "id": f"{int(time.time() * 1000)}",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "tool": tool_name,
                "path": str(target),
                "existed": target.exists(),
                "backup": "",
            }
            if target.exists() and target.is_file():
                backup_name = f"{entry['id']}_{target.name}"
                backup_path = self._dir / backup_name
                shutil.copy2(target, backup_path)
                entry["backup"] = backup_name

            with open(self._manifest, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            self._prune()
            return True
        except Exception:
            return False

    # --------------------------------------------------------------
    # 回滚
    # --------------------------------------------------------------

    def list_backups(self, limit: int = 20) -> list[dict]:
        """列出最近的备份（新的在前）"""
        entries = self._read_manifest()
        return list(reversed(entries))[-limit:][::-1] if entries else []

    def undo_last(self) -> tuple[bool, str]:
        """回滚最近一次备份

        Returns:
            (是否成功, 描述信息)
        """
        entries = self._read_manifest()
        if not entries:
            return False, "没有可回滚的备份"

        # 找最后一条未回滚的记录（简单策略：直接取最后一条并删除）
        entry = entries[-1]
        ok, msg = self._restore(entry)
        if ok:
            self._remove_entry(len(entries) - 1)
        return ok, msg

    def undo_by_id(self, backup_id: str) -> tuple[bool, str]:
        """按 ID 回滚指定备份"""
        entries = self._read_manifest()
        for i, entry in enumerate(entries):
            if entry.get("id") == backup_id:
                ok, msg = self._restore(entry)
                if ok:
                    self._remove_entry(i)
                return ok, msg
        return False, f"找不到备份 ID: {backup_id}"

    def _restore(self, entry: dict) -> tuple[bool, str]:
        try:
            target = Path(entry["path"])
            if entry.get("existed"):
                backup_path = self._dir / entry["backup"]
                if not backup_path.exists():
                    return False, f"备份文件已丢失: {backup_path}"
                shutil.copy2(backup_path, target)
                backup_path.unlink()
                return True, f"已恢复: {target}"
            else:
                # 备份时文件不存在 → 删除工具新建的文件
                if target.exists():
                    target.unlink()
                    return True, f"已删除新建文件: {target}"
                return True, f"文件本就不存在: {target}（无需回滚）"
        except Exception as e:
            return False, f"回滚失败: {e}"

    # --------------------------------------------------------------
    # 清单维护
    # --------------------------------------------------------------

    def _read_manifest(self) -> list[dict]:
        if not self.available or not self._manifest.exists():
            return []
        entries = []
        try:
            for line in self._manifest.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return entries

    def _remove_entry(self, index: int):
        entries = self._read_manifest()
        if 0 <= index < len(entries):
            entries.pop(index)
            try:
                with open(self._manifest, "w", encoding="utf-8") as f:
                    for e in entries:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def _prune(self):
        """清理超出上限的最旧备份"""
        entries = self._read_manifest()
        if len(entries) <= MAX_BACKUPS:
            return
        overflow = entries[:len(entries) - MAX_BACKUPS]
        keep = entries[len(entries) - MAX_BACKUPS:]
        for entry in overflow:
            backup_name = entry.get("backup")
            if backup_name:
                try:
                    (self._dir / backup_name).unlink(missing_ok=True)
                except Exception:
                    pass
        try:
            with open(self._manifest, "w", encoding="utf-8") as f:
                for e in keep:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception:
            pass
