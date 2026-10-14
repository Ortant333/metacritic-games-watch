"""Похожие игры - TF-IDF + косинусная мера по названию/жанрам/описанию.

Считается по играм, уже лежащим в базе сервиса (как и требует задание).
Матрица кешируется и пересчитывается при изменении базы.
"""
import math
import re
import threading

import db

_TOKEN = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}")
_LOCK = threading.Lock()
_CACHE = {"key": None, "rows": None, "terms": None, "idf": None}


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text or "")]


def _build():
    games = db.q("SELECT id, title, genres, description FROM games ORDER BY id")
    docs = []
    for g in games:
        text = f"{g['title']} {g['title']} {g['genres'] or ''} {g['description'] or ''}"
        docs.append((g["id"], _tokens(text)))
    n = max(1, len(docs))
    df = {}
    for _, toks in docs:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
    rows = []
    for gid, toks in docs:
        tf = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        vec = {t: (1 + math.log(c)) * idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        rows.append((gid, {t: v / norm for t, v in vec.items()}))
    _CACHE["key"] = db.q("SELECT COUNT(*) c, COALESCE(MAX(id),0) m FROM games")[0]
    _CACHE["rows"] = rows
    _CACHE["idf"] = idf


def similar(game_id: int, top: int = 5) -> list[dict]:
    with _LOCK:
        key = db.q("SELECT COUNT(*) c, COALESCE(MAX(id),0) m FROM games")[0]
        if _CACHE["key"] != key or _CACHE["rows"] is None:
            _build()
        rows = _CACHE["rows"]
    target = next((vec for gid, vec in rows if gid == game_id), None)
    if target is None:
        return []
    scored = []
    for gid, vec in rows:
        if gid == game_id:
            continue
        score = sum(target[t] * vec[t] for t in target if t in vec)
        if score > 0:
            scored.append((score, gid))
    scored.sort(reverse=True)
    out = []
    for score, gid in scored[:top]:
        g = db.q("SELECT id, title, metascore, userscore FROM games WHERE id=?", (gid,))[0]
        out.append({"id": gid, "title": g["title"], "metascore": g["metascore"],
                    "userscore": g["userscore"], "score": round(score, 3)})
    return out
