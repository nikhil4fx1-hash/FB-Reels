import os
import random
import requests
import feedparser
import urllib.parse
import asyncio
import io
from PIL import Image
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

# --- Environment Variables ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
HF_API_KEY = os.environ.get("HF_API_KEY")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")

def get_trending_topic():
    keywords = ["കേരളം", "ഇന്ത്യൻ ഫുട്ബോൾ", "സഞ്ജു സാംസൺ", "ടെക്നോളജി", "സിനിമ"]
    keyword = random.choice(keywords)
    encoded = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=ml&gl=IN&ceid=IN:ml"
    
    try:
        response = requests.get(rss_url, timeout=30)
        feed = feedparser.parse(response.content)
        if feed.entries:
            return random.choice(feed.entries[:5]).title
    except:
        pass
    return "ഇന്നത്തെ പ്രധാന വാർത്തകൾ"

def get_gemini_script(topic):
    print(f"ജെമിനി വഴി സ്ക്രിപ്റ്റ് തയ്യാറാക്കുന്നു. വിഷയം: {topic}")
    prompt = f"""
    വിഷയം: '{topic}'
    ഇതിനെക്കുറിച്ച് ഒരു ഫേസ്ബുക്ക് റീൽസ് ഉണ്ടാക്കാൻ താഴെ പറയുന്ന 3 കാര്യങ്ങൾ തയ്യാറാക്കുക. മറുപടി '|' ഉപയോഗിച്ച് വേർതിരിക്കണം.
    ഫോർമാറ്റ്: [60 സെക്കൻഡിൽ വായിക്കാൻ കഴിയുന്ന മലയാളം വോയ്‌സ് ഓവർ സ്ക്രിപ്റ്റ്] | [ഈ വീഡിയോയ്ക്ക് ചേരുന്ന ഹൈ-ക്വാളിറ്റി ഫോട്ടോ ജനറേറ്റ് ചെയ്യാനുള്ള ഇംഗ്ലീഷ് പ്രോംപ്റ്റ്] | [ഫേസ്ബുക്കിൽ ഇടാനുള്ള മലയാളം ക്യാപ്ഷനും ഹാഷ്‌ടാഗുകളും]
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    try:
        res = requests.post(url, json=payload, headers={'Content-Type': 'application/json'})
        text = res.json()['candidates'][0]['content']['parts'][0]['text']
        parts = text.strip().split('|')
        if len(parts) >= 3:
            return parts[0].strip(), parts[1].strip(), parts[2].strip()
    except Exception as e:
        print("Gemini Error:", e)
    return None, None, None

def generate_flux_image(prompt, filename="reel_image.jpg"):
    print("Hugging Face (FLUX.1) വഴി ചിത്രം ജനറേറ്റ് ചെയ്യുന്നു...")
    url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {"inputs": f"{prompt}, vertical portrait layout 9:16, highly detailed, 4k, cinematic"}
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=90)
        if res.status_code == 200:
            image = Image.open(io.BytesIO(res.content))
            image = image.resize((1080, 1920), Image.Resampling.LANCZOS)
            image.save(filename)
            return filename
    except Exception as e:
        print("HF Error:", e)
    return None

async def generate_voiceover(script_text, filename="voice.mp3"):
    print("മലയാളം ശബ്ദം (Edge-TTS) റെക്കോർഡ് ചെയ്യുന്നു...")
    communicate = edge_tts.Communicate(script_text, "ml-IN-MidhunNeural")
    await communicate.save(filename)
    return filename

def create_video(image_path, audio_path, output_path="final_reel.mp4"):
    print("MoviePy ഉപയോഗിച്ച് വീഡിയോ എഡിറ്റ് ചെയ്യുന്നു...")
    audio = AudioFileClip(audio_path)
    # ആനിമേഷൻ എഫക്ട് നൽകി വീഡിയോ ആക്കുന്നു (ചെറിയ സൂം ഇൻ)
    clip = ImageClip(image_path).set_duration(audio.duration)
    # GitHub Actions-ൽ വേഗത്തിൽ റെൻഡർ ആകാൻ ലളിതമായ സൂം ഉപയോഗിക്കുന്നു
    clip = clip.resize(lambda t: 1 + 0.02 * t).set_position('center')
    
    video = clip.set_audio(audio)
    # fps 24 നൽകിയത് റെൻഡറിങ് വേഗത കൂട്ടാനാണ്
    video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac")
    return output_path

def upload_to_facebook(video_path, caption):
    print("ഫേസ്ബുക്കിലേക്ക് റീൽസ് അപ്‌ലോഡ് ചെയ്യുന്നു...")
    # Step 1: Initialize
    init_url = f"https://graph.facebook.com/v18.0/{FB_PAGE_ID}/video_reels"
    res1 = requests.post(init_url, data={'upload_phase': 'start', 'access_token': FB_PAGE_ACCESS_TOKEN}).json()
    video_id = res1.get('video_id')
    if not video_id: return print("Init Failed:", res1)

    # Step 2: Upload
    upload_url = f"https://rupload.facebook.com/video-upload/v18.0/{video_id}"
    headers = {'Authorization': f'OAuth {FB_PAGE_ACCESS_TOKEN}', 'offset': '0', 'file_size': str(os.path.getsize(video_path))}
    with open(video_path, 'rb') as f:
        res2 = requests.post(upload_url, headers=headers, data=f)
    if res2.status_code != 200: return print("Upload Failed")

    # Step 3: Publish
    payload = {'upload_phase': 'finish', 'video_id': video_id, 'video_state': 'PUBLISHED', 'description': caption, 'access_token': FB_PAGE_ACCESS_TOKEN}
    res3 = requests.post(init_url, data=payload).json()
    print("Facebook Publish Success!" if res3.get('success') else f"Failed: {res3}")

async def main():
    topic = get_trending_topic()
    script, img_prompt, caption = get_gemini_script(topic)
    
    if script and img_prompt:
        img_path = generate_flux_image(img_prompt)
        audio_path = await generate_voiceover(script)
        
        if img_path and audio_path:
            video_path = create_video(img_path, audio_path)
            upload_to_facebook(video_path, caption)
        else:
            print("ചിത്രമോ ശബ്ദമോ നിർമ്മിക്കുന്നതിൽ പിഴവ്.")
    else:
        print("സ്ക്രിപ്റ്റ് ലഭിച്ചില്ല.")

if __name__ == "__main__":
    asyncio.run(main())
