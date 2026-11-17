import os
import random
import requests
import feedparser
import urllib.parse
import asyncio
import io
import time
from PIL import Image

# === MOVIEPY ERROR FIX ===
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
# =========================

import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, CompositeAudioClip

# --- Environment Variables ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
HF_API_KEY = os.environ.get("HF_API_KEY")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")

def get_trending_topic():
    print("ട്രെൻഡിങ് വിഷയം തിരയുന്നു...")
    keywords = ["കേരളം", "ഇന്ത്യൻ ഫുട്ബോൾ", "സഞ്ജു സാംസൺ", "ടെക്നോളജി", "സിനിമ", "കേരള രാഷ്ട്രീയം"]
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
    except Exception as e:
        print("RSS Fetch Error:", e)
        pass
    
    return "ഇന്നത്തെ പ്രധാന വാർത്തകൾ"

def get_available_models():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    available_models = []
    try:
        response = requests.get(url, timeout=30)
        data = response.json()
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    available_models.append(m['name'].split('/')[-1])
    except Exception as e:
        print("Model Fetch Error:", e)
    return available_models

def get_gemini_script(topic):
    available_models = get_available_models()
    preferences = ['gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it', 'gemini-1.5-flash-latest']
    models_to_try = [m for m in preferences if m in available_models]
    
    for m in available_models:
        if m not in models_to_try:
            models_to_try.append(m)

    if not models_to_try:
        print("❌ ഒരു മോഡലും ലഭ്യമായില്ല!")
        return None, None, None

    prompt = f"""
    വിഷയം: '{topic}'
    ഇതിനെക്കുറിച്ച് ഒരു ഫേസ്ബുക്ക് റീൽസ് ഉണ്ടാക്കാൻ താഴെ പറയുന്ന 3 കാര്യങ്ങൾ തയ്യാറാക്കുക. മറുപടി കൃത്യമായി '|' ഉപയോഗിച്ച് വേർതിരിക്കണം. വേറെ ഒന്നും എഴുതരുത്.
    ഫോർമാറ്റ്: [60 സെക്കൻഡിൽ വായിക്കാൻ കഴിയുന്ന മലയാളം വോയ്‌സ് ഓവർ സ്ക്രിപ്റ്റ്] | [ഈ വീഡിയോയ്ക്ക് ചേരുന്ന ഹൈ-ക്വാളിറ്റി ഫോട്ടോ ജനറേറ്റ് ചെയ്യാനുള്ള ഇംഗ്ലീഷ് പ്രോംപ്റ്റ്] | [ഫേസ്ബുക്കിൽ ഇടാനുള്ള മലയാളം ക്യാപ്ഷനും ഹാഷ്‌ടാഗുകളും]
    """
    
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    for model in models_to_try:
        print(f"\n{model} ഉപയോഗിച്ച് സ്ക്രിപ്റ്റ് തയ്യാറാക്കുന്നു...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=60)
            data = res.json()
            
            if 'candidates' in data:
                text = data['candidates'][0]['content']['parts'][0]['text']
                parts = text.strip().split('|')
                if len(parts) >= 3:
                    print(f"✅ {model} ഉപയോഗിച്ച് സ്ക്രിപ്റ്റ് വിജയകരമായി ലഭിച്ചു!")
                    return parts[0].strip(), parts[1].strip(), parts[2].strip()
        except Exception as e:
            continue
            
    return None, None, None

def generate_image(prompt, topic, filename="reel_image.jpg"):
    print("\nചിത്രം ജനറേറ്റ് ചെയ്യാൻ ശ്രമിക്കുന്നു...")
    
    # Option A: Hugging Face
    try:
        hf_url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
        headers = {"Authorization": f"Bearer {HF_API_KEY}", "User-Agent": "Mozilla/5.0"}
        payload = {"inputs": f"{prompt}, vertical portrait layout 9:16, highly detailed, 4k"}
        res = requests.post(hf_url, headers=headers, json=payload, timeout=30)
        if res.status_code == 200:
            Image.open(io.BytesIO(res.content)).resize((1080, 1920), Image.Resampling.LANCZOS).save(filename)
            print("✅ Hugging Face വഴി ചിത്രം ലഭിച്ചു!")
            return filename
    except:
        pass

    # Option B: Pollinations.ai
    try:
        encoded_prompt = urllib.parse.quote(f"{prompt}, vertical portrait layout 9:16")
        poll_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true"
        res = requests.get(poll_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if res.status_code == 200:
            Image.open(io.BytesIO(res.content)).resize((1080, 1920), Image.Resampling.LANCZOS).save(filename)
            print("✅ Pollinations.ai വഴി ചിത്രം ലഭിച്ചു!")
            return filename
    except:
        pass

    # Option C: Wikipedia Image
    try:
        search_term = " ".join(topic.split()[:2])
        wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&format=json&prop=pageimages&generator=search&gsrsearch={urllib.parse.quote(search_term)}&gsrlimit=1&pithumbsize=1000"
        res = requests.get(wiki_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).json()
        pages = res.get('query', {}).get('pages', {})
        for page_id in pages:
            img_url = pages[page_id].get('thumbnail', {}).get('source')
            if img_url:
                img_data = requests.get(img_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).content
                Image.open(io.BytesIO(img_data)).resize((1080, 1920), Image.Resampling.LANCZOS).save(filename)
                print("✅ വിക്കിപീഡിയ വഴി യഥാർത്ഥ ചിത്രം ലഭിച്ചു!")
                return filename
    except:
        pass

    print("❌ ചിത്രങ്ങൾ ലഭ്യമായില്ല.")
    return None

async def generate_voiceover(script_text, filename="voice.mp3"):
    print("\nമലയാളം ശബ്ദം (Edge-TTS) റെക്കോർഡ് ചെയ്യുന്നു...")
    try:
        communicate = edge_tts.Communicate(script_text, "ml-IN-MidhunNeural")
        await communicate.save(filename)
        return filename
    except Exception as e:
        print(f"❌ TTS Error: {e}")
        return None

def download_random_bgm(filename="bgm.mp3"):
    print("\nഡയറക്ടറിയിൽ നിന്ന് റാൻഡം BGM തിരഞ്ഞെടുക്കുന്നു...")
    
    bgm_links = [
        "https://upload.wikimedia.org/wikipedia/commons/b/b2/Silence.ogg", 
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3"
    ]
    
    selected_url = random.choice(bgm_links)
    
    try:
        res = requests.get(selected_url, timeout=30)
        if res.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(res.content)
            print("✅ റാൻഡം BGM വിജയകരമായി ഡൗൺലോഡ് ചെയ്തു!")
            return filename
    except Exception as e:
        print(f"⚠️ BGM Download Error: {e}")
    
    return None

def create_video(image_path, audio_path, output_path="final_reel.mp4"):
    print("\nMoviePy ഉപയോഗിച്ച് വീഡിയോയും BGM-ഉം മിക്സ് ചെയ്യുന്നു...")
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

        image_clip = ImageClip(image_path).set_duration(voice_clip.duration)
        image_clip = image_clip.resize(lambda t: 1 + 0.02 * t).set_position('center')
        
        video = image_clip.set_audio(final_audio)
        video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", logger=None)
        
        print("✅ വീഡിയോ എഡിറ്റിംഗും BGM മിക്സിംഗും പൂർത്തിയായി!")
        return output_path
    except Exception as e:
        print(f"❌ MoviePy Error: {e}")
        return None

def upload_to_facebook(video_path, caption):
    print("\nഫേസ്ബുക്കിലേക്ക് റീൽസ് അപ്‌ലോഡ് ചെയ്യുന്നു...")
    
    init_url = f"https://graph.facebook.com/v18.0/{FB_PAGE_ID}/video_reels"
    res1 = requests.post(init_url, data={'upload_phase': 'start', 'access_token': FB_PAGE_ACCESS_TOKEN}).json()
    video_id = res1.get('video_id')
    if not video_id: 
        print("❌ Init Failed:", res1)
        return

    upload_url = f"https://rupload.facebook.com/video-upload/v18.0/{video_id}"
    headers = {'Authorization': f'OAuth {FB_PAGE_ACCESS_TOKEN}', 'offset': '0', 'file_size': str(os.path.getsize(video_path))}
    with open(video_path, 'rb') as f:
        res2 = requests.post(upload_url, headers=headers, data=f)
    if res2.status_code != 200: 
        print("❌ Upload Failed:", res2.text)
        return

    payload = {'upload_phase': 'finish', 'video_id': video_id, 'video_state': 'PUBLISHED', 'description': caption, 'access_token': FB_PAGE_ACCESS_TOKEN}
    res3 = requests.post(init_url, data=payload).json()
    if res3.get('success'):
        print("🎉 റീൽസ് വിജയകരമായി ഫേസ്ബുക്കിൽ പോസ്റ്റ് ചെയ്തു!")
    else:
        print(f"❌ Facebook Publish Failed: {res3}")

async def main():
    print("🚀 Auto Facebook Reels Bot ആരംഭിക്കുന്നു...\n")
    
    topic = get_trending_topic()
    script, img_prompt, caption = get_gemini_script(topic)
    
    if script and img_prompt and caption:
        img_path = generate_image(img_prompt, topic)
        
        if img_path:
            audio_path = await generate_voiceover(script)
            
            if audio_path:
                video_path = create_video(img_path, audio_path)
                
                if video_path:
                    upload_to_facebook(video_path, caption)
                else:
                    print("❌ വീഡിയോ നിർമ്മിക്കാൻ കഴിഞ്ഞില്ല.")
            else:
                print("❌ വോയ്‌സ് ഓവർ റെക്കോർഡ് ചെയ്യാൻ കഴിഞ്ഞില്ല.")
        else:
            print("❌ ചിത്രം ലഭ്യമല്ലാത്തതിനാൽ സ്കിപ്പ് ചെയ്യുന്നു.")
    else:
        print("❌ ജെമിനിയിൽ നിന്ന് ഡാറ്റ ലഭിച്ചില്ല.")

if __name__ == "__main__":
    asyncio.run(main())
