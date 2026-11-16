import os
import random
import requests
import feedparser
import urllib.parse
import asyncio
import io
from PIL import Image
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip

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
    
    print("⚠️ ഫീഡ് കിട്ടിയില്ല, ഡിഫോൾട്ട് വിഷയം എടുക്കുന്നു.")
    return "ഇന്നത്തെ പ്രധാന വാർത്തകൾ"

def get_available_models():
    """ജെമിനി അക്കൗണ്ടിലുള്ള ആക്ടീവ് ആയ മോഡലുകൾ എടുക്കാൻ"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    available_models = []
    try:
        response = requests.get(url, timeout=30)
        data = response.json()
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    # 'models/' എന്ന ഭാഗം ഒഴിവാക്കി പേര് മാത്രം എടുക്കുന്നു
                    available_models.append(m['name'].split('/')[-1])
    except Exception as e:
        print("Model Fetch Error:", e)
    return available_models

def get_gemini_script(topic):
    available_models = get_available_models()
    
    # നമ്മൾ ആദ്യം ട്രൈ ചെയ്യാൻ ആഗ്രഹിക്കുന്ന മോഡലുകൾ
    preferences = ['gemini-1.5-flash-latest', 'gemini-1.5-flash', 'gemini-1.5-pro-latest', 'gemini-pro']
    
    # അക്കൗണ്ടിൽ ഉള്ള മോഡലുകൾ മാത്രം ലിസ്റ്റിൽ എടുക്കുന്നു
    models_to_try = [m for m in preferences if m in available_models]
    
    # ഇഷ്ടമുള്ളവ ഇല്ലെങ്കിൽ അക്കൗണ്ടിലുള്ള ബാക്കി മോഡലുകൾ കൂട്ടി ചേർക്കുന്നു
    for m in available_models:
        if m not in models_to_try:
            models_to_try.append(m)

    if not models_to_try:
        print("❌ ഒരു മോഡലും ലഭ്യമായില്ല! API Key ശരിയാണോ എന്ന് നോക്കുക.")
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
                else:
                    print(f"⚠️ {model}: ഫോർമാറ്റ് തെറ്റാണ്, അടുത്തതിലേക്ക് പോകുന്നു.")
            else:
                print(f"❌ {model} പരാജയപ്പെട്ടു: {data.get('error', {}).get('message', 'Unknown Error')}")
                
        except Exception as e:
            print(f"❌ {model} കണക്ഷൻ എറർ: {e}")
            continue # എറർ വന്നാൽ അടുത്ത മോഡലിലേക്ക് പോകും
            
    print("❌ എല്ലാ മോഡലുകളും പരാജയപ്പെട്ടു.")
    return None, None, None

def generate_flux_image(prompt, filename="reel_image.jpg"):
    print("\nHugging Face (FLUX.1) വഴി ചിത്രം ജനറേറ്റ് ചെയ്യുന്നു...")
    url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {"inputs": f"{prompt}, vertical portrait layout 9:16, highly detailed, 4k, cinematic lighting"}
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=90)
        if res.status_code == 200:
            image = Image.open(io.BytesIO(res.content))
            image = image.resize((1080, 1920), Image.Resampling.LANCZOS)
            image.save(filename)
            print("✅ ചിത്രം വിജയകരമായി ഡൗൺലോഡ് ചെയ്തു!")
            return filename
        else:
             print(f"❌ HF API Error: {res.status_code} - {res.text}")
             return None
    except Exception as e:
        print(f"❌ HF Exception: {e}")
    return None

async def generate_voiceover(script_text, filename="voice.mp3"):
    print("\nമലയാളം ശബ്ദം (Edge-TTS) റെക്കോർഡ് ചെയ്യുന്നു...")
    try:
        communicate = edge_tts.Communicate(script_text, "ml-IN-MidhunNeural")
        await communicate.save(filename)
        print("✅ ഓഡിയോ തയ്യാറായി!")
        return filename
    except Exception as e:
        print(f"❌ TTS Error: {e}")
        return None

def create_video(image_path, audio_path, output_path="final_reel.mp4"):
    print("\nMoviePy ഉപയോഗിച്ച് വീഡിയോ എഡിറ്റ് ചെയ്യുന്നു...")
    try:
        audio = AudioFileClip(audio_path)
        clip = ImageClip(image_path).set_duration(audio.duration)
        clip = clip.resize(lambda t: 1 + 0.02 * t).set_position('center')
        video = clip.set_audio(audio)
        video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", logger=None)
        print("✅ വീഡിയോ എഡിറ്റിംഗ് പൂർത്തിയായി!")
        return output_path
    except Exception as e:
        print(f"❌ MoviePy Error: {e}")
        return None

def upload_to_facebook(video_path, caption):
    print("\nഫേസ്ബുക്കിലേക്ക് റീൽസ് അപ്‌ലോഡ് ചെയ്യുന്നു...")
    
    # Step 1: Initialize
    print("Step 1: Initializing...")
    init_url = f"https://graph.facebook.com/v18.0/{FB_PAGE_ID}/video_reels"
    res1 = requests.post(init_url, data={'upload_phase': 'start', 'access_token': FB_PAGE_ACCESS_TOKEN}).json()
    video_id = res1.get('video_id')
    if not video_id: 
        print("❌ Init Failed:", res1)
        return

    # Step 2: Upload
    print(f"Step 2: Uploading (Video ID: {video_id})...")
    upload_url = f"https://rupload.facebook.com/video-upload/v18.0/{video_id}"
    headers = {'Authorization': f'OAuth {FB_PAGE_ACCESS_TOKEN}', 'offset': '0', 'file_size': str(os.path.getsize(video_path))}
    with open(video_path, 'rb') as f:
        res2 = requests.post(upload_url, headers=headers, data=f)
    if res2.status_code != 200: 
        print("❌ Upload Failed:", res2.text)
        return

    # Step 3: Publish
    print("Step 3: Publishing...")
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
        img_path = generate_flux_image(img_prompt)
        
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
            print("❌ ചിത്രം ഡൗൺലോഡ് ചെയ്യാൻ കഴിഞ്ഞില്ല.")
    else:
        print("❌ ജെമിനിയിൽ നിന്ന് ആവശ്യമായ ഡാറ്റ ലഭിച്ചില്ല. വർക്ക്ഫ്ലോ നിർത്തുന്നു.")

if __name__ == "__main__":
    asyncio.run(main())
