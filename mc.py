"""Клиент Metacritic: вежливые запросы и разбор Nuxt-страниц.

Источник данных у всех страниц один - payload __NUXT_DATA__ (devalue-формат,
разбирается nuxtdata.nuxt_json). Разметка HTML используется только для ленты
New Releases на главной Games (там 20 карточек data-testid="product-card").
"""
import html as htmlmod
import re
import threading
import time
import urllib.request

from nuxtdata import nuxt_json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
BASE = "https://www.metacritic.com"
MIN_DELAY = 1.2          # пауза между запросами к площадке, сек
IMG_BASE = f"{BASE}/a/img/catalog"

_gate = threading.Lock()
_last = [0.0]


class FetchError(RuntimeError):
    pass


def fetch(url: str, tries: int = 3) -> str:
    """GET с паузой между запросами и повторами на 429/5xx."""
    for attempt in range(tries):
        with _gate:
            wait = MIN_DELAY - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        })
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                if r.status != 200:
                    raise FetchError(f"HTTP {r.status} для {url}")
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise FetchError(f"HTTP {e.code} для {url}") from e
        except Exception:
            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    raise FetchError(url)


def _img_url(image: dict | None) -> str | None:
    if not image:
        return None
    bp = image.get("bucketPath")
    if bp and image.get("bucketType") == "catalog":
        return IMG_BASE + bp
    return None


def _platform_slug(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"[?&]platform=([^&]+)", url)
    return m.group(1) if m else None


# ---------------- лента New Releases (главная Games) ----------------

def landing_new_releases() -> list[dict]:
    """Первые 20 игр раздела New Releases со страницы /game/."""
    html = fetch(f"{BASE}/game/")
    j = html.find("New Releases")
    k = html.find("Upcoming Games")
    seg = html[j:k if k > j else j + 200000]
    cards = []
    blocks = re.split(r'(?=<div class="flex h-full flex-col items-start justify-between" data-testid="product-card")', seg)
    for b in blocks:
        m = re.search(r'data-testid="product-card-content" href="(/game/[^"]+)"', b)
        if not m:
            continue
        mt = re.search(r'data-testid="product-card-title" title="([^"]*)"', b)
        mi = re.search(r'<img[^>]+src="([^"]+)"', b)
        ms = re.search(r'title="Metascore (\d+) out of 100"', b)
        cards.append({
            "url": BASE + m.group(1),
            "slug": m.group(1).strip("/").split("/")[-1],
            "title": htmlmod.unescape(mt.group(1)) if mt else None,
            "cover_url": htmlmod.unescape(mi.group(1)) if mi else None,
            "landing_metascore": int(ms.group(1)) if ms else None,
        })
        if len(cards) >= 20:
            break
    return cards


# ---------------- лента SEE ALL (сортировка «Новые») ----------------

def browse_new(page: int = 1) -> tuple[list[dict], int]:
    """Страница `page` раздела SEE ALL (all-time/new). Возвращает (игры, total)."""
    url = f"{BASE}/browse/game/all/all/all-time/new/" + (f"?page={page}" if page > 1 else "")
    html = fetch(url)
    data = nuxt_json(html)
    node = None
    for key, val in (data.get("data") or {}).items():
        if key.startswith("browse-game-") and isinstance(val, dict) and "items" in val:
            node = val
            break
    if node is None:
        raise FetchError(f"не нашёл список игр в payload {url}")
    out = []
    for it in node["items"]:
        out.append({
            "url": f"{BASE}/game/{it['slug']}/",
            "slug": it["slug"],
            "title": it.get("title"),
            "cover_url": _img_url(it.get("image")),
            "release_date": it.get("releaseDate"),
            "premiere_year": str(it.get("premiereYear") or ""),
            "genres": ", ".join(g.get("name", "") for g in (it.get("genres") or []) if g.get("name")),
            "description": it.get("description"),
            "platforms_hint": [p.get("name") for p in (it.get("platforms") or []) if p.get("name")],
            "landing_metascore": (it.get("criticScoreSummary") or {}).get("score"),
        })
    return out, int(node.get("total") or 0)


# ---------------- страница игры ----------------

def _score_summary(node: dict) -> dict:
    return {
        "score": node.get("score"),
        "max": node.get("max"),
        "sentiment": node.get("sentiment"),
        "review_count": node.get("reviewCount"),
        "positive": node.get("positiveCount"),
        "neutral": node.get("neutralCount"),
        "negative": node.get("negativeCount"),
        "url": node.get("url"),
    }


def parse_game_page(html: str, slug: str) -> dict:
    data = nuxt_json(html)
    page_node = None
    for key, val in (data.get("data") or {}).items():
        if key.startswith("loadPage:games:") and isinstance(val, dict):
            page_node = val
            break
    if page_node is None:
        raise FetchError(f"не нашёл payload игры {slug}")

    item = None
    critic = user = None
    critic_reviews = {"default": [], "positive": [], "negative": [], "neutral": []}
    user_reviews = {"default": [], "positive": [], "negative": [], "neutral": []}
    for comp in page_node.get("components") or []:
        d = (comp or {}).get("data") or {}
        it = d.get("item")
        if isinstance(it, dict):
            if "description" in it and "platforms" in it and item is None:
                item = it
            elif "reviewCount" in it and "max" in it:
                if it.get("max") == 10:
                    user = it
                else:
                    critic = it
            elif "default" in it and isinstance(it.get("default"), list):
                sample = (it.get("default") or it.get("positive") or [{}])[0] or {}
                target = user_reviews if ("thumbsUp" in sample or "version" in sample) else critic_reviews
                for bucket in target:
                    target[bucket] = it.get(bucket) or []

    if item is None:
        raise FetchError(f"не нашёл карточку игры в payload {slug}")

    companies = ((item.get("production") or {}).get("companies")) or []
    developer = next((c.get("name") for c in companies if c.get("typeName") == "Developer"), None)
    publisher = next((c.get("name") for c in companies if c.get("typeName") == "Publisher"), None)

    video = item.get("video") or {}
    video_url = video.get("embedUrl")
    if not video_url and video.get("jwPlayerId"):
        video_url = f"https://cdn.jwplayer.com/players/{video['jwPlayerId']}.html"

    images = item.get("images") or []
    cover = None
    for prefer in ("cardImage", "mainImage"):
        cover = next((_img_url(im) for im in images
                      if im.get("typeName") == prefer and _img_url(im)), None)
        if cover:
            break

    platforms = []
    for p in item.get("platforms") or []:
        cs = p.get("criticScoreSummary") or {}
        platforms.append({
            "name": p.get("name"),
            "slug": _platform_slug(cs.get("url")),
            "metascore": cs.get("score"),
            "metascore_reviews": cs.get("reviewCount"),
            "userscore": None,
            "user_reviews": None,
        })

    lead_platform = item.get("platform")

    def pack_reviews(src):
        out = []
        for bucket, rows in src.items():
            for r in rows:
                quote = (r.get("quote") or "").strip()
                if not quote:
                    continue
                out.append({
                    "bucket": bucket,
                    "score": r.get("score"),
                    "quote": quote[:2000],
                    "author": r.get("author") or "",
                    "publication": r.get("publicationName") or "",
                    "date": r.get("date") or "",
                })
        return out

    return {
        "slug": slug,
        "mc_id": str(item.get("id") or ""),
        "title": item.get("title") or slug,
        "description": (item.get("description") or "").strip(),
        "developer": developer,
        "publisher": publisher,
        "release_date": item.get("releaseDate") or item.get("releaseDateText"),
        "premiere_year": str(item.get("premiereYear") or ""),
        "rating": item.get("rating"),
        "genres": ", ".join(g.get("name", "") for g in (item.get("genres") or []) if g.get("name")),
        "cover_url": cover,
        "video_url": video_url,
        "video_title": htmlmod.unescape(re.sub(r"<[^>]+>", "", video.get("title") or "")).strip() or None,
        "lead_platform": lead_platform,
        "metascore": (critic or {}).get("score") if (critic or {}).get("reviewCount") else None,
        "metascore_sentiment": (critic or {}).get("sentiment"),
        "metascore_reviews": (critic or {}).get("reviewCount"),
        "userscore": (user or {}).get("score") if (user or {}).get("reviewCount") else None,
        "userscore_sentiment": (user or {}).get("sentiment"),
        "user_reviews": (user or {}).get("reviewCount"),
        "platforms": platforms,
        "critic_quotes": pack_reviews(critic_reviews),
        "user_quotes": pack_reviews(user_reviews),
    }


def fetch_game(slug: str) -> dict:
    html = fetch(f"{BASE}/game/{slug}/")
    return parse_game_page(html, slug)


def platform_user_score(slug: str, platform_slug: str) -> dict | None:
    """Юзер-оценка игры на конкретной платформе (страница user-reviews?platform=)."""
    if not platform_slug:
        return None
    html = fetch(f"{BASE}/game/{slug}/user-reviews/?platform={platform_slug}")
    data = nuxt_json(html)
    for key, val in (data.get("data") or {}).items():
        if key.startswith("loadPage:games-user-reviews:") and isinstance(val, dict):
            for comp in val.get("components") or []:
                it = ((comp or {}).get("data") or {}).get("item")
                if isinstance(it, dict) and it.get("max") == 10 and "reviewCount" in it:
                    reviews = it.get("reviewCount") or 0
                    # при нуле отзывов Metacritic показывает score=0 - это не оценка
                    return {"userscore": it.get("score") if reviews else None,
                            "user_reviews": reviews}
    return None
