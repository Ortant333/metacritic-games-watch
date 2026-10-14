"""LLM-слой сервиса: короткие резюме отзывов и заключения по летсплеям.

Бэкенд - любой OpenAI-совместимый API (по умолчанию mimo flash). Дневной
бюджет вызовов ограничен: когда он исчерпан или API недоступен, включается
извлекающий fallback (работает всегда, без сети и без денег).
"""
import json
import os
import urllib.request
from datetime import date

import db


class LLM:
    def __init__(self):
        self.base = (os.environ.get("LLM_API_BASE", "") or "").strip().rstrip("/")
        self.key = (os.environ.get("LLM_API_KEY", "") or "").strip()
        self.model = (os.environ.get("LLM_MODEL", "mimo-v2.6-flash") or "").strip()
        self.budget = int(os.environ.get("LLM_DAILY_BUDGET", "120") or 120)
        self.timeout = int(os.environ.get("LLM_TIMEOUT", "90") or 90)

    @property
    def enabled(self) -> bool:
        return bool(self.base and self.key)

    def _budget_key(self) -> str:
        return "llm_calls_" + date.today().isoformat()

    def calls_today(self) -> int:
        return int(db.kv_get(self._budget_key(), "0"))

    def budget_left(self) -> int:
        return max(0, self.budget - self.calls_today())

    def chat(self, system: str, user: str, max_tokens: int = 400) -> str | None:
        """Один вызов модели. None = некому ответить (нет API/бюджета)."""
        if not self.enabled or self.budget_left() <= 0:
            return None
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"].strip()
            db.kv_set(self._budget_key(), self.calls_today() + 1)
            return text
        except Exception:
            return None


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "..."


def _quotes_block(quotes: list[dict], limit: int = 24) -> str:
    lines = []
    for r in quotes[:limit]:
        label = r.get("bucket", "")
        score = r.get("score")
        head = f"[{label}{'/' + str(score) if score is not None else ''}]"
        lines.append(f"{head} {_clip(r['quote'], 700)}")
    return "\n".join(lines)


def _extractive(quotes: list[dict], kind: str) -> str:
    """Fallback без LLM: тональность по ведрам/score + выдержки из отзывов."""
    pos = [r for r in quotes if r.get("bucket") == "positive" or (r.get("score") or 0) >= 75]
    neg = [r for r in quotes if r.get("bucket") == "negative" or (r.get("score") or 100) <= 40]
    who = "Критики" if kind == "critic" else "Игроки"
    parts = []
    if pos:
        first = pos[0]["quote"].split(". ")[0].strip()
        parts.append(f"{who} отмечают: {first[:300]}.")
    if neg:
        first = neg[0]["quote"].split(". ")[0].strip()
        parts.append(f"Критика: {first[:300]}.")
    if not parts:
        parts.append(f"{who}: пока слишком мало отзывов для устойчивого резюме.")
    return " ".join(parts)


def summarize_reviews(llm: LLM, title: str, kind: str, quotes: list[dict]) -> tuple[str, str]:
    """Возвращает (текст, движок)."""
    who = "критиков (игровые издания)" if kind == "critic" else "игроков"
    prompt = (
        f"Игра: «{title}». Ниже - отзывы {who}.\n\n"
        f"{_quotes_block(quotes)}\n\n"
        f"Составь короткое резюме отзывов {who} на русском языке, 2-4 предложения: "
        "что хвалят, в чём критика. Только по приведённым отзывам, без выдумок. "
        "Не используй списки и заголовки."
    )
    text = llm.chat(
        "Ты - аналитик игровых отзывов. Пишешь по-русски, коротко, по делу, без воды.",
        prompt,
    )
    if text:
        return text, "llm"
    return _extractive(quotes, kind), "extractive"


def letsplay_conclusion(llm: LLM, title: str, channel: str, transcript: str) -> tuple[str, str]:
    prompt = (
        f"Игра: «{title}». Ниже - расшифровка рассказа блоггера {channel} из летсплея "
        f"на YouTube.\n\n{_clip(transcript, 14000)}\n\n"
        "Составь заключение по этому летсплею на русском языке, 3-4 предложения: "
        "о чём прохождение, как блоггер оценивает игру, что особенно показывает. "
        "Без списков и заголовков."
    )
    # последние 20 вызовов бюджета держим для резюме отзывов (обязательная часть)
    text = None
    if llm.budget_left() > 20:
        text = llm.chat(
            "Ты - обозреватель игровых летсплеев. Пишешь по-русски, коротко и по делу.",
            prompt,
        )
    if text:
        return text, "llm"
    # fallback: первые фразы самого рассказа
    first = transcript.strip().replace("\n", " ")
    return f"Выдержка из рассказа блоггера: {_clip(first, 400)}", "extractive"
