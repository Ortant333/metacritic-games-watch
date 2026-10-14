"""Metacritic Games Watch - точка входа.

Поднимает веб-интерфейс (uvicorn) и почасовой воркер обхода.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import worker  # noqa: E402

DB_PATH = os.environ.get("MC_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mc.db")).strip()
HOST = os.environ.get("MC_HOST", "0.0.0.0").strip()
PORT = int(os.environ.get("MC_PORT", "8095").strip())


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db.init(DB_PATH)
    worker.start_scheduler()
    import uvicorn
    from webapp import app
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
