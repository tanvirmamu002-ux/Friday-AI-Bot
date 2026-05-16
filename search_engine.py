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


# ── Queries that must NEVER trigger search (keep fast) ────────────────────────

_SKIP_PATTERNS = [
    # Greetings / chitchat
    r"^(hello|hi|hey|হ্যালো|হাই|assalamu|salam|আসসালামু|সালাম)\b",
    r"^(how are you|আপনি কেমন|কেমন আছেন|কি খবর|কেমন চলছে)\b",
    r"^(good (morning|afternoon|evening|night)|শুভ (সকাল|বিকাল|সন্ধ্যা|রাত))\b",
    r"^(thanks|thank you|ধন্যবাদ|থ্যাংক|ok|okay|আচ্ছা|ঠিক আছে)\b",
    # Pure math / calculation
    r"\b(calculate|compute|simplify|factori[sz]e|differentiate|integrate)\b",
    r"\b(derivative|integral|equation|algebra|geometry|trigonometry)\b",
    r"^[\d\s\+\-\*\/\^\.\(\)]+[\=\?]",      # bare arithmetic like "2+2=?"
    # Code writing / debugging
    r"\b(write|create|generate|give me).{0,25}(code|function|script|program|snippet|class|loop)\b",
    r"\b(debug|fix|there('s| is) an? (error|bug)|syntax error).{0,25}(code|script|function)\b",
    r"\bhow to (code|program|implement) .{0,25}(in python|in javascript|in java|in c\+\+|in sql|in php)\b",
    # Storytelling / creative writing
    r"\b(write|compose|create|tell me).{0,30}(story|poem|essay|song|lyrics|গল্প|কবিতা|রচনা|গান)\b",
    # Translation (output is deterministic)
    r"\b(translate|অনুবাদ করো|অনুবাদ করুন|translate this)\b",
    # Definitions of immutable concepts
    r"\bwhat is (an? )?(photosynthesis|gravity|newton|einstein|theory of (relativity|evolution)|"
    r"pythagorean|quadratic formula|ohm.s law|boyle.s law|mendel)\b",
]
_SKIP_RE = re.compile("|".join(_SKIP_PATTERNS), re.IGNORECASE)


# ── Patterns that always require live search ───────────────────────────────────

_FORCE_PATTERNS = [
    # ── Temporal / news ──────────────────────────────────────────────────────
    r"আজক[ের]+", r"এখন", r"বর্তমান", r"সর্বশেষ", r"তাজা",
    r"চলতি", r"হালনাগাদ", r"ব্রেকিং", r"নতুন খবর",
    r"\btoday\b", r"\bnow\b", r"\bcurrent(ly)?\b", r"\blatest\b",
    r"\brecent(ly)?\b", r"\blive\b", r"\bbreaking\b", r"\bjust now\b",
    r"\bthis (week|month|year)\b", r"\bright now\b",
    r"\b202[4-9]\b", r"\b203\d\b",
    r"\bnews\b", r"খবর", r"সংবাদ",
    # ── Weather / environment ─────────────────────────────────────────────────
    r"weather|আবহাওয়া|তাপমাত্রা|বৃষ্টি|flood|বন্যা|earthquake|ভূমিকম্প",
    # ── Finance / markets ─────────────────────────────────────────────────────
    r"\bprice\b|দাম|রেট|মূল্য",
    r"\bdollar\b|euro|exchange rate|বিনিময়",
    r"\bstock\b|\bshare price\b|\bmarket cap\b|\bvaluation\b|\bnet worth\b",
    r"bitcoin|crypto|বাজার|শেয়ার",
    r"সম্পদ|কোটি টাকা",
    # ── Politics / government ─────────────────────────────────────────────────
    r"election|নির্বাচন|ভোট",
    r"\b(who is (the )?(current |new |present )?(president|prime minister|pm|minister|"
    r"governor|chancellor|secretary|senator|congressman|mp|mla))\b",
    r"\b(president|prime minister|প্রধানমন্ত্রী|রাষ্ট্রপতি) of\b",
    r"minister|মন্ত্রী|রাজনীতি|politics|সরকার|parliament|cabinet",
    # ── People — age / roles / net worth ─────────────────────────────────────
    r"\bhow old (is|was|are)\b",
    r"\bage of\b",
    r"কত বছর (বয়স|বয়েস|বয়সী)|বয়স কত|কত বয়সে",
    r"\b(ceo|cto|cfo|coo|chairman|founder|owner|director) of\b",
    r"\bwho (runs|leads|heads|owns|controls|founded)\b",
    r"\bwho (is|was) the (ceo|chairman|owner|founder|director|head|chief)\b",
    r"কে (প্রতিষ্ঠা করেন|পরিচালনা করেন|চেয়ারম্যান|সিইও|মালিক)",
    # "Who is [First Last]" — asking about a real public person (2+ word name)
    r"\bwho is [a-z]+ [a-z]+",
    # ── AI / tech models (always changing) ───────────────────────────────────
    r"\b(gpt-?\d|claude[\s-]?\d|gemini (pro|ultra|flash|1|2|3)|llama[\s-]?\d|"
    r"deepseek|grok|mistral|copilot)\b",
    r"\b(latest|newest|best|current).{0,20}(ai model|llm|language model|chatbot|version|update|release)\b",
    r"\b(iphone|samsung galaxy|pixel) \d+\b",
    # ── Rankings / statistics ─────────────────────────────────────────────────
    r"\b(rank(ing)?|ranked|top \d+|number \d+|#\d+)\b",
    r"\b(population|gdp|unemployment rate|inflation rate|literacy rate|birth rate|"
    r"poverty rate|growth rate)\b",
    r"জনসংখ্যা|জিডিপি|বেকারত্ব|মূল্যস্ফীতি|সাক্ষরতার হার",
    r"\b(richest|wealthiest|most powerful|top billionaire)\b",
    # ── Awards / records / sports ─────────────────────────────────────────────
    r"\b(who won|who is the winner|who is the champion|world record)\b",
    r"\b(oscar|grammy|nobel prize|pulitzer|fifa best|ballon d.or|man of the match)\b",
    r"\b(score|match result|standings|points table|league table)\b",
    r"match|score|খেলা|cricket|football|ক্রিকেট|ফুটবল",
    # ── Crisis / events ───────────────────────────────────────────────────────
    r"accident|দুর্ঘটনা|আগুন|war|যুদ্ধ|conflict|সংঘাত|attack|হামলা",
    r"covid|virus|pandemic|মহামারী",
]
_FORCE_RE = re.compile("|".join(_FORCE_PATTERNS), re.IGNORECASE)


def should_search(query: str) -> bool:
    """
    Return True if the query needs a live DuckDuckGo search.

    Decision order:
      1. If query matches _SKIP_RE  → False  (math / code / greetings / creative)
      2. If query matches _FORCE_RE → True   (temporal / entity / dynamic facts)
      3. Default                    → False  (normal AI conversation)
    """
    q = query.strip()
    if _SKIP_RE.search(q):
        return False
    return bool(_FORCE_RE.search(q))


# Kept for backward compatibility
def needs_realtime_search(text: str) -> bool:
    return should_search(text)


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
