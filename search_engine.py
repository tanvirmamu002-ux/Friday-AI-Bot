import logging
import re

log = logging.getLogger(__name__)

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False
    log.warning("duckduckgo_search not installed — search disabled")

# ---------- NSFW / malicious filter ----------

NSFW_WORDS = {
    "porn","xxx","nude","naked","sex video","adult content",
    "hentai","onlyfans","escort","hack","crack","warez",
    "torrent piracy","malware","exploit","ransomware","phishing",
}

def is_safe_result(title: str, snippet: str, url: str) -> bool:
    combined = f"{title} {snippet} {url}".lower()
    return not any(w in combined for w in NSFW_WORDS)


# ---------- Auto-detect real-time search need ----------

_REALTIME_PATTERNS = [
    # Bengali time/current markers
    r"আজক[ের]+", r"এখন", r"বর্তমান", r"সর্বশেষ", r"তাজা",
    r"চলতি", r"হালনাগাদ", r"ব্রেকিং", r"নতুন খবর",
    # English time markers
    r"\btoday\b", r"\bnow\b", r"\bcurrent(ly)?\b", r"\blatest\b",
    r"\brecent(ly)?\b", r"\blive\b", r"\bbreaking\b", r"\bjust now\b",
    r"\bthis (week|month|year)\b", r"\bright now\b",
    # Year numbers 2024+
    r"\b202[4-9]\b", r"\b203\d\b",
    # Topic keywords
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
    """Return True if the query likely needs live web data."""
    return bool(_REALTIME_RE.search(text))


# ---------- Core search + summarise ----------

def web_search_and_summarize(query: str, generate_fn) -> str:
    """
    DuckDuckGo text search → feed results to Gemini for summarisation.
    generate_fn(prompt) must return a string AI response.
    """
    if not DDG_AVAILABLE:
        log.warning("DDG unavailable, falling back to AI memory")
        return generate_fn(query)

    raw_results = []
    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=8))
        log.info(f"DDG search '{query[:50]}' → {len(raw_results)} results")
    except Exception as e:
        log.warning(f"DuckDuckGo search failed: {e}")
        return generate_fn(query)

    safe = [
        r for r in raw_results
        if is_safe_result(r.get("title",""), r.get("body",""), r.get("href",""))
    ]

    if not safe:
        log.info("No safe DDG results; falling back to AI memory")
        return generate_fn(query)

    snippets = ""
    for i, r in enumerate(safe[:6], 1):
        title = r.get("title","")[:150]
        body  = r.get("body", "")[:300]
        href  = r.get("href","")[:100]
        snippets += f"{i}. [{title}]({href})\n   {body}\n\n"

    prompt = (
        "তুমি একটি AI assistant। নিচের real-time web search results ব্যবহার করে "
        "প্রশ্নের উত্তর দাও। পুরনো Gemini memory নয়, এই fresh results থেকে উত্তর দাও। "
        "NSFW বা ক্ষতিকর তথ্য বাদ দাও। সংক্ষিপ্ত, নির্ভুল ও তথ্যপূর্ণ উত্তর দাও।\n\n"
        f"=== WEB SEARCH RESULTS (real-time) ===\n{snippets}\n"
        f"=== QUESTION ===\n{query}"
    )
    return generate_fn(prompt)


def image_search(query: str, max_results: int = 10) -> list[dict]:
    """Return list of safe image results [{title, image, url}]."""
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
    """Return list of video results."""
    if not DDG_AVAILABLE:
        return []
    try:
        with DDGS() as ddgs:
            return list(ddgs.videos(query, max_results=max_results))
    except Exception as e:
        log.warning(f"DDG video search error: {e}")
        return []
