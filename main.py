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


def get_gemini_scenes(topic):
    """Returns (scenes, caption). Uses Google Search grounding when available so the
    script and the long caption contain the latest real details about the topic."""
    available = get_available_models()
    prefs = ['gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it']
    models = [m for m in prefs if m in available] + [m for m in available if m not in prefs]

    prompt = f"""
You are an expert Malayalam news-explainer Reels creator for a Facebook page.
Topic given by the page owner: '{topic}'

STEP 1: Use Google Search to find the LATEST verified information about this topic:
what happened, who is involved, where and when, key numbers, the reasons, reactions from different sides,
background, and what may happen next.

RULES
- Use only facts you actually found. Never invent numbers, quotes, names or dates.
- If something is unconfirmed, say "റിപ്പോർട്ടുകൾ പ്രകാരം". Keep facts separate from opinions. Be fair and non-defamatory.

STEP 2: Respond ONLY with one valid JSON object (no markdown fences, no text outside the JSON).
Inside JSON strings write line breaks as \\n.

"scenes": 6 to 8 scenes for a ~60 second Malayalam voiceover. Each scene has:
  - "narration": 1-2 short, spoken-style Malayalam sentences.
    Scene 1 must be a strong hook (a surprising fact or a sharp question).
    The last scene must leave the viewer with a debatable point (do NOT ask for comments, likes or shares).
  - "keywords": 2-3 simple English words for stock footage matching THAT narration
  - "image_prompt": detailed English prompt for an AI image matching that scene
  - "wiki_title": exact English Wikipedia article title of the MAIN real person, team, place,
    organisation or event of that scene (e.g. "Sanju Samson", "Kerala"). Use "" if generic.

"caption": the Facebook post text in Malayalam, MAX 1800 characters, written so that a person who never
watched the reel still understands the whole story:
  - Line 1: attention-grabbing headline with one emoji
  - Then short paragraphs: what happened, who/where/when, key numbers and facts, why it matters,
    background, what may happen next
  - Then 1-2 sentences that lay out the different sides or the dilemma, so readers feel like giving
    their own opinion. NEVER write "comment", "share", "tag", "like" requests and never ask
    "what do you think?"; just present the issue so sharply that people react on their own.
  - 5 to 8 relevant hashtags at the end

Exact structure:
{{"scenes": [{{"narration": "...", "keywords": "...", "image_prompt": "...", "wiki_title": "..."}}],
  "caption": "..."}}
"""
    for model in models:
        print(f"\\n{model} ഉപയോഗിച്ച് സീനുകൾ തയ്യാറാക്കുന്നു...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        for use_search in (True, False):
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            if use_search:
                payload["tools"] = [{"google_search": {}}]
            try:
                data = requests.post(url, json=payload, timeout=120).json()
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
                if len(scenes) >= 3 and caption:
                    print(f"✅ {len(scenes)} സീനുകൾ ലഭിച്ചു! (search={use_search})")
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


async def build_reel(scenes, output="final_reel.mp4", user_media=None):
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
        if user_media:
            chosen = assign_media(i, len(keep), user_media)
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
# Telegram control:  you send a topic -> plan -> you send photos + "reel" -> publish
# ======================================================================
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "pending.json"


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


def collect_media(state):
    """Photos/videos sent since state['offset']. Returns (media list, last update id)."""
    res = tg("getUpdates", offset=state.get("offset", 0), timeout=0,
             allowed_updates=json.dumps(["message"]))
    media, max_id = [], None
    for u in res.get("result", []):
        max_id = u["update_id"]
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(TG_CHAT):
            continue
        if m.get("photo"):
            media.append({"file_id": m["photo"][-1]["file_id"], "kind": "image"})
        elif m.get("video"):
            media.append({"file_id": m["video"]["file_id"], "kind": "video"})
        elif m.get("document"):
            mt = m["document"].get("mime_type", "")
            if mt.startswith("image/"):
                media.append({"file_id": m["document"]["file_id"], "kind": "image"})
            elif mt.startswith("video/"):
                media.append({"file_id": m["document"]["file_id"], "kind": "video"})
    return media, max_id


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
        scenes, caption = get_gemini_scenes(topic)
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
             f"📸 {len(scenes)} ഫോട്ടോ/വീഡിയോ ക്രമത്തിൽ അയക്കൂ (കുറവായാലും മതി). "
             "എല്ലാം അയച്ച ശേഷം  reel  എന്ന് ടൈപ്പ് ചെയ്യൂ.",
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
    """Triggered by check_telegram.py when you type 'reel' after sending photos."""
    state = load_state()
    if not state or state.get("status") != "waiting_media":
        print("പോസ്റ്റ് ചെയ്യാൻ ഒന്നുമില്ല.")
        return
    state["status"] = "posting"      # if this run crashes, the same reel is not retried forever
    save_state(state)

    media_info, max_id = collect_media(state)
    state["offset"] = (max_id + 1) if max_id is not None else state.get("offset", 0)

    print(f"\n📥 {len(media_info)} ഫയൽ ഡൗൺലോഡ് ചെയ്യുന്നു...")
    files = []
    for k, mi in enumerate(media_info):
        try:
            info = tg("getFile", file_id=mi["file_id"])
            fp = info["result"]["file_path"]
            data = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{fp}", timeout=120).content
            if mi["kind"] == "image":
                path = f"user_{k}.jpg"
                save_fitted(data, path)
            else:
                path = f"user_{k}.mp4"
                with open(path, "wb") as f:
                    f.write(data)
            files.append((path, mi["kind"]))
        except Exception as e:
            print(f"   ⚠️ ഫയൽ {k+1} എടുക്കാൻ കഴിഞ്ഞില്ല (20MB-ൽ കൂടുതലാകാം): {e}")

    ok = False
    if not files:
        tg_send("❌ അയച്ച ഫയലുകൾ എടുക്കാൻ കഴിഞ്ഞില്ല. ചെറിയ ഫയലുകൾ അയച്ച് വീണ്ടും ശ്രമിക്കൂ.")
    elif not check_facebook_permissions():
        tg_send("❌ ഫേസ്ബുക്ക് ടോക്കൺ പ്രശ്നം. റീൽ പോസ്റ്റ് ചെയ്തില്ല.")
    else:
        tg_send("⚙️ റീൽ ഉണ്ടാക്കുന്നു, കുറച്ച് മിനിറ്റ് എടുക്കും...")
        video_path = await build_reel(state["scenes"], user_media=files)
        ok = bool(video_path) and upload_to_facebook(video_path, state["caption"][:2200])
        tg_send("🎉 റീൽ പോസ്റ്റ് ചെയ്തു!" if ok else
                "❌ റീൽ പോസ്റ്റ് ചെയ്യാൻ കഴിഞ്ഞില്ല. GitHub ലോഗ് നോക്കൂ.")

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
