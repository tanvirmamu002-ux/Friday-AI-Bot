# Friday AI — Advanced Telegram Bot

**Owner:** বোরহান (@hm_burhan) | **Bot:** @fridayaigpt5_bot  
**Engine:** Google Gemini (gemini-flash-lite-latest) + DuckDuckGo real-time search

## Run & Operate

- `python main.py` — run bot via "Telegram Bot" workflow
- Required secrets: `BOT_TOKEN`, `GEMINI_KEY_1`, `GEMINI_KEY_2`, `GEMINI_KEY_3`
- Optional env: `ADMIN_ID` (default: 8234592104), `LOG_GROUP_ID` (default: -1003848412289)

## Modular Architecture

```
main.py          — Entry point, all handlers, startup
database.py      — SQLite: users, ban, premium, topics, backup
ai_logic.py      — Gemini key rotation, session memory, config loader, prompt builder
search_engine.py — DuckDuckGo text/image/video search, NSFW filter, auto-detect
image_tools.py   — Image enhance (Pillow), download, safe send, temp file cleanup
keep_alive.py    — Flask server (/ /health /ping) for UptimeRobot
config/          — Owner-controlled behaviour files (reload with /reload)
```

## Config Files (config/)

| File | Controls |
|------|----------|
| `owner_identity.txt` | Bot name, credit, identity rules |
| `reply_tone.txt` | Language, formatting, personality |
| `forbidden_topics.txt` | Blocked content categories |
| `knowledge_base.txt` | Bot capabilities and custom facts |
| `rag_control.txt` | When to search, memory rules |
| `override_rules.txt` | Highest-priority rules, anti-jailbreak |

Edit any file → send `/reload` to bot → changes apply instantly (no restart).

## Features

### Real-Time Search (Auto-detect)
- Keywords like "আজকের", "latest", "news", "price", "weather", "election" etc. → auto-trigger DuckDuckGo
- Flow: DDG search → safe snippets → Gemini summarises with fresh data
- Prevents outdated hallucinated answers

### Commands
| Command | Who | Description |
|---------|-----|-------------|
| `/start` | All | Welcome + credit |
| `/status` | All | Daily usage, plan, remaining |
| `/search ‹q›` | All | Force real-time web search |
| `/yt ‹q›` | All | YouTube / video search |
| `/image ‹q›` | All | Image search (download-send fallback) |
| `/enhance` | All | Improve photo quality (sharpen/upscale) |
| `/clear` | All | Reset conversation memory |
| `/reload` | Admin | Reload config files without restart |
| `/ban [id]` | Admin | Ban user |
| `/unban [id]` | Admin | Unban user |
| `/premium [id]` | Admin | Grant premium (500/day) |
| `/limit [id] N` | Admin | Set daily limit |
| `/add_info [id] ‹text›` | Admin | Set custom info for AI context |
| `/set_policy [id] ‹text›` | Admin | Set AI behaviour policy per user |
| `/broadcast ‹text›` | Admin | Message all users |

### Admin Override
- Admin (MY_ID) bypasses all limits, rate limits, bans
- No daily cap, no cooldowns
- All admin commands work from user's forum topic (no need to type user_id)

### Memory & Context
- Per-user isolated session (last 10 message pairs)
- No cross-user personality contamination
- Config files inject global behaviour; per-user policy is strictly per-user

### Image Enhancement (/enhance)
- Sharpen via UnsharpMask
- Contrast + color boost
- 2× upscale for small images
- Structured for future Remini/API integration

### Forum Topic Logging
- Separate topic per user in LOG_GROUP_ID
- Full messages forwarded (split at 4096 chars)
- Text, captions, images, replies all supported

### Daily Limits
- DB tracks usage for all users
- Enforcement temporarily disabled (easy to re-enable: uncomment 3 lines in check_user_access)
- Premium users flagged (500/day limit stored)

## UptimeRobot Setup (Free 24/7)

Ping URL: `https://<your-replit-dev-domain>/health`  
Interval: 5 minutes  
Monitor type: HTTP(s)

## Deployment

- `deploymentTarget = "vm"` configured (always-on)
- `run_production.sh` starts both bot + api-server
- Build: `pnpm --filter @workspace/api-server run build` then `bash run_production.sh`

## User Preferences

- Owner credit: **বোরহান (@hm_burhan)** on all start/status messages
- Daily limits currently NOT enforced (tracking only)
- Real-time search auto-triggered by keyword detection
