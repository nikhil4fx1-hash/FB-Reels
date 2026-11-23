"""Lightweight Telegram listener (needs only `requests`).
Reads your new Telegram messages and tells the workflow what to do: plan | publish | none."""
import os
import json
import time
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "pending.json"
EXPIRE_HOURS = 24
REEL_WORDS = ("reel", "/reel", "post", "/post", "done", "/done")
CANCEL_WORDS = ("cancel", "/cancel")
TRENDING_WORDS = ("trending", "/trending")
IGNORE = {"hi", "hii", "hello", "hey", "ok", "okay", "thanks", "thank you", "/start", "/help"}
WORKER_URL = (os.environ.get("WORKER_URL") or "").rstrip("/")
WORKER_API_KEY = os.environ.get("WORKER_API_KEY") or ""
IDLE = {"idle", "posted", "failed", "expired", "cancelled", "posting", "waiting", None}


def tg(method, **params):
    return requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", data=params, timeout=60).json()


def get_updates(offset):
    """Reads messages from the Cloudflare Worker (instant mode) or straight from Telegram (polling mode)."""
    if WORKER_URL:
        r = requests.get(f"{WORKER_URL}/updates", params={"offset": offset},
                         headers={"Authorization": f"Bearer {WORKER_API_KEY}"}, timeout=30)
        return r.json().get("result", [])
    return tg("getUpdates", offset=offset, timeout=0,
              allowed_updates=json.dumps(["message"])).get("result", [])


def say(text):
    tg("sendMessage", chat_id=CHAT, text=text)


def save(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def out(action):
    print(f"action={action}")
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"action={action}\n")


def main():
    if not TOKEN or not CHAT:
        print("Telegram secrets missing")
        return out("none")
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {"status": "idle", "offset": 0}
    status = state.get("status")

    if status == "waiting_media" and time.time() - state.get("created", 0) > EXPIRE_HOURS * 3600:
        state["status"] = status = "expired"
        save(state)
        say("⌛ 24 മണിക്കൂർ കഴിഞ്ഞു, ആ റീൽ ഒഴിവാക്കി. പുതിയ വിഷയം അയക്കാം.")

    if status == "planning":          # an earlier plan run did not finish: retry
        return out("plan")

    updates = get_updates(state.get("offset", 0))
    media_count, last_id = 0, None
    for u in updates:
        uid = u["update_id"]
        last_id = uid
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(CHAT):
            continue
        text = (m.get("text") or "").strip()
        low = text.lower()
        is_media = bool(m.get("photo") or m.get("video") or
                        (m.get("document") or {}).get("mime_type", "").startswith(("image/", "video/")))

        # ---- does this message start a new reel? ----
        new_topic = None
        if low in TRENDING_WORDS:
            new_topic = ""
        elif low.startswith("/new "):
            new_topic = text[5:].strip()
        elif (status in IDLE and text and not text.startswith("/") and low not in IGNORE
              and low not in REEL_WORDS and low not in CANCEL_WORDS):
            new_topic = text
        if new_topic is not None:
            state.update(status="planning", topic=new_topic, offset=uid + 1, created=time.time())
            for k in ("scenes", "caption"):
                state.pop(k, None)
            save(state)
            return out("plan")

        # ---- reel already planned, waiting for photos ----
        if status == "waiting_media":
            if is_media:
                media_count += 1
            elif low in REEL_WORDS:
                if media_count > 0:
                    return out("publish")
                state["offset"] = uid + 1
                save(state)
                say("📸 ഫോട്ടോ ഒന്നും കിട്ടിയില്ല. ഫോട്ടോകൾ അയച്ച ശേഷം 'reel' എന്ന് ടൈപ്പ് ചെയ്യൂ.")
            elif low in CANCEL_WORDS:
                state.update(status="cancelled", offset=uid + 1)
                save(state)
                say("🗑️ റദ്ദാക്കി. പുതിയ വിഷയം അയക്കാം.")
                return out("none")

    if status in IDLE and last_id is not None:   # ignore chatter, don't re-read it every time
        state["offset"] = last_id + 1
        save(state)
    out("none")


if __name__ == "__main__":
    main()
