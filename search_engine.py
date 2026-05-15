"""
search_engine.py — DuckDuckGo search, auto-detection, sanitization
"""

import re
import logging

log = logging.getLogger(__name__)

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False
    log.warning("duckduckgo_search not installed — search disabled")


# ── NSFW / unsafe keyword filter ──────────────────────────────────────────────

_NSFW_WORDS = frozenset({
    "porn", "xxx", "nude", "naked", "sex video", "adult content",
    "hentai", "onlyfans", "escort", "hack tutorial", "crack software",
    "warez", "malware", "exploit kit", "ransomware", "phishing kit",
})

def is_safe_result(title: str, snippet: str, url: str) -> bool:
    combined = f"{title} {snippet} {url}".lower()
    return not any(w in combined for w in _NSFW_WORDS)


# ── Prompt-injection sanitizer ────────────────────────────────────────────────

# Patterns that could hijack the Gemini prompt if present in search snippets
_INJECTION_RE = re.compile(
    r"(ignore (all |previous |prior )?instructions?|"
    r"system prompt|you are now|act as (a |an )?|"
    r"forget (everything|all)|new (role|persona|instructions?)|"
    r"===|---\s*(system|user|assistant)|"
    r"\[INST\]|\[/INST\]|<\|im_start\|>|<\|im_end\|>)",
    re.IGNORECASE,
)

def sanitize_snippet(text: str) -> str:
    """Strip potential prompt-injection patterns from a search snippet."""
    lines = text.splitlines()
    clean = [ln for ln in lines if not _INJECTION_RE.search(ln)]
    return " ".join(clean).strip()


# ── Auto-detect real-time search need ─────────────────────────────────────────

_REALTIME_PATTERNS = [
    r"আজক[ের]+", r"এখন", r"বর্তমান", r"সর্বশেষ", r"তাজা",
    r"চলতি", r"হালনাগাদ", r"ব্রেকিং", r"নতুন খবর",
    r"\btoday\b", r"\bnow\b", r"\bcurrent(ly)?\b", r"\blatest\b",
    r"\brecent(ly)?\b", r"\blive\b", r"\bbreaking\b", r"\bjust now\b",
    r"\bthis (week|month|year)\b", r"\bright now\b",
    r"\b202[4-9]\b", r"\b203\d\b",
    r"\bnews\b", r"খবর", r"সংবাদ",
    r"weather|আবহাওয়া|তাপমাত্রা|বৃষ্টি",
    r"price|দাম|রেট|মূল্য",
    r"dollar|euro|টাকা|exchange rate|বিনিময়",
    r"election|নির্বাচন|ভোট",
    r"president|prime minister|প্রধানমন্ত্রী|রাষ্ট্রপতি|সরকার",
    r"minister|মন্ত্রী|রাজনীতি|politics",
    r"match|score|খেলা|cricket|football|ক্রিকেট|ফুটবল",
    r"stock|শেয়ার|bitcoin|crypto|বাজার",
    r"accident|দুর্ঘটনা|আগুন|flood|বন্যা|earthquake|ভূমিকম্প",
    r"covid|virus|pandemic|মহামারী",
    r"war|যুদ্ধ|conflict|সংঘাত|attack|হামলা",
]
_REALTIME_RE = re.compile("|".join(_REALTIME_PATTERNS), re.IGNORECASE)


def needs_realtime_search(text: str) -> bool:
    return bool(_REALTIME_RE.search(text))


# ── Core search + summarise ───────────────────────────────────────────────────

def web_search_and_summarize(query: str, generate_fn) -> str:
    """
    DuckDuckGo → sanitize results → Gemini summarise.
    Falls back to Gemini memory if DDG fails.
    """
    if not DDG_AVAILABLE:
        log.warning("DDG unavailable, using Gemini memory")
        return generate_fn(query)

    raw = []
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=10))
        log.info(f"DDG '{query[:50]}' → {len(raw)} results")
    except Exception as e:
        log.warning(f"DDG failed: {e}")
        return generate_fn(query)

    # Filter unsafe + sanitize injection patterns
    safe = []
    for r in raw:
        title   = r.get("title", "")[:200]
        body    = r.get("body",  "")[:400]
        href    = r.get("href",  "")[:120]
        if not is_safe_result(title, body, href):
            continue
        clean_body = sanitize_snippet(body)
        if clean_body:
            safe.append({"title": title, "body": clean_body, "href": href})

    if not safe:
        log.info("No safe DDG results — falling back to Gemini memory")
        return generate_fn(query)

    snippets = ""
    for i, r in enumerate(safe[:6], 1):
        snippets += f"{i}. {r['title']}\n   {r['body']}\n   {r['href']}\n\n"

    prompt = (
        "তুমি একটি AI assistant। নিচের real-time web search results ব্যবহার করে "
        "প্রশ্নের সংক্ষিপ্ত ও নির্ভুল উত্তর দাও। পুরনো memory নয়, এই fresh data ব্যবহার করো।\n\n"
        f"Search Results:\n{snippets}\n"
        f"Question: {query}"
    )
    return generate_fn(prompt)


def image_search(query: str, max_results: int = 12) -> list[dict]:
    if not DDG_AVAILABLE:
        return []
    try:
        with DDGS() as ddgs:
            imgs = list(ddgs.images(query, max_results=max_results, safesearch="moderate"))
        return [
            i for i in imgs
            if is_safe_result(i.get("title",""), "", i.get("image",""))
        ]
    except Exception as e:
        log.warning(f"DDG image search error: {e}")
        return []


def video_search(query: str, max_results: int = 5) -> list[dict]:
    if not DDG_AVAILABLE:
        return []
    try:
        with DDGS() as ddgs:
            return list(ddgs.videos(query, max_results=max_results))
    except Exception as e:
        log.warning(f"DDG video search error: {e}")
        return []
