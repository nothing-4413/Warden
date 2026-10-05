"""save_note：把值得记住的内容写回长期记忆（RAG 记忆的"写"侧）。

与 search_notes（读）配成一对，形成长期记忆的读 + 写闭环：
Agent 在会话中得出重要结论/决策时主动 save_note，之后（含下一次会话）可用
search_notes 检索到。写入走 NotesIndexer.index_text（分块 → 嵌入 → 入库）。
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from ...memory.indexer import NotesIndexer
from ..base import Tool


class SaveNoteInput(BaseModel):
    content: str = Field(description="要记住的内容：重要结论、事实、决策等")
    topic: str = Field(default="note", description="记忆主题/标签，用于检索时辨识来源")


def make_save_note_tool(indexer: NotesIndexer) -> Tool:
    def _save(content: str, topic: str = "note") -> str:
        # 每条记忆用独立 doc_id（含随机后缀），避免与已有记忆的 chunk id 冲突
        doc_id = f"note/{topic}/{uuid.uuid4().hex[:8]}"
        n = indexer.index_text(content, doc_id)
        return f"已记住 {n} 段到长期记忆（doc_id={doc_id}）。之后可用 search_notes 检索到这段记忆。"

    return Tool(
        name="save_note",
        description="Save an important fact, conclusion, or decision into long-term "
        "memory (RAG store), so it can be recalled in later sessions via "
        "search_notes. Use it when the user or you produce something worth "
        "remembering. Give a short topic describing what the note is about.",
        input_model=SaveNoteInput,
        func=_save,
    )
