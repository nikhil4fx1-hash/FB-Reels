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

# === MOVIEPY ERROR FIX ===
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
# =========================

import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips

# --- Environment Variables ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
HF_API_KEY = os.environ.get("HF_API_KEY")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

FB_API = "https://graph.facebook.com/v18.0"
UA = {"User-Agent": "Mozilla/5.0"}


def check_facebook_permissions():
    print("\n[1] ഫേസ്ബുക്ക് കണക്ഷൻ പരിശോധിക്കുന്നു...")
    if not FB_PAGE_ACCESS_TOKEN:
        print("❌ എറർ: FB_PAGE_ACCESS_TOKEN നൽകിയിട്ടില്ല!")
        return False
    try:
        res = requests.get(f"{FB_API}/me", params={"fields": "name", "access_token": FB_PAGE_ACCESS_TOKEN}, timeout=30).json()
        if 'name' in res:
            print(f"✅ ഫേസ്ബുക്ക് പേജ് കണക്ട് ആയി: {res['name']}")
            return True
        print(f"❌ ഫേസ്ബുക്ക് കണക്ഷൻ എറർ: {res}")
        return False
    except Exception as e:
        print(f"❌ FB exception: {e}")
        return False


def get_trending_topic():
    print("\n[2] ട്രെൻഡിങ് വിഷയം തിരയുന്നു...")
    keywords = ["കേരളം", "ഇന്ത്യൻ ഫുട്ബോൾ", "സഞ്ജു സാംസൺ", "ടെക്നോളജി", "സിനിമ",
                "കേരള രാഷ്ട്രീയം", "ദുബായ് വാർത്തകൾ", "വിചിത്രമായ വാർത്തകൾ"]
    keyword = random.choice(keywords)
    encoded = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=ml&gl=IN&ceid=IN:ml"
    try:
        response = requests.get(rss_url, timeout=30)
        feed = feedparser.parse(response.content)
        if feed.entries:
            topic = random.choice(feed.entries[:5]).title
            print(f"✅ വിഷയം കണ്ടെത്തി: {topic}")
            return topic
    except Exception:
        pass
    return "ഇന്നത്തെ പ്രധാന വാർത്തകൾ"


def get_available_models():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    available = []
    try:
        data = requests.get(url, timeout=30).json()
        for m in data.get('models', []):
            if 'generateContent' in m.get('supportedGenerationMethods', []):
                available.append(m['name'].split('/')[-1])
    except Exception:
        pass
    return available


def get_gemini_script(topic):
    available_models = get_available_models()
    preferences = ['gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it']
    models_to_try = [m for m in preferences if m in available_models]
    for m in available_models:
        if m not in models_to_try:
            models_to_try.append(m)

    prompt = f"""
    You are an expert Facebook Reels creator. Create content about the topic: '{topic}'.
    IMPORTANT: You MUST respond ONLY with a valid JSON object. Do not add any markdown, asterisks, or extra text.

    The JSON must strictly follow this exact structure:
    {{
        "script": "A 60-second highly engaging Malayalam voiceover script.",
        "image_prompts": [
            "Hyper-realistic, highly detailed, cinematic English prompt for image 1 related to the topic",
            "Hyper-realistic, highly detailed, cinematic English prompt for image 2 related to the topic",
            "Hyper-realistic, highly detailed, cinematic English prompt for image 3 related to the topic"
        ],
        "caption": "Catchy Malayalam Facebook caption with hashtags"
    }}
    """
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    for model in models_to_try:
        print(f"\n{model} ഉപയോഗിച്ച് സ്ക്രിപ്റ്റ് തയ്യാറാക്കുന്നു...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            data = requests.post(url, headers=headers, json=payload, timeout=60).json()
            if 'candidates' in data:
                text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                if text.startswith("```"):
                    text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
                if text.startswith("json"):
                    text = text[4:].strip()
                try:
                    parsed = json.loads(text)
                    script = parsed.get("script", "")
                    prompts = parsed.get("image_prompts", [])
                    caption = parsed.get("caption", "")
                    if script and len(prompts) >= 1 and caption:
                        print(f"✅ സ്ക്രിപ്റ്റും {len(prompts)} ഇമേജ് പ്രോംപ്റ്റുകളും ലഭിച്ചു!")
                        return script, prompts, caption
                except json.JSONDecodeError:
                    print("⚠️ JSON ഡീകോഡിംഗ് എറർ, അടുത്ത മോഡൽ...")
                    continue
        except Exception as e:
            print(f"⚠️ എറർ: {e}")
            continue
    return None, [], None


# ---------- Image sources ----------
def save_fitted(img_bytes, filename):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img = ImageOps.fit(img, (1080, 1920), Image.Resampling.LANCZOS)
    img.save(filename, quality=92)


def try_pollinations(prompt, filename):
    q = urllib.parse.quote(f"{prompt}, hyper-realistic, vertical portrait 9:16")
    url = f"https://image.pollinations.ai/prompt/{q}?width=1080&height=1920&nologo=true&seed={random.randint(1, 99999)}"
    for attempt in range(2):
        try:
            res = requests.get(url, headers=UA, timeout=60)
            if res.status_code == 200 and res.headers.get("content-type", "").startswith("image"):
                save_fitted(res.content, filename)
                return True
            print(f"   ⚠️ Pollinations status {res.status_code}")
            if res.status_code in (402, 429):
                time.sleep(15)   # rate limit: wait and retry once
        except Exception as e:
            print(f"   ⚠️ Pollinations exception: {e}")
            time.sleep(5)
    return False


def try_huggingface(prompt, filename):
    if not HF_API_KEY:
        return False
    # api-inference.huggingface.co is retired -> use the router endpoint
    url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
    headers = {"Authorization": f"Bearer {HF_API_KEY}", "Accept": "image/png"}
    payload = {"inputs": f"{prompt}, vertical portrait 9:16, detailed, 4k"}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=90)
        if res.status_code == 200:
            save_fitted(res.content, filename)
            return True
        print(f"   ⚠️ HF status {res.status_code}: {res.text[:150]}")
    except Exception as e:
        print(f"   ⚠️ HF exception: {e}")
    return False


def try_gemini_image(prompt, filename):
    if not GEMINI_API_KEY:
        return False
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": f"{prompt}. Vertical 9:16 portrait composition."}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    try:
        res = requests.post(url, json=payload, timeout=90)
        data = res.json()
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    save_fitted(base64.b64decode(inline["data"]), filename)
                    return True
        print(f"   ⚠️ Gemini image: {str(data)[:150]}")
    except Exception as e:
        print(f"   ⚠️ Gemini image exception: {e}")
    return False


def make_gradient_background(filename):
    """Last-resort background so the reel never fails."""
    c1 = tuple(random.randint(20, 120) for _ in range(3))
    c2 = tuple(random.randint(100, 230) for _ in range(3))
    img = Image.new("RGB", (1080, 1920))
    px = img.load()
    for y in range(1920):
        t = y / 1919
        row = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
        for x in range(1080):
            px[x, y] = row
    img.save(filename)


def try_wikipedia(topic, filename):
    try:
        term = " ".join(topic.split()[:2])
        wiki_url = ("https://en.wikipedia.org/w/api.php?action=query&format=json&prop=pageimages"
                    f"&generator=search&gsrsearch={urllib.parse.quote(term)}&gsrlimit=1&pithumbsize=1000")
        res = requests.get(wiki_url, headers=UA, timeout=20).json()
        for page in res.get('query', {}).get('pages', {}).values():
            img_url = page.get('thumbnail', {}).get('source')
            if img_url:
                save_fitted(requests.get(img_url, headers=UA, timeout=20).content, filename)
                return True
    except Exception as e:
        print(f"   ⚠️ Wikipedia exception: {e}")
    return False


def generate_multiple_images(prompts, topic):
    print("\n[3] ചിത്രങ്ങൾ ജനറേറ്റ് ചെയ്യാൻ ആരംഭിക്കുന്നു...")
    images = []

    for i, prompt in enumerate(prompts[:3]):
        filename = f"reel_image_{i}.jpg"
        print(f"\nചിത്രം {i+1}: {prompt[:60]}...")

        for name, fn in (("Pollinations", try_pollinations),
                         ("HuggingFace", try_huggingface),
                         ("Gemini", try_gemini_image)):
            print(f"-> {name} ശ്രമിക്കുന്നു...")
            if fn(prompt, filename):
                print(f"✅ ചിത്രം {i+1} {name} വഴി ലഭിച്ചു!")
                images.append(filename)
                break
        time.sleep(5)

    if not images:
        print("\n⚠️ AI ചിത്രങ്ങൾ പരാജയപ്പെട്ടു, വിക്കിപീഡിയ നോക്കുന്നു...")
        if try_wikipedia(topic, "wiki_img.jpg"):
            images.append("wiki_img.jpg")
        else:
            print("⚠️ ഗ്രേഡിയന്റ് ബാക്ക്ഗ്രൗണ്ട് ഉപയോഗിക്കുന്നു...")
            make_gradient_background("fallback_bg.jpg")
            images.append("fallback_bg.jpg")

    return images


# ---------- Audio ----------
def download_random_bgm(filename="bgm.mp3"):
    bgm_links = [
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
    ]
    try:
        res = requests.get(random.choice(bgm_links), headers=UA, timeout=30)
        if res.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(res.content)
            return filename
    except Exception:
        pass
    return None


async def generate_voiceover(script_text, filename="voice.mp3"):
    print("\n[4] മലയാളം ശബ്ദം റെക്കോർഡ് ചെയ്യുന്നു...")
    try:
        communicate = edge_tts.Communicate(script_text, "ml-IN-MidhunNeural")
        await communicate.save(filename)
        return filename
    except Exception as e:
        print(f"❌ TTS error: {e}")
        return None


# ---------- Video ----------
def create_video(image_paths, audio_path, output_path="final_reel.mp4"):
    print(f"\n[5] വീഡിയോ നിർമ്മിക്കുന്നു ({len(image_paths)} ചിത്രങ്ങൾ)...")
    try:
        voice_clip = AudioFileClip(audio_path)
        final_audio = voice_clip

        bgm_path = download_random_bgm()
        if bgm_path and os.path.exists(bgm_path):
            try:
                bgm_clip = AudioFileClip(bgm_path).volumex(0.1)
                if bgm_clip.duration > voice_clip.duration:
                    bgm_clip = bgm_clip.subclip(0, voice_clip.duration)
                final_audio = CompositeAudioClip([voice_clip, bgm_clip])
            except Exception:
                final_audio = voice_clip

        # Make sure the reel has at least 3 slides by cycling images
        slides = list(image_paths)
        while len(slides) < 3:
            slides.append(slides[len(slides) % len(image_paths)])

        duration_per_image = voice_clip.duration / len(slides)
        clips = [ImageClip(p).set_duration(duration_per_image).set_position('center') for p in slides]

        final_video = concatenate_videoclips(clips, method="compose").set_audio(final_audio)
        final_video.write_videofile(output_path, fps=24, codec="libx264",
                                    audio_codec="aac", logger=None)
        print("✅ വീഡിയോ എഡിറ്റിംഗ് പൂർത്തിയായി!")
        return output_path
    except Exception as e:
        print(f"❌ MoviePy Error: {e}")
        return None


# ---------- Facebook upload ----------
def upload_to_facebook(video_path, caption):
    print("\n[6] ഫേസ്ബുക്കിലേക്ക് റീൽസ് അപ്‌ലോഡ് ചെയ്യുന്നു...")
    reels_url = f"{FB_API}/me/video_reels"

    # Step 1: start
    res1 = requests.post(reels_url, data={'upload_phase': 'start',
                                          'access_token': FB_PAGE_ACCESS_TOKEN}, timeout=60).json()
    video_id = res1.get('video_id')
    if not video_id:
        print(f"❌ Init Failed: {res1}")
        return False

    # Step 2: upload binary
    upload_url = f"https://rupload.facebook.com/video-upload/v18.0/{video_id}"
    headers = {
        'Authorization': f'OAuth {FB_PAGE_ACCESS_TOKEN}',
        'offset': '0',
        'file_size': str(os.path.getsize(video_path)),
    }
    with open(video_path, 'rb') as f:
        res2 = requests.post(upload_url, headers=headers, data=f, timeout=300)
    if res2.status_code != 200:
        print(f"❌ Upload Failed: {res2.status_code} {res2.text}")
        return False
    print(f"   Upload response: {res2.text[:200]}")

    # Step 3: finish + publish
    payload = {
        'upload_phase': 'finish',
        'video_id': video_id,
        'video_state': 'PUBLISHED',
        'description': caption,
        'access_token': FB_PAGE_ACCESS_TOKEN,
    }
    res3 = requests.post(reels_url, data=payload, timeout=60).json()
    if res3.get('success'):
        print("🎉 റീൽസ് വിജയകരമായി പോസ്റ്റ് ചെയ്തു!")
        return True
    print(f"❌ Publish Failed: {res3}")
    return False


async def main():
    print("🚀 Auto Facebook Reels Bot ആരംഭിക്കുന്നു...\n")
    if not check_facebook_permissions():
        sys.exit(1)

    topic = get_trending_topic()
    script, img_prompts, caption = get_gemini_script(topic)
    if not (script and img_prompts and caption):
        print("❌ ജെമിനിയിൽ നിന്ന് ഡാറ്റ ലഭിച്ചില്ല.")
        sys.exit(1)

    image_paths = generate_multiple_images(img_prompts, topic)
    audio_path = await generate_voiceover(script)
    if not audio_path:
        print("❌ വോയ്‌സ് ഓവർ പരാജയപ്പെട്ടു.")
        sys.exit(1)

    video_path = create_video(image_paths, audio_path)
    if not video_path:
        print("❌ വീഡിയോ നിർമ്മിക്കാൻ കഴിഞ്ഞില്ല.")
        sys.exit(1)

    if not upload_to_facebook(video_path, caption):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())