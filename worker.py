"""Воркер сервиса: почасовой обход Metacritic + резюме + летсплеи.

Задача (по тестовому заданию):
- 1 раз в час брать 20 игр, которые сегодня ещё не обрабатывали;
- каждый новый день начинать заново: сначала раздел New Releases главной
  Games, затем страницы SEE ALL (сортировка «Новые») по порядку;
- по каждой игре собирать данные и писать резюме отзывов (критики и игроки
  раздельно), при повторных обходах - обновлять;
- доп. часть 1: летсплей на YouTube с заключением по рассказу блоггера.
"""
import os
import threading
import time
import traceback
from datetime import datetime, timezone

import db
import letsplay as lp
import mc
from llm import LLM, summarize_reviews

GAMES_PER_RUN = int((os.environ.get("GAMES_PER_RUN", "20") or "20").strip())
LETSPPLAY_PER_RUN = int((os.environ.get("LETSPLAY_PER_RUN", "2") or "2").strip())

STATE = {
    "started_at": None,
    "last_tick": None,
    "next_tick": None,
    "running": False,
    "tick_count": 0,
    "stages": {
        "ingest": {"status": "idle", "detail": "", "updated_at": None},
        "summarize": {"status": "idle", "detail": "", "updated_at": None},
        "letsplay": {"status": "idle", "detail": "", "updated_at": None},
    },
    "progress": {"processed": 0, "total": 0},
    "log": [],
    "errors": [],
}

_log_lock = threading.Lock()


def log(msg: str):
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}"
    with _log_lock:
        STATE["log"].append(line)
        del STATE["log"][:-80]


def set_stage(name: str, status: str, detail: str = ""):
    st = STATE["stages"].setdefault(name, {})
    st["status"] = status
    st["detail"] = detail
    st["updated_at"] = datetime.now(timezone.utc).strftime("%H:%M:%S")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sources_for_today() -> list[dict]:
    """Источник партии: New Releases (первый заход дня) или следующая страница SEE ALL."""
    ds = db.day_state(_today())
    if ds is None:
        db.day_upsert(_today(), landing_done=0, browse_page=1, ingested=0)
        ds = db.day_state(_today())
    if not ds["landing_done"]:
        log("источник: New Releases главной Games")
        cards = mc.landing_new_releases()
        db.day_upsert(_today(), landing_done=1)
        return cards
    page = ds["browse_page"] or 1
    log(f"источник: SEE ALL (новые), страница {page}")
    items, total = mc.browse_new(page)
    db.day_upsert(_today(), browse_page=page + 1)
    log(f"в разделе SEE ALL всего {total} игр, взята страница {page}")
    return items


def _ingest_one(card: dict, llm: LLM, counters: dict):
    slug = card["slug"]
    g = mc.fetch_game(slug)
    g["source"] = card.get("url") or f"https://www.metacritic.com/game/{slug}/"
    g["day_key"] = _today()
    if not g.get("cover_url"):
        g["cover_url"] = card.get("cover_url")

    # юзер-оценки по платформам (метаскор по платформам уже в карточке);
    # ведущая платформа берёт оценку из сводки самой игры
    lead_slug = _mc_lead_slug(g)
    for p in g["platforms"]:
        if p.get("slug") and p["slug"] == lead_slug:
            p["userscore"] = g.get("userscore")
            p["user_reviews"] = g.get("user_reviews")
        elif p.get("slug"):
            try:
                us = mc.platform_user_score(slug, p["slug"])
                if us:
                    p.update(us)
            except Exception:
                pass

    # у части игр на Metacritic нет встроенного видео - берём трейлер с YouTube
    if not g.get("video_url"):
        try:
            tr = lp.find_trailer(g["title"])
            if tr:
                g["video_url"] = tr["video_url"]
                g["video_title"] = tr["video_title"]
        except Exception:
            pass

    game_id, added = db.upsert_game(g)
    db.replace_platforms(game_id, g["platforms"])
    quotes = ([{"kind": "critic", **r} for r in g["critic_quotes"]]
              + [{"kind": "user", **r} for r in g["user_quotes"]])
    db.replace_quotes(game_id, quotes)

    counters["games_seen"] += 1
    counters["games_added" if added else "games_updated"] += 1
    log(f"{'+' if added else '='} {g['title'][:60]} (мета {g['metascore']}, юзер {g['userscore']})")
    return game_id, g


def _mc_lead_slug(g: dict) -> str | None:
    for p in g["platforms"]:
        if p.get("name") == g.get("lead_platform"):
            return p.get("slug")
    return None


def _summaries(llm: LLM, counters: dict):
    """Резюме по отзывам: создаётся при появлении, обновляется при росте отзывов."""
    set_stage("summarize", "running")
    rows = db.q("""
        SELECT g.id, g.title,
               COALESCE(s.critic_seen,-1) cs, COALESCE(s.user_seen,-1) us
        FROM games g LEFT JOIN game_summaries s ON s.game_id=g.id
        WHERE s.game_id IS NULL OR s.critic_seen IS NULL
           OR EXISTS(SELECT 1 FROM review_quotes q WHERE q.game_id=g.id AND q.kind='critic')
        ORDER BY g.id DESC LIMIT 60
    """)
    for row in rows:
        gid = row["id"]
        crit = db.q("SELECT * FROM review_quotes WHERE game_id=? AND kind='critic'", (gid,))
        user = db.q("SELECT * FROM review_quotes WHERE game_id=? AND kind='user'", (gid,))
        crit = [dict(r) for r in crit]
        user = [dict(r) for r in user]
        prev = db.q("SELECT * FROM game_summaries WHERE game_id=?", (gid,))
        prev = dict(prev[0]) if prev else {}
        can_llm = llm.budget_left() > 0
        need_c = (prev.get("critic_seen") != len(crit) or not prev.get("critic_summary")
                  or (prev.get("engine") != "llm" and can_llm))
        need_u = (prev.get("user_seen") != len(user) or not prev.get("user_summary")
                  or (prev.get("engine") != "llm" and can_llm))
        if not (need_c or need_u):
            continue
        critic_text = prev.get("critic_summary") or ""
        user_text = prev.get("user_summary") or ""
        engine = prev.get("engine") or "extractive"
        if need_c and crit:
            critic_text, engine = summarize_reviews(llm, row["title"], "critic", crit)
        if need_u and user:
            user_text, e2 = summarize_reviews(llm, row["title"], "user", user)
            engine = e2 if user_text else engine
        if critic_text or user_text:
            db.set_summary(gid, critic_text, user_text, len(crit), len(user), engine)
            counters["summaries_made"] += 1
            log(f"резюме: {row['title'][:50]} [{engine}]")
    set_stage("summarize", "done",
              f"резюме за прогон: {counters['summaries_made']}")


def _letsplays(llm: LLM, counters: dict):
    set_stage("letsplay", "running")
    rows = db.q("""
        SELECT g.id, g.title FROM games g
        LEFT JOIN letsplay l ON l.game_id=g.id
        WHERE l.game_id IS NULL OR (l.conclusion IS NULL AND l.error IS NULL)
        ORDER BY g.id DESC LIMIT ?
    """, (LETSPPLAY_PER_RUN,))
    for row in rows:
        try:
            set_stage("letsplay", "running", row["title"][:50])
            res = lp.process_game(llm, row["title"])
            db.set_letsplay(row["id"], **res)
            counters["letsplay_made"] += 1
            log(f"летсплей: {row['title'][:40]} - {res['video_title'][:40]}")
        except Exception as e:
            db.set_letsplay(row["id"], error=str(e)[:300])
            log(f"летсплей: {row['title'][:40]} - ошибка: {str(e)[:100]}")
    set_stage("letsplay", "done",
              f"летсплеев за прогон: {counters['letsplay_made']}")


_lock = threading.Lock()
_stop = threading.Event()


def tick():
    """Один заход обработки. Наложение запусков исключено."""
    if not _lock.acquire(blocking=False):
        log("заход уже идёт - новый запуск не нужен")
        return
    counters = {"games_seen": 0, "games_added": 0, "games_updated": 0,
                "summaries_made": 0, "letsplay_made": 0}
    run_id = db.run_start("hourly")
    STATE["running"] = True
    STATE["progress"] = {"processed": 0, "total": GAMES_PER_RUN}
    try:
        set_stage("ingest", "running", "выборка партии")
        cards = _sources_for_today()
        today = _today()
        fresh = [c for c in cards
                 if not db.q("SELECT id FROM games WHERE slug=? AND day_key=?",
                             (c["slug"], today))]
        fresh = fresh[:GAMES_PER_RUN]
        log(f"партия: {len(fresh)} игр к обработке")
        for i, card in enumerate(fresh, 1):
            STATE["progress"] = {"processed": i - 1, "total": len(fresh)}
            set_stage("ingest", "running", card.get("title") or card["slug"])
            try:
                _ingest_one(card, llm, counters)
            except Exception as e:
                log(f"ошибка обработки {card['slug']}: {str(e)[:120]}")
                STATE["errors"].append(f"{card['slug']}: {str(e)[:120]}")
                del STATE["errors"][:-20]
            STATE["progress"] = {"processed": i, "total": len(fresh)}
        set_stage("ingest", "done", f"обработано {counters['games_seen']} игр")
        db.day_upsert(today, ingested=(db.day_state(today) or {}).get("ingested", 0)
                      + counters["games_seen"])

        _summaries(llm, counters)
        _letsplays(llm, counters)

        db.run_finish(run_id, status="done",
                      games_seen=counters["games_seen"], games_added=counters["games_added"],
                      games_updated=counters["games_updated"],
                      summaries_made=counters["summaries_made"],
                      letsplay_made=counters["letsplay_made"],
                      message=f"заход завершён, партия {counters['games_seen']} игр")
        log("заход завершён")
    except Exception as e:
        traceback.print_exc()
        db.run_finish(run_id, status="error", message=str(e)[:300])
        set_stage("ingest", "error", str(e)[:120])
        STATE["errors"].append(str(e)[:150])
        log(f"заход упал: {str(e)[:150]}")
    finally:
        STATE["running"] = False
        STATE["last_tick"] = datetime.now(timezone.utc).strftime("%H:%M:%S")
        STATE["tick_count"] += 1
        _lock.release()


llm = LLM()


def run_now():
    threading.Thread(target=tick, daemon=True).start()


def scheduler_loop(period: int = 3600):
    STATE["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    while not _stop.is_set():
        tick()
        nxt = time.time() + period
        STATE["next_tick"] = datetime.fromtimestamp(nxt, timezone.utc).strftime("%H:%M:%S")
        _stop.wait(period)


def start_scheduler():
    threading.Thread(target=scheduler_loop, daemon=True).start()
