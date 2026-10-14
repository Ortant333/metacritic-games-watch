"""Хранилище сервиса: SQLite (WAL). Все обращения к базе - через этот модуль."""
import sqlite3
import threading
from datetime import datetime, timezone

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS games(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mc_id TEXT, slug TEXT UNIQUE, title TEXT, description TEXT,
  developer TEXT, publisher TEXT, release_date TEXT, premiere_year TEXT,
  rating TEXT, genres TEXT, cover_url TEXT, video_url TEXT, video_title TEXT,
  lead_platform TEXT,
  metascore INTEGER, metascore_sentiment TEXT, metascore_reviews INTEGER,
  userscore REAL, userscore_sentiment TEXT, user_reviews INTEGER,
  source TEXT, first_seen TEXT, last_updated TEXT, day_key TEXT
);
CREATE TABLE IF NOT EXISTS game_platforms(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id INTEGER NOT NULL, name TEXT, slug TEXT,
  metascore INTEGER, metascore_reviews INTEGER,
  userscore REAL, user_reviews INTEGER,
  UNIQUE(game_id, name)
);
CREATE TABLE IF NOT EXISTS review_quotes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id INTEGER NOT NULL, kind TEXT, bucket TEXT, score INTEGER,
  quote TEXT, author TEXT, publication TEXT, date TEXT
);
CREATE TABLE IF NOT EXISTS game_summaries(
  game_id INTEGER PRIMARY KEY,
  critic_summary TEXT, user_summary TEXT,
  critic_seen INTEGER, user_seen INTEGER, engine TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS letsplay(
  game_id INTEGER PRIMARY KEY,
  video_url TEXT, video_title TEXT, channel TEXT, views INTEGER,
  transcript TEXT, conclusion TEXT, engine TEXT, updated_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT, phase TEXT, status TEXT,
  games_seen INTEGER DEFAULT 0, games_added INTEGER DEFAULT 0,
  games_updated INTEGER DEFAULT 0, summaries_made INTEGER DEFAULT 0,
  letsplay_made INTEGER DEFAULT 0, message TEXT
);
CREATE TABLE IF NOT EXISTS day_state(
  day_key TEXT PRIMARY KEY, landing_done INTEGER DEFAULT 0,
  browse_page INTEGER DEFAULT 1, ingested INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init(db_path: str):
    global _CONN
    import os
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _LOCK:
        _CONN = sqlite3.connect(db_path, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.executescript(SCHEMA)
        _CONN.commit()


def _conn() -> sqlite3.Connection:
    if _CONN is None:
        raise RuntimeError("db not initialised")
    return _CONN


def q(sql, args=()):
    with _LOCK:
        cur = _conn().execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        return rows


def x(sql, args=()):
    with _LOCK:
        cur = _conn().execute(sql, args)
        _conn().commit()
        row = cur.lastrowid
        cur.close()
        return row


def executemany(sql, rows):
    with _LOCK:
        _conn().executemany(sql, rows)
        _conn().commit()


# ---------------- игры ----------------

def upsert_game(g: dict) -> tuple[int, bool]:
    """Возвращает (game_id, добавлена_ли_новая)."""
    fields = (
        g.get("mc_id"), g["slug"], g.get("title"), g.get("description"), g.get("developer"),
        g.get("publisher"), g.get("release_date"), g.get("premiere_year"),
        g.get("rating"), g.get("genres"), g.get("cover_url"), g.get("video_url"),
        g.get("video_title"), g.get("lead_platform"),
        g.get("metascore"), g.get("metascore_sentiment"), g.get("metascore_reviews"),
        g.get("userscore"), g.get("userscore_sentiment"), g.get("user_reviews"),
    )
    with _LOCK:
        row = _conn().execute("SELECT id FROM games WHERE slug=?", (g["slug"],)).fetchone()
        if row:
            _conn().execute(
                """UPDATE games SET mc_id=?, slug=?, title=?, description=?, developer=?,
                   publisher=?, release_date=?, premiere_year=?, rating=?, genres=?,
                   cover_url=?, video_url=?, video_title=?, lead_platform=?, metascore=?,
                   metascore_sentiment=?, metascore_reviews=?, userscore=?,
                   userscore_sentiment=?, user_reviews=?, last_updated=?, day_key=?
                   WHERE id=?""",
                fields + (now(), g.get("day_key"), row["id"]),
            )
            _conn().commit()
            return row["id"], False
        cur = _conn().execute(
            """INSERT INTO games(mc_id, slug, title, description, developer, publisher,
               release_date, premiere_year, rating, genres, cover_url, video_url, video_title,
               lead_platform, metascore, metascore_sentiment, metascore_reviews, userscore,
               userscore_sentiment, user_reviews, source, first_seen, last_updated, day_key)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            fields + (g.get("source"), now(), now(), g.get("day_key")),
        )
        _conn().commit()
        gid = cur.lastrowid
        cur.close()
        return gid, True


def replace_platforms(game_id: int, platforms: list[dict]):
    with _LOCK:
        _conn().execute("DELETE FROM game_platforms WHERE game_id=?", (game_id,))
        _conn().executemany(
            """INSERT INTO game_platforms(game_id, name, slug, metascore, metascore_reviews,
               userscore, user_reviews) VALUES(?,?,?,?,?,?,?)""",
            [(game_id, p.get("name"), p.get("slug"), p.get("metascore"),
              p.get("metascore_reviews"), p.get("userscore"), p.get("user_reviews"))
             for p in platforms],
        )
        _conn().commit()


def replace_quotes(game_id: int, quotes: list[dict]):
    with _LOCK:
        _conn().execute("DELETE FROM review_quotes WHERE game_id=?", (game_id,))
        _conn().executemany(
            """INSERT INTO review_quotes(game_id, kind, bucket, score, quote, author,
               publication, date) VALUES(?,?,?,?,?,?,?,?)""",
            [(game_id, r.get("kind"), r.get("bucket"), r.get("score"), r.get("quote"),
              r.get("author"), r.get("publication"), r.get("date")) for r in quotes],
        )
        _conn().commit()


def set_summary(game_id, critic, user, c_seen, u_seen, engine):
    with _LOCK:
        _conn().execute(
            """INSERT INTO game_summaries(game_id, critic_summary, user_summary,
               critic_seen, user_seen, engine, updated_at) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(game_id) DO UPDATE SET critic_summary=excluded.critic_summary,
               user_summary=excluded.user_summary, critic_seen=excluded.critic_seen,
               user_seen=excluded.user_seen, engine=excluded.engine,
               updated_at=excluded.updated_at""",
            (game_id, critic, user, c_seen, u_seen, engine, now()),
        )
        _conn().commit()


def set_letsplay(game_id, **kw):
    cols = {k: kw.get(k) for k in ("video_url", "video_title", "channel", "views",
                                   "transcript", "conclusion", "engine", "error")}
    cols["updated_at"] = now()
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{k}=excluded.{k}" for k in cols)
    with _LOCK:
        _conn().execute(
            f"INSERT INTO letsplay(game_id, {names}) VALUES(?, {marks}) "
            f"ON CONFLICT(game_id) DO UPDATE SET {updates}",
            (game_id, *cols.values()),
        )
        _conn().commit()


# ---------------- запуски / день / kv ----------------

def run_start(phase):
    return x("INSERT INTO runs(started_at, phase, status) VALUES(?,?,?)",
             (now(), phase, "running"))


def run_finish(run_id, **kw):
    sets = ", ".join(f"{k}=?" for k in kw)
    x(f"UPDATE runs SET finished_at=?, {sets} WHERE id=?", (now(), *kw.values(), run_id))


def recent_runs(limit=10):
    return [dict(r) for r in q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


def day_state(day_key):
    row = q("SELECT * FROM day_state WHERE day_key=?", (day_key,))
    return dict(row[0]) if row else None


def day_upsert(day_key, **kw):
    ds = day_state(day_key)
    if ds is None:
        x("INSERT INTO day_state(day_key, landing_done, browse_page, ingested) VALUES(?,?,?,?)",
          (day_key, kw.get("landing_done", 0), kw.get("browse_page", 1), kw.get("ingested", 0)))
    else:
        sets = ", ".join(f"{k}=?" for k in kw)
        x(f"UPDATE day_state SET {sets} WHERE day_key=?", (*kw.values(), day_key))


def kv_get(k, default=None):
    rows = q("SELECT v FROM kv WHERE k=?", (k,))
    return rows[0]["v"] if rows else default


def kv_set(k, v):
    x("INSERT INTO kv(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


# ---------------- выборки для UI ----------------

def games_list(platform=None, search=None, sort="metascore_desc", limit=500):
    sql = "SELECT g.* FROM games g"
    args = []
    where = []
    if platform:
        sql += " JOIN game_platforms p ON p.game_id=g.id"
        where.append("p.name=?")
        args.append(platform)
    if search:
        where.append("LOWER(g.title) LIKE ?")
        args.append(f"%{search.lower()}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    order = {
        "metascore_desc": "g.metascore IS NULL, g.metascore DESC, g.title",
        "metascore_asc": "g.metascore IS NULL, g.metascore ASC, g.title",
        "userscore_desc": "g.userscore IS NULL, g.userscore DESC, g.title",
        "userscore_asc": "g.userscore IS NULL, g.userscore ASC, g.title",
        "title_asc": "g.title COLLATE NOCASE",
        "date_desc": "g.release_date IS NULL, g.release_date DESC, g.title",
    }.get(sort, "g.metascore IS NULL, g.metascore DESC, g.title")
    sql += f" GROUP BY g.id ORDER BY {order} LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in q(sql, args)]
    if rows:
        ids = [r["id"] for r in rows]
        marks = ",".join("?" for _ in ids)
        plats = q(f"SELECT game_id, name FROM game_platforms WHERE game_id IN ({marks})", ids)
        by_game: dict[int, list[str]] = {}
        for p in plats:
            if p["name"]:
                by_game.setdefault(p["game_id"], []).append(p["name"])
        for r in rows:
            r["platforms"] = [{"name": n} for n in by_game.get(r["id"], [])]
    return rows


def game_full(game_id):
    rows = q("SELECT * FROM games WHERE id=?", (game_id,))
    if not rows:
        return None
    g = dict(rows[0])
    g["platforms"] = [dict(r) for r in q(
        "SELECT * FROM game_platforms WHERE game_id=? ORDER BY metascore IS NULL, metascore DESC",
        (game_id,))]
    g["quotes"] = [dict(r) for r in q(
        "SELECT * FROM review_quotes WHERE game_id=? ORDER BY kind, id", (game_id,))]
    s = q("SELECT * FROM game_summaries WHERE game_id=?", (game_id,))
    g["summary"] = dict(s[0]) if s else None
    lp = q("SELECT * FROM letsplay WHERE game_id=?", (game_id,))
    g["letsplay"] = dict(lp[0]) if lp else None
    return g


def all_platforms():
    return [r["name"] for r in q(
        "SELECT DISTINCT name FROM game_platforms WHERE name IS NOT NULL ORDER BY name")]


def counts():
    g = q("SELECT COUNT(*) c FROM games")[0]["c"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gd = q("SELECT COUNT(*) c FROM games WHERE day_key=?", (today,))[0]["c"]
    s = q("SELECT COUNT(*) c FROM game_summaries")[0]["c"]
    sl = q("SELECT COUNT(*) c FROM letsplay WHERE conclusion IS NOT NULL")[0]["c"]
    return {"games": g, "games_today": gd, "summaries": s, "letsplays": sl}
