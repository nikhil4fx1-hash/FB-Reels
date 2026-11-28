import os
import random
import requests
import feedparser
import urllib.parse
import asyncio
import io
import time
import sys
import json
import base64
import re
import shutil
from PIL import Image, ImageOps, ImageFilter

# === MOVIEPY / PILLOW FIX ===
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
# ============================

import edge_tts
from moviepy.editor import (ImageClip, VideoFileClip, AudioFileClip, CompositeAudioClip,
                            CompositeVideoClip, concatenate_videoclips, concatenate_audioclips)
from moviepy.video.fx.all import crop, loop

# --- Environment Variables ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
HF_API_KEY = os.environ.get("HF_API_KEY")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY") if os.environ.get("USE_ELEVENLABS") == "1" else None
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL") or "eleven_v3"
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID") or "pNInz6obpgDQGcFmaJgB"
EDGE_VOICE = os.environ.get("EDGE_VOICE") or "ml-IN-MidhunNeural"  # or ml-IN-SobhanaNeural

FB_API = "https://graph.facebook.com/v18.0"
UA = {"User-Agent": "Mozilla/5.0"}
COMMONS_UA = {"User-Agent": "ReelsBot/1.0 (personal project)"}
W, H = 1080, 1920
MAX_REEL_SECONDS = 88
USED_VIDEO_IDS = set()
USED_IMG_URLS = set()
NEWS_LINK = {"url": None}
WIKI_CACHE = {}
USE_NEWS_IMAGES = os.environ.get("USE_NEWS_IMAGES") == "1"


# ======================================================================
# Facebook connection check
# ======================================================================
def check_facebook_permissions():
    print("\n[1] ഫേസ്ബുക്ക് കണക്ഷൻ പരിശോധിക്കുന്നു...")
    if not FB_PAGE_ACCESS_TOKEN:
        print("❌ FB_PAGE_ACCESS_TOKEN നൽകിയിട്ടില്ല!")
        return False
    try:
        res = requests.get(f"{FB_API}/me",
                           params={"fields": "name", "access_token": FB_PAGE_ACCESS_TOKEN},
                           timeout=30).json()
        if 'name' in res:
            print(f"✅ പേജ് കണക്ട് ആയി: {res['name']}")
            return True
        print(f"❌ FB error: {res}")
    except Exception as e:
        print(f"❌ FB exception: {e}")
    return False


# ======================================================================
# Topic
# ======================================================================
def get_trending_topic():
    print("\n[2] ട്രെൻഡിങ് വിഷയം തിരയുന്നു...")
    keywords = ["കേരളം", "ഇന്ത്യൻ ഫുട്ബോൾ", "സഞ്ജു സാംസൺ", "ടെക്നോളജി", "സിനിമ",
                "കേരള രാഷ്ട്രീയം", "ദുബായ് വാർത്തകൾ", "വിചിത്രമായ വാർത്തകൾ"]
    q = urllib.parse.quote(random.choice(keywords))
    rss_url = f"https://news.google.com/rss/search?q={q}&hl=ml&gl=IN&ceid=IN:ml"
    try:
        feed = feedparser.parse(requests.get(rss_url, timeout=30).content)
        if feed.entries:
            entry = random.choice(feed.entries[:5])
            topic = entry.title
            NEWS_LINK["url"] = entry.get("link")
            print(f"✅ വിഷയം: {topic}")
            return topic
    except Exception:
        pass
    return "ഇന്നത്തെ പ്രധാന വാർത്തകൾ"


# ======================================================================
# Gemini: scenes (narration + keywords + image prompt)
# ======================================================================
def get_available_models():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    out = []
    try:
        for m in requests.get(url, timeout=30).json().get('models', []):
            if 'generateContent' in m.get('supportedGenerationMethods', []):
                out.append(m['name'].split('/')[-1])
    except Exception:
        pass
    return out


def research_dossier(topic, models):
    """Pass 1: a long, detailed fact sheet about the topic (uses Google Search several times)."""
    prompt = f"""
Research this news topic thoroughly with Google Search (several searches, several different recent sources):
'{topic}'

Write a detailed FACT DOSSIER in plain text (no JSON), 500 to 900 words. Include everything a reader needs to
understand the story completely:
- exactly what happened, step by step (timeline), who / where / when
- all exact numbers, names, places, dates, amounts
- ALL the reasons and causes, each one explained (the "why"), and how it works (the "how")
- background and earlier related events
- official statements and the reaction of each side (supporters, critics, experts, public)
- impact on ordinary people and what happens next
- at the end: "SOURCES:" followed by the names of the publications/sites you used
Use only facts you actually found. Mark anything unconfirmed as "unconfirmed". Never invent anything.
"""
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}],
                   "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192}}
        try:
            data = requests.post(url, json=payload, timeout=180).json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
            if len(text) > 400:
                print(f"   📚 ഗവേഷണ കുറിപ്പ് തയ്യാർ ({len(text)} അക്ഷരം)")
                return text
        except Exception as e:
            print(f"   ⚠️ research {model}: {str(e)[:100]}")
    return None


def get_gemini_scenes(topic):
    """Returns (scenes, caption). Two passes: (1) deep research dossier with Google Search,
    (2) script + long caption written strictly from that dossier."""
    available = get_available_models()
    prefs = ['gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it']
    models = [m for m in prefs if m in available] + [m for m in available if m not in prefs]

    print("\n[1/2] വിഷയത്തെക്കുറിച്ച് ആഴത്തിൽ ഗവേഷണം ചെയ്യുന്നു...")
    dossier = research_dossier(topic, models)
    research_part = (f"RESEARCH DOSSIER (use ONLY these facts):\n{dossier}\n" if dossier else
                     "Use Google Search several times first and collect the complete story.\n")

    prompt = f"""
You are an expert Malayalam news-explainer Reels creator for a Facebook page.
Topic given by the page owner: '{topic}'

{research_part}
RULES FOR THE NEWS CONTENT
- Use only the facts above. Never invent numbers, quotes, names or dates.
  If something is unconfirmed say "റിപ്പോർട്ടുകൾ പ്രകാരം". Keep facts separate from opinions. Be fair and non-defamatory.
- The news must be COMPLETE: who, what, when, where, every number, ALL the reasons (why) and how it works,
  background, reactions of each side, what happens next. A reader must understand the whole story from the post alone.
- NEVER tease or ask a "why/how" question inside the news without answering it immediately in the same scene or
  paragraph. No cliffhangers, no vague filler such as "വലിയ ചർച്ചയായി" without saying what exactly was discussed.

STEP - Respond ONLY with one valid JSON object (no markdown fences, nothing outside the JSON).
Inside JSON strings write line breaks as \\n.

"key_facts": 8 to 12 short Malayalam strings with the most important concrete facts (who/what/when/where/numbers/reasons).

"scenes": 6 to 8 scenes for a ~60 second Malayalam voiceover. Each scene has:
  - "narration": 1-2 short spoken-style Malayalam sentences carrying REAL information
    (a fact, number or reason in every scene).
    Scene 1: a strong hook that already states the key fact.
    Last scene: one sentence with the conclusion, then ONE natural question about this story that makes viewers
    want to give their own opinion (specific to this story, spoken in a flowing, friendly way).
  - "keywords": 2-3 simple English words for stock footage matching THAT narration
  - "image_prompt": detailed English prompt for an AI image matching that scene
  - "wiki_title": exact English Wikipedia article title of the MAIN real person, team, place,
    organisation or event of that scene (e.g. "Sanju Samson", "Kerala"). Use "" if generic.

"caption": the Facebook post text in Malayalam, a complete mini news article, 1700 to 2000 characters in total
  (hashtags included, never more than 2000):
  - Line 1: headline with one emoji that states the core fact
  - Paragraph: what happened (who / what / when / where) with all key numbers
  - Paragraph: WHY it happened, every reason and cause clearly explained
  - Paragraph: how it works / important details people usually miss
  - Paragraph: background and earlier events
  - Paragraph: reactions and the different sides of the issue
  - Paragraph: what may happen next and how it affects ordinary people
  - ENDING (after ALL the details above): 1-2 sentences that bring readers into the discussion with a question or
    dilemma that comes naturally out of THIS story, in a warm conversational tone, so people feel like sharing their
    own view in the comments. It must be different for every post and specific to the story. Do NOT use a generic
    stock line such as "നിങ്ങളുടെ അഭിപ്രായം കമന്റ് ചെയ്യൂ", do not sound forced or begging, and never ask for
    likes, shares or tags. The ending question must never replace details: the news must already be complete.
  - 5 to 8 relevant hashtags at the very end

"sources": names of 2 to 4 publications or sites you used.

Exact structure:
{{"key_facts": ["..."],
  "scenes": [{{"narration": "...", "keywords": "...", "image_prompt": "...", "wiki_title": "..."}}],
  "caption": "...", "sources": ["..."]}}
"""
    print("[2/2] സ്ക്രിപ്റ്റും ക്യാപ്ഷനും എഴുതുന്നു...")
    for model in models:
        print(f"\n{model} ഉപയോഗിച്ച് എഴുതുന്നു...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        for use_search in ((False,) if dossier else (True, False)):
            payload = {"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.5, "maxOutputTokens": 8192}}
            if use_search:
                payload["tools"] = [{"google_search": {}}]
            try:
                data = requests.post(url, json=payload, timeout=180).json()
                if 'candidates' not in data:
                    print(f"   ⚠️ {'search' if use_search else 'plain'}: {str(data)[:150]}")
                    continue
                parts = data['candidates'][0]['content']['parts']
                text = "".join(p.get("text", "") for p in parts).strip()
                a, b = text.find("{"), text.rfind("}")
                if a < 0 or b < 0:
                    continue
                parsed = json.loads(text[a:b + 1], strict=False)
                scenes = [s for s in parsed.get("scenes", []) if s.get("narration")]
                caption = (parsed.get("caption") or "").strip()
                sources = [x for x in parsed.get("sources", []) if isinstance(x, str) and x.strip()]
                if sources:
                    src_line = "\n\nഉറവിടം: " + ", ".join(sources[:4])
                    if len(caption) + len(src_line) <= 2200:
                        caption += src_line
                if len(scenes) >= 3 and caption:
                    print(f"✅ {len(scenes)} സീനുകൾ ലഭിച്ചു! (ക്യാപ്ഷൻ {len(caption)} അക്ഷരം)")
                    return scenes, caption
            except Exception as e:
                print(f"   ⚠️ {e}")
    return [], None


# ======================================================================
# Visual sources
# ======================================================================
def save_fitted(img_bytes, filename):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    if img.width / img.height > 1.2:
        # landscape: blurred full-screen backdrop + whole photo in the middle
        bg = ImageOps.fit(img, (W, H), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(40))
        fg = img.resize((W, int(img.height * W / img.width)), Image.Resampling.LANCZOS)
        bg.paste(fg, (0, (H - fg.height) // 2))
        img = bg
    else:
        img = ImageOps.fit(img, (W, H), Image.Resampling.LANCZOS)
    img.save(filename, quality=92)


def pexels_video(keywords, filename):
    if not PEXELS_API_KEY:
        return False
    try:
        res = requests.get("https://api.pexels.com/videos/search",
                           headers={"Authorization": PEXELS_API_KEY},
                           params={"query": keywords, "orientation": "portrait", "per_page": 15},
                           timeout=30).json()
        videos = [v for v in res.get("videos", []) if ("px", v["id"]) not in USED_VIDEO_IDS]
        random.shuffle(videos)
        for v in videos:
            files = [f for f in v.get("video_files", [])
                     if f.get("file_type") == "video/mp4" and f.get("height", 0) >= f.get("width", 0)]
            if not files:
                continue
            files.sort(key=lambda f: abs(f["width"] - 1080))
            data = requests.get(files[0]["link"], timeout=120).content
            with open(filename, "wb") as fh:
                fh.write(data)
            USED_VIDEO_IDS.add(("px", v["id"]))
            return True
    except Exception as e:
        print(f"   ⚠️ Pexels video: {e}")
    return False


def pixabay_video(keywords, filename):
    if not PIXABAY_API_KEY:
        return False
    try:
        res = requests.get("https://pixabay.com/api/videos/",
                           params={"key": PIXABAY_API_KEY, "q": keywords[:100],
                                   "per_page": 20, "safesearch": "true"},
                           timeout=30).json()
        hits = [h for h in res.get("hits", []) if ("pb", h["id"]) not in USED_VIDEO_IDS]
        random.shuffle(hits)
        for h in hits:
            vids = h.get("videos", {})
            info = vids.get("medium") or vids.get("small") or vids.get("large")
            if not info or not info.get("url"):
                continue
            data = requests.get(info["url"], timeout=120).content
            with open(filename, "wb") as fh:
                fh.write(data)
            USED_VIDEO_IDS.add(("pb", h["id"]))
            return True
    except Exception as e:
        print(f"   ⚠️ Pixabay video: {e}")
    return False


def commons_video(keywords, filename):
    """No API key needed. Returns the saved path (webm/ogv) or None."""
    try:
        res = requests.get(
            "https://commons.wikimedia.org/w/api.php", headers=COMMONS_UA, timeout=30,
            params={"action": "query", "format": "json", "generator": "search",
                    "gsrsearch": f"{keywords} filetype:video", "gsrnamespace": 6,
                    "gsrlimit": 15, "prop": "imageinfo", "iiprop": "url|mime|size"}).json()
        pages = list(res.get("query", {}).get("pages", {}).values())
        random.shuffle(pages)
        for p in pages:
            info = (p.get("imageinfo") or [{}])[0]
            size = info.get("size", 0)
            if not info.get("url") or size > 25_000_000 or size < 100_000:
                continue
            if ("cm", p["pageid"]) in USED_VIDEO_IDS:
                continue
            r = requests.get(info["url"], headers=COMMONS_UA, timeout=120)
            if r.status_code == 200:
                ext = ".ogv" if "ogg" in info.get("mime", "") else ".webm"
                path = filename.replace(".mp4", ext)
                with open(path, "wb") as f:
                    f.write(r.content)
                USED_VIDEO_IDS.add(("cm", p["pageid"]))
                return path
    except Exception as e:
        print(f"   ⚠️ Commons video: {e}")
    return None


def pexels_photo(keywords, filename):
    if not PEXELS_API_KEY:
        return False
    try:
        res = requests.get("https://api.pexels.com/v1/search",
                           headers={"Authorization": PEXELS_API_KEY},
                           params={"query": keywords, "orientation": "portrait", "per_page": 10},
                           timeout=30).json()
        photos = res.get("photos", [])
        if photos:
            url = random.choice(photos)["src"]["large2x"]
            save_fitted(requests.get(url, timeout=60).content, filename)
            return True
    except Exception as e:
        print(f"   ⚠️ Pexels photo: {e}")
    return False


def commons_photo(keywords, filename):
    """No API key needed. Real photos from Wikimedia Commons."""
    try:
        res = requests.get(
            "https://commons.wikimedia.org/w/api.php", headers=COMMONS_UA, timeout=30,
            params={"action": "query", "format": "json", "generator": "search",
                    "gsrsearch": f"{keywords} filetype:bitmap", "gsrnamespace": 6,
                    "gsrlimit": 15, "prop": "imageinfo", "iiprop": "url|mime",
                    "iiurlwidth": 1200}).json()
        pages = list(res.get("query", {}).get("pages", {}).values())
        random.shuffle(pages)
        for p in pages:
            info = (p.get("imageinfo") or [{}])[0]
            if info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            r = requests.get(url, headers=COMMONS_UA, timeout=60)
            if r.status_code == 200:
                save_fitted(r.content, filename)
                return True
    except Exception as e:
        print(f"   ⚠️ Commons photo: {e}")
    return False


def try_pollinations(prompt, filename):
    q = urllib.parse.quote(f"{prompt}, hyper-realistic, vertical 9:16")
    url = f"https://image.pollinations.ai/prompt/{q}?width={W}&height={H}&nologo=true&seed={random.randint(1, 99999)}"
    try:
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            save_fitted(r.content, filename)
            return True
        print(f"      Pollinations status {r.status_code}")
    except Exception:
        pass
    return False


def try_huggingface(prompt, filename):
    if not HF_API_KEY:
        return False
    url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {HF_API_KEY}", "Accept": "image/png"},
                          json={"inputs": f"{prompt}, vertical 9:16, detailed"}, timeout=90)
        if r.status_code == 200:
            save_fitted(r.content, filename)
            return True
        print(f"      HF status {r.status_code}")
    except Exception:
        pass
    return False


def try_gemini_image(prompt, filename):
    if not GEMINI_API_KEY:
        return False
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": f"{prompt}. Vertical 9:16 composition."}]}],
               "generationConfig": {"responseModalities": ["IMAGE"]}}
    try:
        data = requests.post(url, json=payload, timeout=90).json()
        for c in data.get("candidates", []):
            for p in c.get("content", {}).get("parts", []):
                inline = p.get("inlineData") or p.get("inline_data")
                if inline and inline.get("data"):
                    save_fitted(base64.b64decode(inline["data"]), filename)
                    return True
    except Exception:
        pass
    return False


def make_gradient(filename):
    c1 = tuple(random.randint(20, 120) for _ in range(3))
    c2 = tuple(random.randint(100, 230) for _ in range(3))
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        row = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = row
    img.save(filename)


BAD_FILE_WORDS = ("logo", "icon", "flag", "map", "symbol", "signature", "commons-", "question_book",
                  "edit-", "ambox", "wiki", "stub", "disambig", "padlock", "folder")


def wiki_entity_urls(entity):
    """Image URLs from the English Wikipedia article about `entity` (free-licensed, topic-specific)."""
    if entity in WIKI_CACHE:
        return WIKI_CACHE[entity]
    urls = []
    api = "https://en.wikipedia.org/w/api.php"
    try:
        sr = requests.get(api, headers=COMMONS_UA, timeout=30,
                          params={"action": "query", "format": "json", "list": "search",
                                  "srsearch": entity, "srlimit": 1}).json()
        hits = sr.get("query", {}).get("search", [])
        if hits:
            title = hits[0]["title"]
            im = requests.get(api, headers=COMMONS_UA, timeout=30,
                              params={"action": "query", "format": "json", "titles": title,
                                      "prop": "images", "imlimit": 40}).json()
            files = []
            for p in im.get("query", {}).get("pages", {}).values():
                for f in p.get("images", []):
                    name = f["title"]
                    low = name.lower()
                    if low.endswith((".jpg", ".jpeg", ".png")) and not any(w in low for w in BAD_FILE_WORDS):
                        files.append(name)
            files = files[:20]
            if files:
                info = requests.get(api, headers=COMMONS_UA, timeout=30,
                                    params={"action": "query", "format": "json", "titles": "|".join(files),
                                            "prop": "imageinfo", "iiprop": "url|size", "iiurlwidth": 1200}).json()
                for p in info.get("query", {}).get("pages", {}).values():
                    ii = (p.get("imageinfo") or [{}])[0]
                    if ii.get("thumburl") and ii.get("width", 0) >= 400 and ii.get("height", 0) >= 300:
                        urls.append(ii["thumburl"])
    except Exception as e:
        print(f"   ⚠️ Wikipedia: {e}")
    random.shuffle(urls)
    WIKI_CACHE[entity] = urls
    return urls


def wiki_photo(entity, filename):
    for url in wiki_entity_urls(entity):
        if url in USED_IMG_URLS:
            continue
        try:
            r = requests.get(url, headers=COMMONS_UA, timeout=60)
            if r.status_code == 200:
                save_fitted(r.content, filename)
                USED_IMG_URLS.add(url)
                return True
        except Exception:
            continue
    return False


def fetch_news_image(filename="news_img.jpg"):
    """OPTIONAL (USE_NEWS_IMAGES=1): the main photo of the news article itself."""
    if not USE_NEWS_IMAGES or not NEWS_LINK["url"]:
        return False
    try:
        url = NEWS_LINK["url"]
        try:
            from googlenewsdecoder import gnewsdecoder
            d = gnewsdecoder(url, interval=1)
            if d.get("status") and d.get("decoded_url"):
                url = d["decoded_url"]
        except Exception as e:
            print(f"   ⚠️ link decoder: {e}")
        html = requests.get(url, headers=UA, timeout=30).text
        m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html) or
             re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html))
        if m:
            img_url = m.group(1).replace("&amp;", "&")
            r = requests.get(img_url, headers=UA, timeout=40)
            if r.status_code == 200:
                save_fitted(r.content, filename)
                return True
    except Exception as e:
        print(f"   ⚠️ News image: {e}")
    return False


def openverse_photo(keywords, filename):
    """No key needed (anonymous, rate-limited). Creative-Commons photos."""
    try:
        res = requests.get("https://api.openverse.org/v1/images/", headers=COMMONS_UA, timeout=30,
                           params={"q": keywords, "page_size": 20, "license_type": "commercial",
                                   "mature": "false", "aspect_ratio": "tall"}).json()
        results = res.get("results", [])
        random.shuffle(results)
        for r in results[:6]:
            url = r.get("url")
            if not url:
                continue
            try:
                resp = requests.get(url, headers=UA, timeout=40)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                    save_fitted(resp.content, filename)
                    return True
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ Openverse: {e}")
    return False


def loremflickr_photo(keywords, filename):
    """No key needed. Random Flickr photo matching the tags."""
    try:
        tags = ",".join(keywords.replace(",", " ").split()[:2])
        url = f"https://loremflickr.com/{W}/{H}/{urllib.parse.quote(tags)}?random={random.randint(1, 99999)}"
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            save_fitted(r.content, filename)
            return True
    except Exception as e:
        print(f"   ⚠️ LoremFlickr: {e}")
    return False


LAST_GOOD_IMAGE = {"path": None}


def keyword_variants(kw):
    words = [w for w in kw.replace(",", " ").split() if w]
    variants = [kw]
    if len(words) > 1:
        variants += [" ".join(words[:2]), words[-1], words[0]]
    seen, out = set(), []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def get_scene_visual(i, scene):
    """Returns (path, 'video' | 'image'). Never ends on a blank gradient unless
    every source failed AND there is no earlier image to reuse."""
    kw = scene.get("keywords") or "news"
    prompt = scene.get("image_prompt") or kw
    vpath = f"scene_{i}.mp4"
    ipath = f"scene_{i}.jpg"
    variants = keyword_variants(kw)

    def done(src):
        print(f"   ✅ ചിത്രം: {src}")
        LAST_GOOD_IMAGE["path"] = ipath
        return ipath, "image"

    # ---- topic-specific pictures first ----
    if i == 0:
        print("   -> വാർത്തയുടെ ചിത്രം...")
        if fetch_news_image("news_img.jpg"):
            shutil.copy("news_img.jpg", ipath)
            return done("News article")
    entity = (scene.get("wiki_title") or "").strip()
    if entity:
        print(f"   -> Wikipedia ('{entity}')...")
        if wiki_photo(entity, ipath):
            return done("Wikipedia")

    # ---- videos ----
    for v in variants[:2]:
        if PEXELS_API_KEY:
            print(f"   -> Pexels വീഡിയോ ('{v}')...")
            if pexels_video(v, vpath):
                return vpath, "video"
        if PIXABAY_API_KEY:
            print(f"   -> Pixabay വീഡിയോ ('{v}')...")
            if pixabay_video(v, vpath):
                return vpath, "video"
    print(f"   -> Commons വീഡിയോ ('{kw}')...")
    cv = commons_video(kw, vpath)
    if cv:
        return cv, "video"

    # ---- photos ----
    for v in variants:
        if PEXELS_API_KEY:
            print(f"   -> Pexels ഫോട്ടോ ('{v}')...")
            if pexels_photo(v, ipath):
                return done("Pexels")
        print(f"   -> Commons ഫോട്ടോ ('{v}')...")
        if commons_photo(v, ipath):
            return done("Commons")
    for v in variants[:3]:
        print(f"   -> Openverse ('{v}')...")
        if openverse_photo(v, ipath):
            return done("Openverse")
    for v in variants[:2]:
        print(f"   -> LoremFlickr ('{v}')...")
        if loremflickr_photo(v, ipath):
            return done("LoremFlickr")

    # ---- AI images ----
    for name, fn in (("Pollinations", try_pollinations),
                     ("HuggingFace", try_huggingface),
                     ("Gemini", try_gemini_image)):
        print(f"   -> {name}...")
        if fn(prompt, ipath):
            return done(name)
        time.sleep(2)

    # ---- reuse an earlier scene's picture instead of a blank colour ----
    prev = LAST_GOOD_IMAGE["path"]
    if prev and os.path.exists(prev):
        shutil.copy(prev, ipath)
        print("   ♻️ മുൻ സീനിലെ ചിത്രം വീണ്ടും ഉപയോഗിക്കുന്നു")
        return ipath, "image"

    print("   -> ഗ്രേഡിയന്റ് (അവസാന മാർഗം)")
    make_gradient(ipath)
    return ipath, "image"


# ======================================================================
# Voice (ElevenLabs first, edge-tts fallback; one voice for the whole reel)
# ======================================================================
def eleven_tts(text, filename):
    payload = {"text": text, "model_id": ELEVENLABS_MODEL}
    if ELEVENLABS_MODEL != "eleven_v3":
        payload["language_code"] = "ml"
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json=payload, timeout=90)
        if r.status_code == 200:
            with open(filename, "wb") as f:
                f.write(r.content)
            return True
        print(f"   ⚠️ ElevenLabs {r.status_code}: {r.text[:150]}")
    except Exception as e:
        print(f"   ⚠️ ElevenLabs: {e}")
    return False


async def edge_tts_save(text, filename):
    comm = edge_tts.Communicate(text, EDGE_VOICE, rate="+8%", pitch="-2Hz")
    await comm.save(filename)


async def make_all_audio(scenes):
    """Returns list of audio file paths (same order as scenes) or None."""
    if ELEVENLABS_API_KEY:
        print("\n   ശബ്ദം: ElevenLabs ശ്രമിക്കുന്നു...")
        files = []
        for i, s in enumerate(scenes):
            fn = f"voice_{i}.mp3"
            if not eleven_tts(s["narration"], fn):
                files = None
                break
            files.append(fn)
        if files:
            print("   ✅ ElevenLabs ശബ്ദം ഉപയോഗിച്ചു")
            return files
        print("   ⚠️ ElevenLabs പരാജയപ്പെട്ടു -> edge-tts ഉപയോഗിക്കുന്നു")

    print(f"   ശബ്ദം: edge-tts ({EDGE_VOICE})")
    files = []
    for i, s in enumerate(scenes):
        fn = f"voice_{i}.mp3"
        try:
            await edge_tts_save(s["narration"], fn)
            files.append(fn)
        except Exception as e:
            print(f"   ❌ TTS error: {e}")
            return None
    return files


# ======================================================================
# Video building
# ======================================================================
def fill_portrait(clip):
    scale = max(W / clip.w, H / clip.h)
    clip = clip.resize(scale)
    return crop(clip, x_center=clip.w / 2, y_center=clip.h / 2, width=W, height=H)


def build_scene_clip(path, kind, duration):
    if kind == "video":
        v = VideoFileClip(path).without_audio()
        v = fill_portrait(v)
        v = loop(v, duration=duration) if v.duration < duration else v.subclip(0, duration)
        return v.set_duration(duration)
    img = ImageClip(path).set_duration(duration)
    zoomed = img.resize(lambda t: 1 + 0.06 * t / duration).set_position("center")
    return CompositeVideoClip([zoomed], size=(W, H)).set_duration(duration)


def download_bgm(filename="bgm.mp3"):
    links = [f"https://www.soundhelix.com/examples/mp3/SoundHelix-Song-{n}.mp3" for n in (1, 2, 3)]
    try:
        r = requests.get(random.choice(links), headers=UA, timeout=30)
        if r.status_code == 200:
            with open(filename, "wb") as f:
                f.write(r.content)
            return filename
    except Exception:
        pass
    return None


async def build_reel(scenes, output="final_reel.mp4", scene_media=None):
    print("\n[3] ശബ്ദം തയ്യാറാക്കുന്നു...")
    audio_files = await make_all_audio(scenes)
    if not audio_files:
        return None

    # Keep scenes only while total stays under the reel limit
    audio_clips, keep, total = [], [], 0.0
    for scene, af in zip(scenes, audio_files):
        a = AudioFileClip(af)
        d = a.duration + 0.15
        if total + d > MAX_REEL_SECONDS and keep:
            print("   ⚠️ ദൈർഘ്യ പരിധി: ബാക്കി സീനുകൾ ഒഴിവാക്കി")
            break
        audio_clips.append(a)
        keep.append(scene)
        total += d

    print("\n[4] ഓരോ സീനിനും ദൃശ്യം കണ്ടെത്തുന്നു...")
    video_parts = []
    for i, (scene, audio) in enumerate(zip(keep, audio_clips)):
        print(f"\nസീൻ {i+1}: {scene['narration'][:50]}...")
        duration = audio.duration + 0.15
        chosen = scene_media[i] if (scene_media and i < len(scene_media)) else None
        if chosen:
            sub = duration / len(chosen)
            parts = []
            for p, kind in chosen:
                try:
                    parts.append(build_scene_clip(p, kind, sub))
                except Exception as e:
                    print(f"   ⚠️ ക്ലിപ്പ് പിശക്: {e} -> ഗ്രേഡിയന്റ്")
                    make_gradient("fallback.jpg")
                    parts.append(build_scene_clip("fallback.jpg", "image", sub))
            clip = parts[0] if len(parts) == 1 else concatenate_videoclips(parts, method="compose")
            clip = clip.set_duration(duration)
        else:
            path, kind = get_scene_visual(i, scene)
            try:
                clip = build_scene_clip(path, kind, duration)
            except Exception as e:
                print(f"   ⚠️ ക്ലിപ്പ് പിശക്: {e} -> ഗ്രേഡിയന്റ്")
                make_gradient("fallback.jpg")
                clip = build_scene_clip("fallback.jpg", "image", duration)
        video_parts.append(clip)
        time.sleep(1)

    print("\n[5] വീഡിയോ എഡിറ്റ് ചെയ്യുന്നു...")
    # Place each voice clip at the exact start time of its scene
    starts, t = [], 0.0
    for a in audio_clips:
        starts.append(t)
        t += a.duration + 0.15
    total_len = t
    voice = CompositeAudioClip([a.set_start(st) for a, st in zip(audio_clips, starts)]).set_duration(total_len)

    final_video = concatenate_videoclips(video_parts, method="compose")

    final_audio = voice
    bgm = download_bgm()
    if bgm:
        try:
            b = AudioFileClip(bgm).volumex(0.08)
            b = b.subclip(0, min(b.duration, voice.duration))
            final_audio = CompositeAudioClip([voice, b])
        except Exception:
            pass

    final_video = final_video.set_audio(final_audio).set_duration(voice.duration)
    print(f"ആകെ ദൈർഘ്യം: {voice.duration:.1f}s")
    try:
        final_video.write_videofile(output, fps=24, codec="libx264", audio_codec="aac",
                                    preset="ultrafast", threads=2, logger=None)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        return None
    print("✅ വീഡിയോ തയ്യാർ!")
    return output


# ======================================================================
# Facebook upload
# ======================================================================
def upload_to_facebook(video_path, caption):
    print("\n[6] ഫേസ്ബുക്കിലേക്ക് അപ്‌ലോഡ് ചെയ്യുന്നു...")
    reels_url = f"{FB_API}/me/video_reels"

    res1 = requests.post(reels_url, data={'upload_phase': 'start',
                                          'access_token': FB_PAGE_ACCESS_TOKEN},
                         timeout=60).json()
    video_id = res1.get('video_id')
    if not video_id:
        print(f"❌ Init Failed: {res1}")
        return False

    upload_url = f"https://rupload.facebook.com/video-upload/v18.0/{video_id}"
    headers = {'Authorization': f'OAuth {FB_PAGE_ACCESS_TOKEN}', 'offset': '0',
               'file_size': str(os.path.getsize(video_path))}
    with open(video_path, 'rb') as f:
        res2 = requests.post(upload_url, headers=headers, data=f, timeout=300)
    if res2.status_code != 200:
        print(f"❌ Upload Failed: {res2.status_code} {res2.text}")
        return False

    payload = {'upload_phase': 'finish', 'video_id': video_id, 'video_state': 'PUBLISHED',
               'description': caption, 'access_token': FB_PAGE_ACCESS_TOKEN}
    res3 = requests.post(reels_url, data=payload, timeout=60).json()
    if res3.get('success'):
        print("🎉 റീൽസ് വിജയകരമായി പോസ്റ്റ് ചെയ്തു!")
        return True
    print(f"❌ Publish Failed: {res3}")
    return False


# ======================================================================
# ======================================================================
# Keyword mode: you send search keywords, the bot finds the pictures
# ======================================================================
def gemini_json(prompt):
    """Small helper: ask Gemini (no search) and return the parsed JSON object, or None."""
    prefs = ['gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it']
    available = get_available_models()
    for model in [m for m in prefs if m in available] + [m for m in available if m not in prefs]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            data = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=90).json()
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            a, b = text.find("{"), text.rfind("}")
            if a >= 0 and b > a:
                return json.loads(text[a:b + 1], strict=False)
        except Exception:
            continue
    return None


def to_english_queries(queries):
    """Malayalam (or any non-English) keywords -> short English photo-search phrases."""
    uniq = list(dict.fromkeys(queries))
    if all(q.isascii() for q in uniq):
        return {q: q for q in uniq}
    data = gemini_json("Translate each photo-search phrase into a short English phrase suitable for searching "
                       "photos (keep people's and places' usual English spelling). Return ONLY a JSON object "
                       "mapping every original phrase to its English version.\n"
                       + json.dumps(uniq, ensure_ascii=False))
    out = {}
    for q in uniq:
        v = (data or {}).get(q)
        out[q] = v.strip() if isinstance(v, str) and v.strip() else q
    return out


def parse_keyword_messages(texts):
    """Your keyword messages -> list of (scene number or None, [queries]).  One line = one scene.
    Examples:  '1 Sanju Samson batting'   |   'Kerala cricket stadium, cheering fans'"""
    rows = []
    for t in texts:
        for line in t.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^(\d{1,2})\s*[\.\):\-–]?\s+(.+)$', line)
            num, body = (int(m.group(1)), m.group(2)) if m else (None, line)
            queries = [q.strip() for q in re.split(r'[,;|]', body) if q.strip()]
            if queries:
                rows.append((num, queries))
    return rows


def cand_pexels(q):
    if not PEXELS_API_KEY:
        return []
    res = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": PEXELS_API_KEY},
                       params={"query": q, "orientation": "portrait", "per_page": 15}, timeout=30).json()
    return [p["src"]["large2x"] for p in res.get("photos", [])]


def cand_wikipedia(q):
    return wiki_entity_urls(q)


def cand_commons(q):
    res = requests.get("https://commons.wikimedia.org/w/api.php", headers=COMMONS_UA, timeout=30,
                       params={"action": "query", "format": "json", "generator": "search",
                               "gsrsearch": f"{q} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 20,
                               "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 1200}).json()
    urls = []
    for p in res.get("query", {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mime") in ("image/jpeg", "image/png") and (ii.get("thumburl") or ii.get("url")):
            urls.append(ii.get("thumburl") or ii["url"])
    random.shuffle(urls)
    return urls


def cand_openverse(q):
    res = requests.get("https://api.openverse.org/v1/images/", headers=COMMONS_UA, timeout=30,
                       params={"q": q, "page_size": 20, "license_type": "commercial", "mature": "false"}).json()
    urls = [r.get("url") or r.get("thumbnail") for r in res.get("results", [])]
    urls = [u for u in urls if u]
    random.shuffle(urls)
    return urls


def cand_flickr(q):
    tags = ",".join(q.replace(",", " ").split()[:2])
    return [f"https://loremflickr.com/{W}/{H}/{urllib.parse.quote(tags)}?lock={random.randint(1, 99999)}"
            for _ in range(4)]


def fetch_query_images(query, n, tag):
    """Finds up to n different pictures for one keyword phrase. Returns [(path, 'image')]."""
    sources = [("Pexels", cand_pexels), ("Wikipedia", cand_wikipedia), ("Commons", cand_commons),
               ("Openverse", cand_openverse), ("Flickr", cand_flickr)]
    got = []
    for name, fn in sources:
        if len(got) >= n:
            break
        try:
            urls = fn(query)
        except Exception as e:
            print(f"   ⚠️ {name}: {e}")
            continue
        for u in urls:
            if len(got) >= n:
                break
            if u in USED_IMG_URLS:
                continue
            try:
                r = requests.get(u, headers=COMMONS_UA, timeout=40)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                    path = f"kw_{tag}_{len(got)}.jpg"
                    save_fitted(r.content, path)
                    USED_IMG_URLS.add(u)
                    got.append((path, "image"))
                    print(f"      ✅ {name}: '{query}'")
            except Exception:
                continue
    return got


def scene_numbers_from_caption(cap):
    """Photo caption '3' / 'scene 3' / '3, 4'  ->  [3] / [3] / [3, 4]; anything else -> []."""
    m = re.fullmatch(r'\s*(?:scene|സീൻ)?\s*(\d{1,2}(?:\s*[,&/]\s*\d{1,2})*)\s*', cap or "", re.I)
    return [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []


def build_scene_media(items, n):
    """Mixes your photos/videos and keyword lines into one list of pictures per scene.
      - photo/video whose caption is a scene number ('3' or '3, 4')  -> that scene
      - keyword line that starts with a scene number ('3 Sanju Samson') -> that scene
      - everything WITHOUT a scene number is spread, in the order you sent it, over the scenes
        that have nothing yet
      - a scene that stays empty (or whose keyword finds nothing) uses the bot's own keywords (auto)
    Returns (list with one [(path, kind), ...] per scene, summary text)."""
    slots = [{"media": [], "kw": []} for _ in range(n)]
    loose = []
    for it in items:
        if it["type"] == "media":
            if not it.get("path"):
                continue
            f = (it["path"], it["kind"])
            nums = [x for x in scene_numbers_from_caption(it.get("caption")) if 1 <= x <= n]
            if nums:
                for x in nums:
                    slots[x - 1]["media"].append(f)
            else:
                loose.append(("media", f))
        else:
            for num, qs in parse_keyword_messages([it["text"]]):
                if num and 1 <= num <= n:
                    slots[num - 1]["kw"].extend(qs)
                else:
                    loose.append(("kw", qs))

    targets = [i for i in range(n) if not slots[i]["media"] and not slots[i]["kw"]] or list(range(n))
    if loose:
        L, T = len(loose), len(targets)
        for t, i in enumerate(targets):
            a, b = (t * L) // T, ((t + 1) * L) // T
            for kind, payload in (loose[a:b] or [loose[min(L - 1, a)]]):
                if kind == "media":
                    slots[i]["media"].append(payload)
                else:
                    slots[i]["kw"].extend(payload)

    all_q = [q for s in slots for q in s["kw"]]
    english = to_english_queries(all_q) if all_q else {}
    result, summary = [], []
    for i, s in enumerate(slots):
        imgs = list(s["media"])
        qs = list(dict.fromkeys(s["kw"]))
        found_kw = 0
        if qs:
            n_each = 2 if (len(qs) == 1 and not imgs) else 1     # one keyword = 2 pictures
            print(f"\nസീൻ {i+1} കീവേഡ്: {qs}")
            for k, q in enumerate(qs):
                got = fetch_query_images(english.get(q, q), n_each, f"{i}_{k}")
                found_kw += len(got)
                imgs += got
        result.append(imgs)
        parts = []
        if s["media"]:
            parts.append(f"📷 {len(s['media'])} ഫയൽ")
        if qs:
            parts.append(f"🔎 {', '.join(qs)}" + ("" if found_kw else " (ചിത്രം കിട്ടിയില്ല)"))
        summary.append(f"സീൻ {i+1}: " + (" + ".join(parts) if imgs else "🤖 auto (ബോട്ടിന്റെ കീവേഡ്)"))
    return result, "\n".join(summary)


# ======================================================================
# Telegram control:  you send a topic -> plan -> you send photos + "reel" -> publish
# ======================================================================
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "pending.json"
WORKER_URL = (os.environ.get("WORKER_URL") or "").rstrip("/")
WORKER_API_KEY = os.environ.get("WORKER_API_KEY") or ""


def tg(method, **params):
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", data=params, timeout=60)
    return r.json()


def tg_send(text):
    """Sends a message (split if long). Returns True only if Telegram accepted every part."""
    ok = True
    for i in range(0, len(text), 3800):
        res = tg("sendMessage", chat_id=TG_CHAT, text=text[i:i + 3800])
        if not res.get("ok"):
            print(f"❌ Telegram send failed: {res}")
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


NON_KEYWORD = {"reel", "/reel", "post", "/post", "done", "/done", "auto", "/auto", "cancel", "/cancel",
               "trends", "/trends", "trending", "/trending", "news", "/news", "suggest", "/suggest",
               "yes", "y", "no", "n", "hi", "hii", "hello", "hey", "ok", "okay", "thanks", "thank you"}


def collect_media(state):
    """Everything you sent since state['offset'], in order.
    Returns (items, last update id). Item = {'type':'media','file_id','kind','caption'} or {'type':'text','text'}."""
    if WORKER_URL:
        updates = requests.get(f"{WORKER_URL}/updates", params={"offset": state.get("offset", 0)},
                               headers={"Authorization": f"Bearer {WORKER_API_KEY}"},
                               timeout=30).json().get("result", [])
    else:
        updates = tg("getUpdates", offset=state.get("offset", 0), timeout=0,
                     allowed_updates=json.dumps(["message"])).get("result", [])
    items, max_id = [], None
    for u in updates:
        max_id = u["update_id"]
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(TG_CHAT):
            continue
        cap = (m.get("caption") or "").strip()
        if m.get("photo"):
            items.append({"type": "media", "file_id": m["photo"][-1]["file_id"], "kind": "image", "caption": cap})
        elif m.get("video"):
            items.append({"type": "media", "file_id": m["video"]["file_id"], "kind": "video", "caption": cap})
        elif m.get("document"):
            mt = m["document"].get("mime_type", "")
            if mt.startswith("image/"):
                items.append({"type": "media", "file_id": m["document"]["file_id"], "kind": "image", "caption": cap})
            elif mt.startswith("video/"):
                items.append({"type": "media", "file_id": m["document"]["file_id"], "kind": "video", "caption": cap})
        else:
            text = (m.get("text") or "").strip()
            low = text.lower()
            if text and not text.startswith("/") and low not in NON_KEYWORD and not low.rstrip(".)").isdigit():
                items.append({"type": "text", "text": text})
    return items, max_id


def assign_media(i, n, media):
    """Spread the user's media over the scenes (works for fewer or more files than scenes)."""
    m = len(media)
    a, b = (i * m) // n, ((i + 1) * m) // n
    chosen = media[a:b]
    if not chosen:
        chosen = [media[min(m - 1, a)]]
    return chosen


async def plan():
    """Triggered by check_telegram.py when you send a topic. Writes the script and sends it to you."""
    state = load_state() or {}
    topic = (state.get("topic") or "").strip()
    try:
        if not topic:
            topic = get_trending_topic()
        tg_send(f"🔎 '{topic}' - ഏറ്റവും പുതിയ വിവരങ്ങൾ തിരഞ്ഞ് സ്ക്രിപ്റ്റ് തയ്യാറാക്കുന്നു...")
        context = (state.get("context") or "").strip()
        scenes, caption = get_gemini_scenes(topic + (f" (പശ്ചാത്തലം: {context})" if context else ""))
    except Exception as e:
        print(f"❌ {e}")
        scenes, caption = [], None

    if not scenes:
        state.update(status="failed")
        save_state(state)
        tg_send("❌ സ്ക്രിപ്റ്റ് ഉണ്ടാക്കാൻ കഴിഞ്ഞില്ല. വിഷയം ഒന്ന് മാറ്റി വീണ്ടും അയക്കൂ.")
        sys.exit(1)

    state.update(status="waiting_media", topic=topic, scenes=scenes, caption=caption,
                 created=time.time())
    lines = [f"🎬 വിഷയം: {topic}", "",
             "ഇനി ഓരോ സീനിനും ചിത്രം നൽകൂ. രണ്ട് രീതിയും ഒരുമിച്ച് ഉപയോഗിക്കാം:",
             "🔎 കീവേഡ്: സീൻ നമ്പർ + കീവേഡ് അയക്കൂ (ഒരു വരി ഒരു സീനിന്, ഇംഗ്ലീഷ് നല്ലത്):",
             "     1 Sanju Samson batting",
             "     2 Kerala cricket stadium, cheering fans",
             "📷 ഫോട്ടോ/വീഡിയോ: ചിത്രം കിട്ടാൻ സാധ്യതയില്ലാത്ത സീനിന് നിങ്ങളുടെ ഫോട്ടോ അയക്കൂ, "
             "caption-ൽ സീൻ നമ്പർ എഴുതൂ (ഉദാ: 3).",
             "നമ്പർ ഇല്ലാതെ അയച്ചാൽ അവ ബാക്കി സീനുകളിൽ ക്രമത്തിൽ വരും. ഒന്നും നൽകാത്ത സീനിന് ബോട്ടിന്റെ "
             "സ്വന്തം കീവേഡ് ഉപയോഗിക്കും.",
             "എല്ലാം അയച്ച ശേഷം  reel  എന്ന് ടൈപ്പ് ചെയ്യൂ. എല്ലാം ബോട്ടിന് വിടാൻ  auto  എന്ന് അയക്കൂ.",
             "വേറെ വിഷയം: /new വിഷയം   |   ഒഴിവാക്കാൻ: cancel", ""]
    for i, s in enumerate(scenes, 1):
        hint = s.get("wiki_title") or s.get("keywords") or ""
        lines.append(f"സീൻ {i}: {s['narration']}")
        lines.append(f"   🔎 തിരയാൻ: {hint}")
    if not tg_send("\n".join(lines)):
        print("❌ ടെലിഗ്രാമിൽ അയക്കാൻ കഴിഞ്ഞില്ല. സ്റ്റേറ്റ് സേവ് ചെയ്തില്ല.")
        sys.exit(1)
    tg_send("📝 പോസ്റ്റ് ടെക്സ്റ്റ് (ക്യാപ്ഷൻ):\n\n" + caption)
    save_state(state)
    print("✅ സ്ക്രിപ്റ്റ് അയച്ചു. ഇനി ഫോട്ടോകൾക്കായി കാത്തിരിക്കുന്നു.")


async def publish():
    """Triggered by check_telegram.py when you type 'reel' (or 'auto')."""
    state = load_state()
    if not state or state.get("status") != "waiting_media":
        print("പോസ്റ്റ് ചെയ്യാൻ ഒന്നുമില്ല.")
        return
    state["status"] = "posting"      # if this run crashes, the same reel is not retried forever
    save_state(state)

    items, max_id = collect_media(state)
    state["offset"] = (max_id + 1) if max_id is not None else state.get("offset", 0)
    scenes = state["scenes"]
    n = len(scenes)

    n_media = sum(1 for it in items if it["type"] == "media")
    if n_media:
        print(f"\n📥 {n_media} ഫയൽ ഡൗൺലോഡ് ചെയ്യുന്നു...")
    for k, it in enumerate(items):
        if it["type"] != "media":
            continue
        try:
            info = tg("getFile", file_id=it["file_id"])
            fp = info["result"]["file_path"]
            data = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{fp}", timeout=120).content
            if it["kind"] == "image":
                path = f"user_{k}.jpg"
                save_fitted(data, path)
            else:
                path = f"user_{k}.mp4"
                with open(path, "wb") as f:
                    f.write(data)
            it["path"] = path
        except Exception as e:
            print(f"   ⚠️ ഫയൽ {k+1} എടുക്കാൻ കഴിഞ്ഞില്ല (20MB-ൽ കൂടുതലാകാം): {e}")

    ok = False
    try:
        if not check_facebook_permissions():
            tg_send("❌ ഫേസ്ബുക്ക് ടോക്കൺ പ്രശ്നം. റീൽ പോസ്റ്റ് ചെയ്തില്ല.")
        else:
            scene_media = None
            if any(it["type"] == "text" or it.get("path") for it in items):
                tg_send("🔎 ചിത്രങ്ങൾ തയ്യാറാക്കുന്നു (നിങ്ങളുടെ ഫോട്ടോകളും കീവേഡുകളും)...")
                scene_media, summary = build_scene_media(items, n)
                tg_send("📋 ചിത്രങ്ങൾ ഇങ്ങനെ ഉപയോഗിക്കുന്നു:\n" + summary)
                if not any(scene_media):
                    scene_media = None
            tg_send("⚙️ റീൽ ഉണ്ടാക്കുന്നു, കുറച്ച് മിനിറ്റ് എടുക്കും...")
            video_path = await build_reel(scenes, scene_media=scene_media)
            ok = bool(video_path) and upload_to_facebook(video_path, state["caption"][:2200])
            tg_send("🎉 റീൽ പോസ്റ്റ് ചെയ്തു!" if ok else
                    "❌ റീൽ പോസ്റ്റ് ചെയ്യാൻ കഴിഞ്ഞില്ല. GitHub ലോഗ് നോക്കൂ.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        tg_send(f"❌ പിശക്: {str(e)[:200]}")

    state["status"] = "posted" if ok else "failed"
    save_state(state)
    if not ok:
        sys.exit(1)


async def main():
    print("🚀 Auto Facebook Reels Bot ആരംഭിക്കുന്നു...\n")
    print(f"Keys -> Pexels: {bool(PEXELS_API_KEY)} | Pixabay: {bool(PIXABAY_API_KEY)} | "
          f"ElevenLabs: {bool(ELEVENLABS_API_KEY)} | HF: {bool(HF_API_KEY)}")
    if not check_facebook_permissions():
        sys.exit(1)

    topic = get_trending_topic()
    scenes, caption = get_gemini_scenes(topic)
    if not scenes:
        print("❌ ജെമിനിയിൽ നിന്ന് ഡാറ്റ ലഭിച്ചില്ല.")
        sys.exit(1)

    video_path = await build_reel(scenes)
    if not video_path:
        print("❌ വീഡിയോ നിർമ്മിക്കാൻ കഴിഞ്ഞില്ല.")
        sys.exit(1)

    if not upload_to_facebook(video_path, caption):
        sys.exit(1)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if mode == "plan":
        asyncio.run(plan())
    elif mode == "publish":
        asyncio.run(publish())
    else:
        asyncio.run(main())   # old fully-automatic mode
