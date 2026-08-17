from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    """基于 JSONL 的会话存储（多会话，暂未做认证）。

    data_dir/conversations/
    ├── _meta.json      # 会话索引
    └── {id}.jsonl      # 每行一条 JSON 消息
    """

    def __init__(self, data_dir: Path) -> None:
        self.conv_dir = Path(data_dir) / "conversations"
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.conv_dir / "_meta.json"
        self._meta: dict[str, dict[str, Any]] = {}
        self._load_meta()

    def _load_meta(self) -> None:
        if not self._meta_path.exists():
            return
        try:
            data = json.loads(self._meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._meta = data
        except (json.JSONDecodeError, OSError):
            self._meta = {}

    def _save_meta(self) -> None:
        tmp = self._meta_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._meta_path)

    def _conv_path(self, conversation_id: str) -> Path:
        return self.conv_dir / f"{conversation_id}.jsonl"

    def create(self, title: Optional[str] = None, model: str = "") -> dict[str, Any]:
        conversation_id = uuid4().hex[:12]
        now = _utc_now_iso()
        conv = {
            "id": conversation_id,
            "title": title or f"会话 {conversation_id[:6]}",
            "model": model,
            "created_at": now,
            "updated_at": now,
        }
        self._meta[conversation_id] = conv
        self._save_meta()
        return dict(conv)

    def get(self, conversation_id: str) -> Optional[dict[str, Any]]:
        conv = self._meta.get(conversation_id)
        return dict(conv) if conv else None

    def list(self) -> list[dict[str, Any]]:
        return [dict(c) for c in sorted(self._meta.values(), key=lambda x: x["created_at"])]

    def set_model(self, conversation_id: str, model: str) -> Optional[dict[str, Any]]:
        conv = self._meta.get(conversation_id)
        if conv is None:
            return None
        conv["model"] = model
        conv["updated_at"] = _utc_now_iso()
        self._save_meta()
        return dict(conv)

    def add_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        conv = self._meta.get(conversation_id)
        if conv is None:
            raise KeyError(f"Conversation not found: {conversation_id}")
        entry = dict(message)
        entry.setdefault("ts", _utc_now_iso())
        path = self._conv_path(conversation_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        conv["updated_at"] = _utc_now_iso()
        self._save_meta()

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        path = self._conv_path(conversation_id)
        if not path.exists():
            return []
        messages = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return messages
