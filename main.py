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
from PIL import Image, ImageOps

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
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID") or "pNInz6obpgDQGcFmaJgB"
EDGE_VOICE = os.environ.get("EDGE_VOICE") or "ml-IN-MidhunNeural"  # or ml-IN-SobhanaNeural

FB_API = "https://graph.facebook.com/v18.0"
UA = {"User-Agent": "Mozilla/5.0"}
COMMONS_UA = {"User-Agent": "ReelsBot/1.0 (personal project)"}
W, H = 1080, 1920
MAX_REEL_SECONDS = 88
USED_VIDEO_IDS = set()


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
            topic = random.choice(feed.entries[:5]).title
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
    available = get_available_models()
    prefs = ['gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it']
    models = [m for m in prefs if m in available] + [m for m in available if m not in prefs]

    prompt = f"""
    You are an expert Facebook Reels creator. Topic: '{topic}'.
    Respond ONLY with a valid JSON object, no markdown, no extra text.

    Split a ~60 second Malayalam voiceover into 6 to 8 short scenes. For each scene give:
    - "narration": 1-2 short Malayalam sentences spoken in that scene
    - "keywords": 2-3 simple English words for stock footage that visually matches THAT narration
      (concrete things like "gold jewellery", "heavy rain", "football stadium", "smartphone", "city traffic")
    - "image_prompt": a detailed English prompt for an AI image matching that scene

    Exact structure:
    {{
      "scenes": [
        {{"narration": "...", "keywords": "...", "image_prompt": "..."}}
      ],
      "caption": "Catchy Malayalam Facebook caption with hashtags"
    }}
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for model in models:
        print(f"\n{model} ഉപയോഗിച്ച് സീനുകൾ തയ്യാറാക്കുന്നു...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            data = requests.post(url, json=payload, timeout=90).json()
            if 'candidates' not in data:
                continue
            text = data['candidates'][0]['content']['parts'][0]['text'].strip()
            if text.startswith("```"):
                text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
            if text.startswith("json"):
                text = text[4:].strip()
            parsed = json.loads(text)
            scenes = [s for s in parsed.get("scenes", []) if s.get("narration")]
            caption = parsed.get("caption", "")
            if len(scenes) >= 3 and caption:
                print(f"✅ {len(scenes)} സീനുകൾ ലഭിച്ചു!")
                return scenes, caption
        except Exception as e:
            print(f"⚠️ {e}")
    return [], None


# ======================================================================
# Visual sources
# ======================================================================
def save_fitted(img_bytes, filename):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
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


def get_scene_visual(i, scene):
    """Returns (path, 'video' | 'image').
    Order: Pexels video > Pixabay video > Commons video > Pexels photo >
           Commons photo > AI image > gradient. Sources without keys are skipped."""
    kw = scene.get("keywords") or "news"
    prompt = scene.get("image_prompt") or kw
    vpath = f"scene_{i}.mp4"
    ipath = f"scene_{i}.jpg"

    if PEXELS_API_KEY:
        print("   -> Pexels വീഡിയോ...")
        if pexels_video(kw, vpath):
            return vpath, "video"

    if PIXABAY_API_KEY:
        print("   -> Pixabay വീഡിയോ...")
        if pixabay_video(kw, vpath):
            return vpath, "video"

    print("   -> Commons വീഡിയോ...")
    cv = commons_video(kw, vpath)
    if cv:
        return cv, "video"

    if PEXELS_API_KEY:
        print("   -> Pexels ഫോട്ടോ...")
        if pexels_photo(kw, ipath):
            return ipath, "image"

    print("   -> Commons ഫോട്ടോ...")
    if commons_photo(kw, ipath):
        return ipath, "image"

    for name, fn in (("Pollinations", try_pollinations),
                     ("HuggingFace", try_huggingface),
                     ("Gemini", try_gemini_image)):
        print(f"   -> {name}...")
        if fn(prompt, ipath):
            return ipath, "image"
        time.sleep(2)

    print("   -> ഗ്രേഡിയന്റ്")
    make_gradient(ipath)
    return ipath, "image"


# ======================================================================
# Voice (ElevenLabs first, edge-tts fallback; one voice for the whole reel)
# ======================================================================
def eleven_tts(text, filename):
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_multilingual_v2",
                  "voice_settings": {"stability": 0.4, "similarity_boost": 0.8, "style": 0.3}},
            timeout=90)
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


async def build_reel(scenes, output="final_reel.mp4"):
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
    # Pad each voice clip so audio timing matches the video scene length
    from moviepy.audio.AudioClip import AudioClip
    padded = []
    for a in audio_clips:
        pad = AudioClip(lambda t: 0 * t, duration=0.15, fps=44100)
        padded.append(concatenate_audioclips([a, pad]))
    voice = concatenate_audioclips(padded)

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
    final_video.write_videofile(output, fps=24, codec="libx264", audio_codec="aac",
                                preset="ultrafast", threads=2, logger=None)
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
    asyncio.run(main())
