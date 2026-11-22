"""Lightweight poller (needs only `requests`). Tells the workflow whether the photos have arrived."""
import os
import json
import time
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "pending.json"
EXPIRE_HOURS = 24
POST_WORDS = ("post", "/post", "done", "/done")


def tg(method, **params):
    return requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", data=params, timeout=60).json()


def set_output(ready):
    line = f"ready={'true' if ready else 'false'}\n"
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(line)
    print(line.strip())


def main():
    if not TOKEN or not CHAT:
        print("Telegram secrets missing")
        return set_output(False)
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        print("pending.json ഇല്ല")
        return set_output(False)
    if state.get("status") != "waiting":
        print("കാത്തിരിക്കുന്ന റീൽ ഇല്ല:", state.get("status"))
        return set_output(False)

    if time.time() - state.get("created", 0) > EXPIRE_HOURS * 3600:
        state["status"] = "expired"
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        tg("sendMessage", chat_id=CHAT, text="⌛ 24 മണിക്കൂർ കഴിഞ്ഞു, ഈ റീൽ ഒഴിവാക്കി. അടുത്തത് ഉടൻ വരും.")
        return set_output(False)

    res = tg("getUpdates", offset=state.get("offset", 0), timeout=0,
             allowed_updates=json.dumps(["message"]))
    count, post_now = 0, False
    for u in res.get("result", []):
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(CHAT):
            continue
        doc_ok = (m.get("document") or {}).get("mime_type", "").startswith(("image/", "video/"))
        if m.get("photo") or m.get("video") or doc_ok:
            count += 1
        if (m.get("text") or "").strip().lower() in POST_WORDS:
            post_now = True

    need = len(state.get("scenes", []))
    print(f"ലഭിച്ചത്: {count}/{need} | post കമാൻഡ്: {post_now}")
    set_output(count >= need or (post_now and count > 0))


if __name__ == "__main__":
    main()
