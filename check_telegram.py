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
AUTO_WORDS = ("auto", "/auto")
SUGGEST_WORDS = ("trends", "/trends", "trending", "/trending", "news", "/news", "suggest", "/suggest")
IGNORE = {"yes", "y", "no", "n", "hi", "hii", "hello", "hey", "ok", "okay", "thanks", "thank you", "/start", "/help"}
WORKER_URL = (os.environ.get("WORKER_URL") or "").rstrip("/")
WORKER_API_KEY = os.environ.get("WORKER_API_KEY") or ""
IDLE = {"choosing", "idle", "posted", "failed", "expired", "cancelled", "posting", "waiting", None}


def tg(method, **params):
    return requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", data=params, timeout=60).json()


def get_updates(offset):
    """Reads messages from the Cloudflare Worker (instant mode) or straight from Telegram (polling mode).
    Problems are printed in the log AND sent to you on Telegram, so nothing fails silently."""
    if WORKER_URL:
        r = requests.get(f"{WORKER_URL}/updates", params={"offset": offset},
                         headers={"Authorization": f"Bearer {WORKER_API_KEY}"}, timeout=30)
        if r.status_code != 200:
            print(f"❌ Worker /updates returned {r.status_code}: {r.text[:200]}")
            say(f"⚠️ Worker-ൽ നിന്ന് സന്ദേശങ്ങൾ എടുക്കാൻ കഴിഞ്ഞില്ല (HTTP {r.status_code}). "
                "GitHub secret WORKER_API_KEY, Cloudflare API_KEY എന്നിവ ഒരുപോലെയാണോ എന്ന് നോക്കൂ.")
            return []
        return r.json().get("result", [])
    res = tg("getUpdates", offset=offset, timeout=0, allowed_updates=json.dumps(["message"]))
    if not res.get("ok"):
        print(f"❌ Telegram getUpdates failed: {res}")
        if "webhook" in str(res.get("description", "")).lower():
            say("⚠️ ടെലിഗ്രാമിൽ webhook സെറ്റ് ചെയ്തിരിക്കുന്നു, പക്ഷേ GitHub secrets-ൽ WORKER_URL, "
                "WORKER_API_KEY ഇല്ല. അവ ചേർക്കൂ (അല്ലെങ്കിൽ deleteWebhook ചെയ്യൂ).")
        return []
    return res.get("result", [])


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
    handled = set(state.get("handled", []))
    for u in updates:
        uid = u["update_id"]
        last_id = uid
        if uid in handled:
            continue
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(CHAT):
            continue
        text = (m.get("text") or "").strip()
        low = text.lower()
        is_media = bool(m.get("photo") or m.get("video") or
                        (m.get("document") or {}).get("mime_type", "").startswith(("image/", "video/")))

        # ---- a number picks one of the suggested trending topics ----
        suggestions = state.get("suggestions") or []
        picked = None
        if status == "choosing" and suggestions:
            num = low.rstrip(".)")
            if num.isdigit() and 1 <= int(num) <= len(suggestions):
                picked = suggestions[int(num) - 1]
            elif len(suggestions) == 1 and low in ("yes", "y", "ok", "okay"):
                picked = suggestions[0]
        if picked:
            state.update(status="planning", topic=picked["topic"], context=picked.get("context", ""),
                         offset=uid + 1, created=time.time())
            for k in ("scenes", "caption", "suggestions"):
                state.pop(k, None)
            save(state)
            return out("plan")

        # ---- command: show the latest trending topics right now ----
        if low in SUGGEST_WORDS:
            if status == "waiting_media":       # photos are being collected: don't overwrite anything
                say("⏳ ഒരു റീൽ ഫോട്ടോകൾക്കായി കാത്തിരിക്കുന്നു. ഫോട്ടോ അയച്ച് 'reel' അയക്കൂ, "
                    "അല്ലെങ്കിൽ 'cancel' അയച്ച ശേഷം 'trends' അയക്കൂ.")
                state["handled"] = (state.get("handled", []) + [uid])[-50:]
                save(state)
                continue
            state["offset"] = uid + 1
            save(state)
            return out("suggest")

        # ---- does this message start a new reel? ----
        new_topic = None
        if low.startswith("/new "):
            new_topic = text[5:].strip()
        elif (status in IDLE and text and not text.startswith("/") and low not in IGNORE
              and low not in REEL_WORDS and low not in AUTO_WORDS and low not in CANCEL_WORDS
              and low not in SUGGEST_WORDS
              and not low.rstrip(".)").isdigit()):
            new_topic = text
        if new_topic is not None:
            state.update(status="planning", topic=new_topic, context="", offset=uid + 1, created=time.time())
            for k in ("scenes", "caption", "suggestions"):
                state.pop(k, None)
            save(state)
            return out("plan")

        # ---- cancel also clears an open suggestion list ----
        if status == "choosing" and low in CANCEL_WORDS:
            state.update(status="cancelled", offset=uid + 1)
            state.pop("suggestions", None)
            save(state)
            say("🗑️ ലിസ്റ്റ് ഒഴിവാക്കി. അടുത്ത മണിക്കൂറിൽ പുതിയ നിർദ്ദേശങ്ങൾ വരും.")
            return out("none")

        # ---- reel already planned, waiting for photos ----
        if status == "waiting_media":
            if is_media:
                media_count += 1
            elif low in AUTO_WORDS:                      # use the bot's own keywords
                return out("publish")
            elif low in REEL_WORDS:
                if media_count > 0:
                    return out("publish")
                state["offset"] = uid + 1
                save(state)
                say("📸 കീവേഡോ ഫോട്ടോയോ ഒന്നും കിട്ടിയില്ല. അവ അയച്ച ശേഷം 'reel' എന്ന് ടൈപ്പ് ചെയ്യൂ. "
                    "ബോട്ടിന്റെ സ്വന്തം കീവേഡ് മതിയെങ്കിൽ 'auto' അയക്കൂ.")
            elif low in CANCEL_WORDS:
                state.update(status="cancelled", offset=uid + 1)
                save(state)
                say("🗑️ റദ്ദാക്കി. പുതിയ വിഷയം അയക്കാം.")
                return out("none")
            elif (text and not text.startswith("/") and low not in IGNORE
                  and not low.rstrip(".)").isdigit()):    # a keyword message
                media_count += 1

    if status in IDLE and last_id is not None:   # ignore chatter, don't re-read it every time
        state["offset"] = last_id + 1
        save(state)
    out("none")


if __name__ == "__main__":
    main()
