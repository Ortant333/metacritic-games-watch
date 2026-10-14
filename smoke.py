"""Дымовой тест: один заход воркера в урезанном режиме."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GAMES_PER_RUN", "2")
os.environ.setdefault("LETSPLAY_PER_RUN", "0")

import db  # noqa: E402
import worker  # noqa: E402

db.init(os.environ.get("MC_DB", "data/mc.db"))

# 1) жив ли LLM из этой сети
probe = worker.llm.chat("Отвечай коротко.", "Скажи одно слово: готово")
print("LLM probe:", repr(probe), "| enabled:", worker.llm.enabled,
      "| budget left:", worker.llm.budget_left())

# 2) один заход
worker.tick()

rows = db.q("SELECT id, slug, title, metascore, userscore, developer, video_url, cover_url FROM games")
print("games in db:", len(rows))
for r in rows:
    print(dict(r))
plats = db.q("SELECT game_id, name, metascore, userscore FROM game_platforms")
print("platforms:", [dict(p) for p in plats][:8])
summ = db.q("SELECT game_id, engine, substr(critic_summary,1,80) c, substr(user_summary,1,80) u FROM game_summaries")
print("summaries:", [dict(s) for s in summ])
print("runs:", db.recent_runs(2))
