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

def check_facebook_permissions():
    print(f"\n[1] ഫേസ്ബുക്ക് കണക്ഷൻ പരിശോധിക്കുന്നു...")
    if not FB_PAGE_ACCESS_TOKEN:
        print("❌ എറർ: GitHub Secrets-ൽ FB_PAGE_ACCESS_TOKEN നൽകിയിട്ടില്ല!")
        return False
        
    url = "https://" + f"graph.facebook.com/v18.0/me?fields=name&access_token={FB_PAGE_ACCESS_TOKEN}"
    try:
        res = requests.get(url).json()
        if 'name' in res:
            print(f"✅ ഫേസ്ബുക്ക് പേജ് കണക്ട് ആയി: {res['name']}")
            return True
        else:
            print(f"❌ ഫേസ്ബുക്ക് കണക്ഷൻ എറർ: നിങ്ങളുടെ ടോക്കൺ തെറ്റാണ്!")
            return False
    except Exception as e:
        return False

def get_trending_topic():
    print("\n[2] ട്രെൻഡിങ് വിഷയം തിരയുന്നു...")
    keywords = ["കേരളം", "ഇന്ത്യൻ ഫുട്ബോൾ", "സഞ്ജു സാംസൺ", "ടെക്നോളജി", "സിനിമ", "കേരള രാഷ്ട്രീയം", "ദുബായ് വാർത്തകൾ", "വിചിത്രമായ വാർത്തകൾ"]
    keyword = random.choice(keywords)
    encoded = urllib.parse.quote(keyword)
    rss_url = "https://" + f"news.google.com/rss/search?q={encoded}&hl=ml&gl=IN&ceid=IN:ml"
    
    try:
        response = requests.get(rss_url, timeout=30)
        feed = feedparser.parse(response.content)
        if feed.entries:
            topic = random.choice(feed.entries[:5]).title
            print(f"✅ വിഷയം കണ്ടെത്തി: {topic}")
            return topic
    except:
        pass
    return "ഇന്നത്തെ പ്രധാന വാർത്തകൾ"

def get_available_models():
    url = "https://" + f"generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    available_models = []
    try:
        response = requests.get(url, timeout=30)
        data = response.json()
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    available_models.append(m['name'].split('/')[-1])
    except:
        pass
    return available_models

def get_gemini_script(topic):
    available_models = get_available_models()
    preferences = ['gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it']
    models_to_try = [m for m in preferences if m in available_models]
    for m in available_models:
        if m not in models_to_try:
            models_to_try.append(m)

    prompt = f"""
    You are an expert Facebook Reels creator. Create content about the topic: '{topic}'.
    IMPORTANT: You MUST respond ONLY with a valid JSON object. Do not add any markdown, asterisks, or extra text like "Here is the JSON".
    
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
        url = "https://" + f"generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=60)
            data = res.json()
            if 'candidates' in data:
                text = data['candidates'][0]['content']['parts'][0]['text']
                
                clean_text = text.strip()
                if clean_text.startswith("```"):
                    clean_text = clean_text.split('\n', 1)[-1].rsplit('\n', 1)[0]
                if clean_text.startswith("json"):
                    clean_text = clean_text[4:].strip()
                
                try:
                    parsed_data = json.loads(clean_text)
                    script = parsed_data.get("script", "")
                    image_prompts = parsed_data.get("image_prompts", [])
                    caption = parsed_data.get("caption", "")
                    
                    if script and len(image_prompts) >= 1 and caption:
                        print(f"✅ സ്ക്രിപ്റ്റും {len(image_prompts)} ഇമേജ് പ്രോംപ്റ്റുകളും കൃത്യമായി ലഭിച്ചു!")
                        return script, image_prompts, caption
                except json.JSONDecodeError:
                    print("⚠️ JSON ഡീകോഡിംഗ് എറർ, അടുത്ത മോഡൽ നോക്കുന്നു...")
                    continue
        except Exception as e:
            print(f"⚠️ എറർ: {e}")
            continue
            
    return None, [], None

def generate_multiple_images(prompts, topic):
    print("\n[3] ചിത്രങ്ങൾ ജനറേറ്റ് ചെയ്യാൻ ആരംഭിക്കുന്നു...")
    downloaded_images = []
    
    for i, prompt in enumerate(prompts[:3]):
        filename = f"reel_image_{i}.jpg"
        print(f"\nചിത്രം {i+1} ജനറേറ്റ് ചെയ്യുന്നു... പ്രോംപ്റ്റ്: {prompt[:50]}...")
        success = False

        # Option A: Pollinations.ai 
        print("-> Option A: Pollinations.ai വഴി ശ്രമിക്കുന്നു...")
        try:
            encoded_prompt = urllib.parse.quote(f"{prompt}, attractive, hyper-realistic, vertical portrait 9:16")
            poll_url = "https://" + f"image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true"
            res = requests.get(poll_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
            if res.status_code == 200:
                img = Image.open(io.BytesIO(res.content))
                img = ImageOps.fit(img, (1080, 1920), Image.Resampling.LANCZOS)
                img.save(filename)
                downloaded_images.append(filename)
                print(f"✅ ചിത്രം {i+1} Pollinations.ai വഴി ലഭിച്ചു!")
                success = True
            else:
                print(f"⚠️ Pollinations Error: Status {res.status_code}")
        except Exception as e:
            print(f"⚠️ Pollinations Exception: {e}")

        # Option B: Hugging Face 
        if not success:
            print("-> Option B: Hugging Face വഴി ശ്രമിക്കുന്നു...")
            try:
                hf_url = "https://" + "api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
                headers = {"Authorization": f"Bearer {HF_API_KEY}", "User-Agent": "Mozilla/5.0"}
                payload = {"inputs": f"{prompt}, vertical portrait layout 9:16, attractive, detailed, 4k"}
                res = requests.post(hf_url, headers=headers, json=payload, timeout=45)
                if res.status_code == 200:
                    img = Image.open(io.BytesIO(res.content))
                    img = ImageOps.fit(img, (1080, 1920), Image.Resampling.LANCZOS)
                    img.save(filename)
                    downloaded_images.append(filename)
                    print(f"✅ ചിത്രം {i+1} Hugging Face വഴി ലഭിച്ചു!")
                    success = True
                else:
                    print(f"⚠️ HF Error: Status {res.status_code} - {res.text[:100]}")
            except Exception as e:
                print(f"⚠️ HF Exception: {e}")
                
        time.sleep(3) 
    
    if not downloaded_images:
        print("\n⚠️ AI ചിത്രങ്ങൾ പരാജയപ്പെട്ടു, യഥാർത്ഥ ചിത്രം തിരയുന്നു...")
        try:
            search_term = " ".join(topic.split()[:2])
            wiki_url = "https://" + f"en.wikipedia.org/w/api.php?action=query&format=json&prop=pageimages&generator=search&gsrsearch={urllib.parse.quote(search_term)}&gsrlimit=1&pithumbsize=1000"
            res = requests.get(wiki_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).json()
            pages = res.get('query', {}).get('pages', {})
            for page_id in pages:
                img_url = pages[page_id].get('thumbnail', {}).get('source')
                if img_url:
                    img_data = requests.get(img_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).content
                    img = Image.open(io.BytesIO(img_data))
                    img = ImageOps.fit(img, (1080, 1920), Image.Resampling.LANCZOS)
                    img.save("wiki_img.jpg")
                    downloaded_images.append("wiki_img.jpg")
                    print("✅ വിക്കിപീഡിയ വഴി യഥാർത്ഥ ചിത്രം ലഭിച്ചു!")
                    break
        except Exception as e:
            print(f"⚠️ Wikipedia Exception: {e}")

    return downloaded_images

def download_random_bgm(filename="bgm.mp3"):
    bgm_links = [
        "https://" + "upload.wikimedia.org/wikipedia/commons/b/b2/Silence.ogg", 
        "https://" + "[www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3](https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3)",
        "https://" + "[www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3](https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3)"
    ]
    try:
        res = requests.get(random.choice(bgm_links), timeout=30)
        if res.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(res.content)
            return filename
    except:
        pass
    return None

async def generate_voiceover(script_text, filename="voice.mp3"):
    print("\n[4] മലയാളം ശബ്ദം റെക്കോർഡ് ചെയ്യുന്നു...")
    try:
        communicate = edge_tts.Communicate(script_text, "ml-IN-MidhunNeural")
        await communicate.save(filename)
        return filename
    except:
        return None

def create_video(image_paths, audio_path, output_path="final_reel.mp4"):
    print(f"\n[5] വീഡിയോ നിർമ്മിക്കുന്നു ({len(image_paths)} ചിത്രങ്ങൾ ഉപയോഗിച്ച്)...")
    try:
        voice_clip = AudioFileClip(audio_path)
        bgm_path = download_random_bgm()
        
        if bgm_path and os.path.exists(bgm_path):
            try:
                bgm_clip = AudioFileClip(bgm_path).volumex(0.1) 
                if bgm_clip.duration > voice_clip.duration:
                    bgm_clip = bgm_clip.subclip(0, voice_clip.duration)
                final_audio = CompositeAudioClip([voice_clip, bgm_clip])
            except:
                final_audio = voice_clip
        else:
            final_audio = voice_clip

        duration_per_image = voice_clip.duration / len(image_paths)
        
        clips = []
        for img_path in image_paths:
            clip = ImageClip(img_path).set_duration(duration_per_image).set_position('center')
            clips.append(clip)

        final_video = concatenate_videoclips(clips, method="compose")
        final_video = final_video.set_audio(final_audio)
        
        final_video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", logger=None)
        print("✅ സ്ലൈഡ്‌ഷോ വീഡിയോ എഡിറ്റിംഗ് പൂർത്തിയായി!")
        return output_path
    except Exception as e:
        print(f"❌ MoviePy Error: {e}")
        return None

def upload_to_facebook(video_path, caption):
    print("\n[6] ഫേസ്ബുക്കിലേക്ക് റീൽസ് അപ്‌ലോഡ് ചെയ്യുന്നു...")
    init_url = "https://" + "[graph.facebook.com/v18.0/me/video_reels](https://graph.facebook.com/v18.0/me/video_reels)"
    res1 = requests.post(init_url, data={'upload_phase': 'start', 'access_token': FB_PAGE_ACCESS_TOKEN}).json()
    video_id = res1.get('video_id')
    if not video_id: 
        print(f"❌ Init Failed: {res1}")
        return

    upload_url = "https://" + f"[rupload.facebook.com/video-upload/v18.0/](https://rupload.facebook.com/video-upload/v18.0/){video_id}"
    headers = {'Authorization': f'OAuth {FB_PAGE_ACCESS_TOKEN}', 'offset': '0', 'file_size': str(os.path.getsize(video_path))}
    with open(video_path, 'rb') as f:
        res2 = requests.post(upload_url, headers=headers, data=f)
    if res2.status_code != 200: 
        print(f"❌ Upload Failed: {res2.text}")
        return

    payload = {'upload_phase': 'finish', 'video_id': video_id, 'video_state': 'PUBLISHED', 'description': caption, 'access_token': FB_PAGE_ACCESS_TOKEN}
    res3 = requests.post(init_url, data=payload).json()
    if res3.get('success'):
        print("🎉 റീൽസ് വിജയകരമായി ഫേസ്ബുക്കിൽ പോസ്റ്റ് ചെയ്തു!")

async def main():
    print("🚀 Auto Facebook Reels Bot ആരംഭിക്കുന്നു...\n")
    if not check_facebook_permissions():
        sys.exit(1)
    
    topic = get_trending_topic()
    script, img_prompts, caption = get_gemini_script(topic)
    
    if script and img_prompts and caption:
        image_paths = generate_multiple_images(img_prompts, topic)
        
        if image_paths:
            audio_path = await generate_voiceover(script)
            if audio_path:
                video_path = create_video(image_paths, audio_path)
                if video_path:
                    upload_to_facebook(video_path, caption)
                else:
                    print("❌ വീഡിയോ നിർമ്മിക്കാൻ കഴിഞ്ഞില്ല.")
            else:
                print("❌ വോയ്‌സ് ഓവർ റെക്കോർഡ് ചെയ്യാൻ കഴിഞ്ഞില്ല.")
        else:
            print("❌ ചിത്രങ്ങൾ ലഭ്യമല്ലാത്തതിനാൽ സ്കിപ്പ് ചെയ്യുന്നു.")
    else:
        print("❌ ജെമിനിയിൽ നിന്ന് ഡാറ്റ ലഭിച്ചില്ല.")

if __name__ == "__main__":
    asyncio.run(main())
