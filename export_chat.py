"""Экспорт переписки с нейросетью (Hermes session store) в raw JSONL.

Использование: python export_chat.py <session_id> <вых.файл>
Пишет все сообщения сессии (включая вызовы инструментов и их вывод),
секреты вычищаются, длинные куски обрезаются до 60000 знаков.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

DB = r"C:\Users\Работяга\AppData\Local\hermes\state.db"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|bearer)\s*[:=]\s*\S{12,}"),
]

CAP = 60000


def clean(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text or "")
    return text[:CAP]


def main():
    sid = sys.argv[1]
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "ai_chat.jsonl")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    s = con.execute("SELECT id, title, started_at FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        raise SystemExit(f"сессия {sid} не найдена")
    rows = con.execute(
        "SELECT * FROM messages WHERE session_id=? ORDER BY rowid", (sid,)).fetchall()
    n = 0
    with out.open("w", encoding="utf-8", newline="\n") as f:
        meta = {"type": "session", "session_id": s["id"], "title": s["title"],
                "started_at": s["started_at"]}
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in rows:
            d = dict(r)
            record = {
                "type": "message",
                "role": d.get("role"),
                "timestamp": d.get("timestamp"),
                "tool_name": d.get("tool_name"),
                "tool_call_id": d.get("tool_call_id"),
                "tool_calls": d.get("tool_calls"),
                "content": clean(d.get("content") or ""),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
    print(f"сессия: {s['title']!r} | сообщений: {n} | файл: {out.resolve()} "
          f"({out.stat().st_size} байт)")


if __name__ == "__main__":
    main()
