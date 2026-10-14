"""Разборщик __NUXT_DATA__ (devalue-формат Nuxt): плоский JSON-массив ->
обычная структура. Числа внутри объектов/массивов - это ссылки на индексы
верхнего уровня; литералы лежат сами по себе в верхнем массиве."""
import json
import re


def extract_payload(html: str):
    m = re.search(
        r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S
    )
    if not m:
        m = re.search(
            r'<script[^>]*data-nuxt-data="nuxt-app"[^>]*>(.*?)</script>', html, re.S
        )
    if not m:
        return None
    return json.loads(m.group(1))


def unflatten(arr):
    from functools import lru_cache

    sentinel = object()

    @lru_cache(maxsize=None)
    def resolve(i):
        v = arr[i]
        if isinstance(v, dict):
            return {k: resolve_ref(x) for k, x in v.items()}
        if isinstance(v, list):
            tag = v[0] if v and isinstance(v[0], str) else None
            if tag in ("ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef"):
                return resolve_ref(v[1]) if len(v) > 1 else None
            return [resolve_ref(x) for x in v]
        return v

    def resolve_ref(x):
        if isinstance(x, int) and not isinstance(x, bool) and 0 <= x < len(arr):
            return resolve(x)
        return x

    return resolve_ref(0)


def nuxt_json(html: str):
    arr = extract_payload(html)
    if arr is None:
        return None
    return unflatten(arr)
