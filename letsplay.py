"""Дополнительная часть 1: летсплей на YouTube.

Ищет по названию игры летсплеи, берёт самый популярный (по просмотрам),
достаёт расшифровку рассказа блоггера из субтитров и пишет заключение.
"""
import re
import urllib.request

from llm import LLM, letsplay_conclusion


class LetsplayError(RuntimeError):
    pass


def _ydl(opts: dict):
    import yt_dlp
    return yt_dlp.YoutubeDL(opts)


def find_popular_letsplay(title: str) -> dict:
    """Самый популярный летсплей по числу просмотров."""
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "skip_download": True}
    query = f"ytsearch25:{title} lets play"
    with _ydl(opts) as ydl:
        info = ydl.extract_info(query, download=False)
    entries = (info or {}).get("entries") or []
    best = None
    for e in entries:
        if not e:
            continue
        duration = e.get("duration") or 0
        # летсплей, а не трейлер: от 8 минут
        if duration and duration < 480:
            continue
        views = e.get("view_count") or 0
        if best is None or views > best["views"]:
            best = {
                "video_url": e.get("url") or e.get("webpage_url"),
                "video_title": e.get("title") or "",
                "channel": e.get("channel") or e.get("uploader") or "",
                "views": views,
                "id": e.get("id"),
            }
    if not best or not best.get("id"):
        raise LetsplayError(f"летсплей не найден: {title}")
    if not best["video_url"].startswith("http"):
        best["video_url"] = f"https://www.youtube.com/watch?v={best['id']}"
    return best


def _sub_text(sub_url: str) -> str:
    """Субтитры в формате json3 -> обычный текст."""
    req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        import json
        data = json.loads(raw)
        words = []
        for ev in data.get("events") or []:
            for seg in ev.get("segs") or []:
                t = seg.get("utf8") or ""
                if t.strip():
                    words.append(t)
        text = "".join(words)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
        return re.sub(r"\s+", " ", text).strip()


def fetch_transcript(video_id: str) -> str:
    """Расшифровка рассказа блоггера: сначала субтитры, потом автосубтитры."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with _ydl(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    tracks = (info or {}).get("subtitles") or {}
    auto = (info or {}).get("automatic_captions") or {}
    langs = ["en", "en-US", "en-orig", "ru", "uk", "de", "es"]
    for source in (tracks, auto):
        for lang in langs:
            if lang in source:
                for fmt in source[lang]:
                    if fmt.get("ext") in ("json3", "vtt", "srv3"):
                        try:
                            text = _sub_text(fmt["url"])
                            if len(text) > 200:
                                return text
                        except Exception:
                            continue
        # любой язык с латиницей/кириллицей
        for lang, formats in source.items():
            for fmt in formats:
                if fmt.get("ext") in ("json3", "vtt"):
                    try:
                        text = _sub_text(fmt["url"])
                        if len(text) > 200:
                            return text
                    except Exception:
                        continue
    raise LetsplayError("субтитры к ролику недоступны")


def find_trailer(title: str) -> dict | None:
    """Запасное «ссылку на видео»: официальный трейлер, если у игры нет
    встроенного видео на Metacritic."""
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "skip_download": True}
    with _ydl(opts) as ydl:
        info = ydl.extract_info(f"ytsearch8:{title} official trailer", download=False)
    best = None
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        dur = e.get("duration") or 0
        if dur and dur > 600:
            continue
        views = e.get("view_count") or 0
        if best is None or views > best["views"]:
            vid = e.get("id")
            best = {"video_url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
                    "video_title": e.get("title") or "", "views": views}
    return best


def process_game(llm: LLM, title: str) -> dict:
    """Полный цикл для одной игры: поиск -> транскрипт -> заключение."""
    best = find_popular_letsplay(title)
    transcript = ""
    try:
        transcript = fetch_transcript(best["id"])
    except LetsplayError:
        transcript = ""
    if transcript:
        conclusion, engine = letsplay_conclusion(llm, title, best["channel"], transcript)
    else:
        conclusion, engine = (
            f"Летсплей «{best['video_title']}» (канал {best['channel']}, "
            f"{best['views'] or '?'} просмотров). Расшифровка рассказа недоступна: "
            "у ролика нет субтитров.", "none")
    return {
        "video_url": best["video_url"],
        "video_title": best["video_title"],
        "channel": best["channel"],
        "views": best["views"],
        "transcript": transcript[:60000],
        "conclusion": conclusion,
        "engine": engine,
    }
