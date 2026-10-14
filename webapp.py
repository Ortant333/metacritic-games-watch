"""Веб-интерфейс сервиса.

/            - список загруженных игр: фильтр по платформам, поиск, сортировка
/game/<id>   - карточка игры: полная информация, резюме отзывов, летсплей,
               похожие игры (клик по названию открывает карточку)
/monitor     - мониторинг работы в реальном времени + кнопка принудительного запуска
"""
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import similar
import worker

BASE_DIR = Path(__file__).parent
app = FastAPI(title="Metacritic Games Watch")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request, platform: str = "", q: str = "", sort: str = "metascore_desc"):
    games = db.games_list(platform=platform or None, search=q or None, sort=sort)
    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "games": games,
        "platforms": db.all_platforms(),
        "platform": platform,
        "q": q,
        "sort": sort,
        "counts": db.counts(),
    })


@app.get("/game/{game_id}", response_class=HTMLResponse)
def game_card(request: Request, game_id: int):
    g = db.game_full(game_id)
    if g is None:
        return HTMLResponse("Игра не найдена", status_code=404)
    sims = similar.similar(game_id)
    critic_quotes = [x for x in g["quotes"] if x["kind"] == "critic"]
    user_quotes = [x for x in g["quotes"] if x["kind"] == "user"]
    return templates.TemplateResponse(request, "game.html", {
        "request": request, "g": g, "similar": sims,
        "critic_quotes": critic_quotes, "user_quotes": user_quotes,
    })


@app.get("/monitor", response_class=HTMLResponse)
def monitor(request: Request):
    return templates.TemplateResponse(request, "monitor.html", {"request": request})


@app.get("/api/status")
def api_status():
    return JSONResponse({
        "state": worker.STATE,
        "counts": db.counts(),
        "runs": db.recent_runs(10),
        "llm": {
            "enabled": worker.llm.enabled,
            "model": worker.llm.model if worker.llm.enabled else None,
            "calls_today": worker.llm.calls_today(),
            "budget": worker.llm.budget,
        },
    })


@app.post("/api/run-now")
def api_run_now():
    worker.run_now()
    return JSONResponse({"ok": True, "detail": "заход обработки запущен"})


def create_app():
    return app
