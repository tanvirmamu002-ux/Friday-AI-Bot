import logging
import threading
from pathlib import Path
from collections import defaultdict

from google import genai

log = logging.getLogger(__name__)

MODEL_NAME       = "gemini-flash-lite-latest"
SESSION_MAX_PAIRS = 10
CONFIG_DIR        = Path("config")

# ---------- Gemini key rotation ----------

_gemini_keys  = []
_key_index    = 0
_key_lock     = threading.Lock()


def init_gemini(keys: list[str]):
    global _gemini_keys
    _gemini_keys = [k for k in keys if k]
    log.info(f"Gemini keys loaded: {len(_gemini_keys)}")


def generate_ai_response(prompt: str, image=None) -> str:
    global _key_index
    total = len(_gemini_keys)
    if not total:
        return "Gemini keys not configured."

    for _ in range(total):
        with _key_lock:
            idx     = _key_index
            api_key = _gemini_keys[idx]
        try:
            client   = genai.Client(api_key=api_key)
            contents = [prompt, image] if image else prompt
            resp     = client.models.generate_content(model=MODEL_NAME, contents=contents)
            text     = resp.text.strip() if resp.text else None
            if text:
                return text
        except Exception as e:
            log.warning(f"Gemini key {idx+1} failed: {e}")
            with _key_lock:
                _key_index = (_key_index + 1) % total

    return "দুঃখিত, AI সার্ভিস এখন unavailable। একটু পরে চেষ্টা করুন।"


# ---------- Config file loader ----------

def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def load_all_configs() -> dict:
    names = [
        "owner_identity", "override_rules", "forbidden_topics",
        "reply_tone", "knowledge_base", "rag_control",
    ]
    cfg = {}
    for name in names:
        cfg[name] = _read_file(CONFIG_DIR / f"{name}.txt")
    loaded = sum(1 for v in cfg.values() if v)
    log.info(f"Config files loaded: {loaded}/{len(names)}")
    return cfg


_configs      = {}
_config_lock  = threading.Lock()


def reload_configs():
    global _configs
    new = load_all_configs()
    with _config_lock:
        _configs = new
    return new


def get_configs() -> dict:
    with _config_lock:
        return dict(_configs)


# ---------- Per-user session memory ----------

_sessions      = defaultdict(list)
_session_lock  = threading.Lock()


def add_to_session(user_id: int, role: str, content: str):
    with _session_lock:
        sess = _sessions[user_id]
        sess.append({"role": role, "content": content})
        if len(sess) > SESSION_MAX_PAIRS * 2:
            _sessions[user_id] = sess[-(SESSION_MAX_PAIRS * 2):]


def clear_session(user_id: int):
    with _session_lock:
        _sessions[user_id] = []


def get_session(user_id: int) -> list:
    with _session_lock:
        return list(_sessions[user_id])


# ---------- Prompt builder ----------

def build_prompt(user_id: int, new_message: str, user_data: dict,
                 search_context: str | None = None) -> str:
    """
    Build full system + history + message prompt.
    search_context: raw web search snippets to inject (real-time data).
    """
    history = get_session(user_id)

    cfg = get_configs()
    sections = []

    if cfg.get("owner_identity"):
        sections.append(f"=== OWNER & IDENTITY ===\n{cfg['owner_identity']}")
    if cfg.get("override_rules"):
        sections.append(f"=== OVERRIDE RULES (highest priority) ===\n{cfg['override_rules']}")
    if cfg.get("forbidden_topics"):
        sections.append(f"=== FORBIDDEN TOPICS ===\n{cfg['forbidden_topics']}")
    if cfg.get("reply_tone"):
        sections.append(f"=== REPLY TONE ===\n{cfg['reply_tone']}")
    if cfg.get("knowledge_base"):
        sections.append(f"=== KNOWLEDGE BASE ===\n{cfg['knowledge_base']}")

    # Per-user DB overrides (admin-set) — strictly isolated to this user
    policy      = (user_data or {}).get("policy", "").strip()
    custom_info = (user_data or {}).get("custom_info", "").strip()
    if policy:
        sections.append(f"=== USER POLICY (admin-set for user {user_id}) ===\n{policy}")
    if custom_info:
        sections.append(f"=== USER INFO (admin-set for user {user_id}) ===\n{custom_info}")

    system_prompt = "\n\n".join(sections) if sections else \
        "তুমি একটি বুদ্ধিমান AI assistant। বাংলা ও ইংরেজি উভয়ে উত্তর দিতে পারো।"

    # Inject real-time search results
    if search_context:
        system_prompt += (
            "\n\n=== REAL-TIME WEB SEARCH RESULTS ==="
            "\nনিচের live results ব্যবহার করো। পুরনো Gemini memory নয়, এই fresh data থেকে উত্তর দাও।"
            f"\n{search_context}"
        )

    # Conversation history
    history_lines = []
    for msg in history[-(SESSION_MAX_PAIRS * 2):]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history_lines.append(f"{role}: {msg['content']}")

    if history_lines:
        history_block = "\n".join(history_lines)
        return f"{system_prompt}\n\n--- Conversation ---\n{history_block}\n\nUser: {new_message}\nAssistant:"
    else:
        return f"{system_prompt}\n\nUser: {new_message}\nAssistant:"
