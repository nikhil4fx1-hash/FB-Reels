"""Hourly trending-topic suggestions -> Telegram.
Needs only: requests, feedparser.  Run:  python suggest.py"""
import os
import re
import sys
import json
import time
import requests
import feedparser
from datetime import datetime, timedelta

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "pending.json"
BUSY = {"waiting_media", "planning", "posting"}   # a reel is in progress: don't disturb
MAX_TOPICS = 8
MAX_SEEN = 60
UA = {"User-Agent": "Mozilla/5.0"}


# ---------------- helpers ----------------
def tg_send(text):
    ok = True
    for i in range(0, len(text), 3800):
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          data={"chat_id": CHAT, "text": text[i:i + 3800]}, timeout=60).json()
        if not r.get("ok"):
            print(f"❌ Telegram send failed: {r}")
            ok = False
    return ok


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# ---------------- data sources (no API keys) ----------------
def fetch_google_trends():
    """Google Trends 'trending now' for India: what people are searching for right now."""
    urls = ["https://trends.google.com/trending/rss?geo=IN",
            "https://trends.google.com/trends/trendingsearches/daily/rss?geo=IN"]
    for u in urls:
        try:
            r = requests.get(u, headers=UA, timeout=30)
            feed = feedparser.parse(r.content)
            items = []
            for e in feed.entries[:25]:
                extra = " | ".join(x for x in (e.get("ht_approx_traffic", ""), e.get("ht_news_item_title", "")) if x)
                items.append(f"{e.title}" + (f"  ({extra})" if extra else ""))
            if items:
                return items
        except Exception as ex:
            print(f"   ⚠️ Trends {u}: {ex}")
    return []


def fetch_news():
    """Top headlines (Malayalam + India English) as extra signals of what is being talked about."""
    feeds = ["https://news.google.com/rss?hl=ml&gl=IN&ceid=IN:ml",
             "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"]
    out = []
    for u in feeds:
        try:
            feed = feedparser.parse(requests.get(u, headers=UA, timeout=30).content)
            out += [e.title for e in feed.entries[:12]]
        except Exception as ex:
            print(f"   ⚠️ News {u}: {ex}")
    return out


# ---------------- Gemini ----------------
def gemini_models():
    prefs = ['gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it']
    try:
        data = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}",
                            timeout=30).json()
        avail = [m['name'].split('/')[-1] for m in data.get('models', [])
                 if 'generateContent' in m.get('supportedGenerationMethods', [])]
    except Exception:
        avail = []
    return [m for m in prefs if m in avail] + [m for m in avail if m not in prefs]


def call_gemini(prompt):
    for model in gemini_models():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        for use_search in (True, False):
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            if use_search:
                payload["tools"] = [{"google_search": {}}]
            try:
                data = requests.post(url, json=payload, timeout=120).json()
                if "candidates" not in data:
                    continue
                parts = data["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts).strip()
                a, b = text.find("{"), text.rfind("}")
                if a >= 0 and b > a:
                    return json.loads(text[a:b + 1], strict=False)
            except Exception as e:
                print(f"   ⚠️ {model}: {e}")
    return None


def pick_topics(trends, news, seen):
    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    prompt = f"""
You help a Malayalam Facebook page that posts short news/explainer reels for Kerala and Indian audiences.
Current time (IST): {now_ist:%Y-%m-%d %H:%M}.

Use Google Search to check what is trending RIGHT NOW on social media and in the news (X/Twitter, YouTube,
Instagram, Facebook, Google Trends) in Kerala and India. These are extra signals already collected:

GOOGLE TRENDS INDIA:
{chr(10).join('- ' + t for t in trends[:25]) or '- (none)'}

TOP HEADLINES:
{chr(10).join('- ' + t for t in news[:24]) or '- (none)'}

TOPICS ALREADY SUGGESTED EARLIER (do not repeat them):
{chr(10).join('- ' + t for t in seen[-30:]) or '- (none)'}

Choose the {MAX_TOPICS} topics with the highest chance of getting views and opinions from Malayalam readers
(mix: Kerala, India, cricket/football, cinema, tech, viral stories). Skip tragedies with victims, communal or
hate-related topics, and unverified rumours.

Respond ONLY with one valid JSON object, no markdown:
{{"topics": [{{"topic": "short Malayalam title, max 10 words",
              "context": "one Malayalam line: what happened / why it is trending",
              "where": "where it is trending, e.g. Google Trends, X, YouTube, news"}}]}}
"""
    data = call_gemini(prompt)
    topics = []
    if data:
        for t in data.get("topics", []):
            if t.get("topic"):
                topics.append({"topic": t["topic"].strip(), "context": (t.get("context") or "").strip(),
                               "where": (t.get("where") or "").strip()})
    if not topics:   # Gemini failed: fall back to the raw Google Trends list
        print("⚠️ Gemini കിട്ടിയില്ല, Google Trends പട്ടിക നേരിട്ട് ഉപയോഗിക്കുന്നു")
        for t in trends:
            title = t.split("  (")[0].strip()
            topics.append({"topic": title, "context": t[len(title):].strip(" ()"), "where": "Google Trends"})
    fresh, used = [], set(norm(s) for s in seen)
    for t in topics:
        if norm(t["topic"]) not in used:
            used.add(norm(t["topic"]))
            fresh.append(t)
    return fresh[:MAX_TOPICS]


# ---------------- main ----------------
def main():
    if not TOKEN or not CHAT:
        print("❌ TELEGRAM secrets missing")
        sys.exit(1)
    manual = len(sys.argv) > 1 and sys.argv[1] == "manual"   # you asked for it on Telegram
    state = load_state() or {"status": "idle", "offset": 0}
    if state.get("status") in BUSY:
        print(f"ഒരു റീൽ പുരോഗമിക്കുന്നു ({state.get('status')}), നിർദ്ദേശങ്ങൾ ഒഴിവാക്കി.")
        if manual:
            tg_send("⏳ ഒരു റീൽ ഇപ്പോൾ തയ്യാറാകുന്നു. കഴിഞ്ഞ ശേഷം 'trends' വീണ്ടും അയക്കൂ.")
        return

    print("📡 ട്രെൻഡിങ് വിവരങ്ങൾ ശേഖരിക്കുന്നു...")
    trends, news = fetch_google_trends(), fetch_news()
    print(f"   trends: {len(trends)} | news: {len(news)}")
    seen = state.get("seen_topics", [])
    topics = pick_topics(trends, news, [] if manual else seen)   # on request: show everything again
    if not topics:
        print("പുതിയ വിഷയങ്ങളൊന്നുമില്ല.")
        if manual:
            tg_send("ഇപ്പോൾ ട്രെൻഡിങ് വിഷയങ്ങളൊന്നും കിട്ടിയില്ല. കുറച്ച് കഴിഞ്ഞ് വീണ്ടും 'trends' അയക്കൂ.")
        return

    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    if len(topics) == 1:
        t = topics[0]
        lines = [f"🔥 ട്രെൻഡിങ് ({now_ist:%H:%M})", "", f"1. {t['topic']}"]
        if t["context"]:
            lines.append(f"   ↳ {t['context']}")
        lines += ["", "ഇതിന്റെ റീൽ വേണമെങ്കിൽ  1  അല്ലെങ്കിൽ  yes  എന്ന് അയക്കൂ."]
    else:
        lines = [f"🔥 ട്രെൻഡിങ് വിഷയങ്ങൾ ({now_ist:%H:%M})", ""]
        for i, t in enumerate(topics, 1):
            lines.append(f"{i}. {t['topic']}")
            if t["context"]:
                lines.append(f"   ↳ {t['context']}")
        lines += ["", "റീൽ വേണ്ട വിഷയത്തിന്റെ നമ്പർ മാത്രം അയക്കൂ (ഉദാ: 3).",
                  "വേറെ വിഷയം വേണമെങ്കിൽ അത് ടൈപ്പ് ചെയ്ത് അയക്കൂ."]
    if not tg_send("\n".join(lines)):
        sys.exit(1)

    state["status"] = "choosing"
    state["suggestions"] = topics
    state["suggested_at"] = time.time()
    state["seen_topics"] = (seen + [t["topic"] for t in topics])[-MAX_SEEN:]
    save_state(state)
    print(f"✅ {len(topics)} വിഷയങ്ങൾ അയച്ചു.")


if __name__ == "__main__":
    main()
