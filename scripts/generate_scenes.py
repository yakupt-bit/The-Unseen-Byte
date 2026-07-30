"""
Script'i sahnelere ayırır. Her sahne için ÖNCE Claude'a sahnenin GENEL/
ATMOSFERİK mi yoksa script'e ÖZGÜ/BENZERSİZ bir an mı olduğunu sorar:

  - GENEL/ATMOSFERİK sahneler (çoğunluk) -> Pexels'ten stok video aranır
    (ör. klavye yazan eller, sunucu odası, retro konsol genel çekimi).
    Bulunursa scene_XXX.mp4 olarak kaydedilir.
  - ÖZGÜ/BENZERSİZ sahneler (script'in tam olarak bahsettiği tek bir
    nesne/an) veya Pexels'te uygun sonuç bulunamazsa -> önce GÖRSEL
    KÜTÜPHANESİNDE (assets/scene_library/) benzer etiketli bir görsel
    aranır (maliyet tasarrufu); bulunamazsa AI görsel üretimine (Wiro,
    openai/gpt-image-2) düşülür ve üretilen görsel gelecekte tekrar
    kullanılabilmesi için kütüphaneye eklenir.

assemble_video.py hem .png (Ken Burns animasyonu) hem .mp4 (stok video,
trim/loop) sahneleri birlikte işleyebiliyor.

ÖNEMLİ: assets/scene_library/index.json ve içindeki görseller, bu
script çalıştıktan SONRA workflow'un "Save topic history" adımında
repoya commit edilmeli ki bir sonraki çalıştırmada da kullanılabilsin.

Claude API çağrıları geçici hatalara (500, rate limit, bağlantı
kopması) karşı otomatik olarak yeniden dener (bkz. call_claude).

Kullanım:
    python scripts/generate_scenes.py --script script.md --out scenes/
"""
import argparse
import hashlib
import json
import os
import random
import shutil
import time

import anthropic
import requests

from wiro_client import run_model, download_output

STYLE_GUIDE = (
    "cinematic documentary B-roll style, warm dramatic lighting, "
    "tech/gaming themed, atmospheric and detailed, 16:9"
)

MODEL = "claude-sonnet-4-6"
MAX_SCENES = 20

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"

SCENE_LIBRARY_DIR = "assets/scene_library"
SCENE_LIBRARY_INDEX = os.path.join(SCENE_LIBRARY_DIR, "index.json")
MIN_TAG_OVERLAP = 2  # en az bu kadar ortak etiket varsa "benzer" say

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)


def call_claude(client, prompt, model=MODEL, max_tokens=300):
    """Claude'a istek atar; geçici hatalarda (500/rate limit/bağlantı)
    üstel bekleme ile otomatik olarak yeniden dener."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in response.content if b.type == "text")
        except RETRYABLE_EXCEPTIONS as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  UYARI: Claude API geçici hata ({type(e).__name__}), "
                      f"{delay}sn sonra tekrar deneniyor "
                      f"(deneme {attempt + 1}/{MAX_RETRIES})...")
                time.sleep(delay)
    raise last_error


def split_into_scenes(script_text: str):
    paragraphs = [p.strip() for p in script_text.split("\n\n") if p.strip()]
    if len(paragraphs) <= MAX_SCENES:
        return paragraphs

    merged = []
    group_size = -(-len(paragraphs) // MAX_SCENES)
    for i in range(0, len(paragraphs), group_size):
        merged.append("\n\n".join(paragraphs[i:i + group_size]))
    return merged


def plan_scene(client, narration_paragraph: str) -> dict:
    """
    Sahne için stok video arama sorgusu, AI görsel tarifi, "stok video
    için uygun mu" kararı VE görsel kütüphanesinde eşleşme aramak için
    jenerik etiketleri tek çağrıda üretir.
    """
    prompt = f"""Aşağıdaki YouTube anlatım paragrafı için bir sahne planı
üret.

ÖNCE KARAR VER: Bu paragraf GENEL/ATMOSFERİK bir an mı (ör. bir
kavramı, ortamı, genel bir eylemi anlatıyor - stok video ile
karşılanabilir) yoksa script'in TAM OLARAK bahsettiği TEK, BENZERSİZ,
SPESİFİK bir nesne/an mı (ör. belirli bir prototipin belirli bir
detayı - sadece özel üretilmiş bir görselle karşılanabilir)?
Mümkün olduğunca "genel/atmosferik" (use_stock: true) sınıflandırmayı
tercih et, çünkü stok video daha gerçekçi durur - sadece gerçekten
script'e özgü, somut bir detay varsa use_stock: false yap.

Üret:
1. "use_stock": true veya false (yukarıdaki karara göre)
2. "stock_query": İngilizce, 2-4 kelimelik, JENERİK bir stok video
   arama sorgusu (ör. "server room lights", "retro game console",
   "typing keyboard close up"). Marka/gerçek kişi/oyun adı KULLANMA.
3. "visual_prompt": AI görsel üretimi için İngilizce, 1-2 cümlelik
   sahne tarifi (use_stock false ise, stok bulunamazsa, ya da
   kütüphanede eşleşme yoksa kullanılacak yedek). Gerçek kişi/marka/
   oyun adı kullanma, insan yüzünü minimize et (silüet/arkadan çekim/
   eller tercih et), şiddet/silah/kan içerme.
4. "tags": 3-6 adet İngilizce, JENERİK, tekil kelime/kısa öbeklerden
   oluşan bir liste - bu görsel BAŞKA bir videoda da benzer bir sahne
   gerektiğinde eşleştirme için kullanılacak (ör. ["server room",
   "blue lighting", "cables", "dark atmosphere"]). Marka/oyun adı
   KULLANMA, sadece görselin genel içeriğini/atmosferini tarif eden
   jenerik kelimeler kullan.

ANLATIM PARAGRAFI:
{narration_paragraph}

Çıktı SADECE JSON: {{"use_stock": true, "stock_query": "...", "visual_prompt": "...", "tags": ["...", "..."]}}"""

    raw = call_claude(client, prompt)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "use_stock": False,
            "stock_query": "",
            "visual_prompt": "a moody abstract technology visualization, "
                              "no people, no text",
            "tags": [],
        }


def load_library() -> list:
    if not os.path.exists(SCENE_LIBRARY_INDEX):
        return []
    try:
        with open(SCENE_LIBRARY_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_library(library: list):
    os.makedirs(SCENE_LIBRARY_DIR, exist_ok=True)
    with open(SCENE_LIBRARY_INDEX, "w", encoding="utf-8") as f:
        json.dump(library, f, ensure_ascii=False, indent=2)


def find_reusable_image(library: list, tags: list):
    """Etiket kümesi en çok örtüşen kütüphane kaydını döndürür (eşik
    üzerindeyse). Bulamazsa None döner."""
    if not tags:
        return None

    tags_set = {t.lower().strip() for t in tags}
    best_entry = None
    best_overlap = 0

    for entry in library:
        entry_tags = {t.lower().strip() for t in entry.get("tags", [])}
        overlap = len(tags_set & entry_tags)
        if overlap > best_overlap:
            best_overlap = overlap
            best_entry = entry

    if best_entry and best_overlap >= MIN_TAG_OVERLAP:
        return best_entry
    return None


def add_to_library(library: list, tags: list, image_path: str):
    """Üretilen görseli kütüphaneye (kalıcı klasöre) kopyalar ve
    index'e etiketleriyle birlikte kaydeder."""
    if not tags:
        return

    os.makedirs(SCENE_LIBRARY_DIR, exist_ok=True)
    with open(image_path, "rb") as f:
        content_hash = hashlib.sha1(f.read()).hexdigest()[:12]
    stored_path = os.path.join(SCENE_LIBRARY_DIR, f"{content_hash}.png")

    if not os.path.exists(stored_path):
        shutil.copyfile(image_path, stored_path)

    library.append({"tags": tags, "file": stored_path})
    save_library(library)


def search_pexels_video(query: str, api_key: str, out_path: str) -> bool:
    """Pexels'te sorguyu arar, ilk uygun klibi indirir. Bulamazsa False döner."""
    if not api_key or not query:
        return False

    try:
        resp = requests.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 5, "orientation": "landscape"},
            timeout=20,
        )
        resp.raise_for_status()
        results = resp.json().get("videos", [])
        if not results:
            return False

        video = results[0]
        candidates = [
            f for f in video.get("video_files", [])
            if f.get("file_type") == "video/mp4" and f.get("width")
        ]
        if not candidates:
            return False

        # Gereksiz yere çok büyük dosya indirmemek için 1280 genişliğine
        # en yakın (ama altına düşmeyen) dosyayı tercih et.
        candidates.sort(key=lambda f: (f["width"] < 1280, abs(f["width"] - 1280)))
        best = candidates[0]

        video_resp = requests.get(best["link"], stream=True, timeout=60)
        video_resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in video_resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
        return True

    except (requests.RequestException, KeyError, IndexError):
        return False


def generate_image(prompt: str, out_path: str):
    full_prompt = f"{STYLE_GUIDE}. Scene: {prompt}"
    task = run_model("openai", "gpt-image-2", {
        "prompt": full_prompt,
        "resolution": "1k",
        "ratio": "16:9",
        "quality": "medium",
        "samples": 1,
    })
    download_output(task, out_path)


FALLBACK_PROMPTS = [
    "a moody abstract technology visualization, flowing data streams, deep blue and purple gradient, no people, no text",
    "a dramatic close-up of glowing circuit board patterns, warm amber lighting, macro photography style, no people, no text",
    "an atmospheric server room corridor with soft blue light trails, cinematic depth of field, no people, no text",
    "a stack of retro gaming cartridges and consoles on a wooden desk, warm dramatic side lighting, no people, no text",
    "an abstract network of glowing connected nodes on a dark background, cinematic and mysterious, no people, no text",
]


def generate_image_with_fallback(client, visual_prompt: str, out_path: str):
    try:
        generate_image(visual_prompt, out_path)
        return
    except RuntimeError as e:
        if "safety system" not in str(e).lower():
            raise

    print("  UYARI: güvenlik reddi, daha soyut bir tarifle tekrar deniyorum...")
    try:
        stricter_prompt = (
            "a completely abstract, symbolic visual representation (no "
            "literal depiction) inspired by this idea, purely artistic "
            f"shapes/colors/lighting only: {visual_prompt[:150]}"
        )
        generate_image(stricter_prompt, out_path)
        return
    except RuntimeError as e:
        if "safety system" not in str(e).lower():
            raise

    print("  UYARI: ikinci deneme de reddedildi, jenerik görsellerden biriyle devam ediyorum...")
    fallback_prompt = random.choice(FALLBACK_PROMPTS)
    generate_image(fallback_prompt, out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    with open(args.script, "r", encoding="utf-8") as f:
        script_text = f.read()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    pexels_key = os.environ.get("PEXELS_API_KEY", "")
    scenes = split_into_scenes(script_text)
    library = load_library()

    stock_count = 0
    ai_count = 0
    reused_count = 0

    for i, scene_text in enumerate(scenes, start=1):
        plan = plan_scene(client, scene_text)

        got_stock = False
        if plan.get("use_stock"):
            mp4_path = os.path.join(args.out, f"scene_{i:03d}.mp4")
            got_stock = search_pexels_video(plan.get("stock_query", ""), pexels_key, mp4_path)
            if got_stock:
                stock_count += 1
                print(f"Sahne {i}/{len(scenes)}: STOK video -> {mp4_path} "
                      f"(sorgu: \"{plan.get('stock_query')}\")")

        if not got_stock:
            png_path = os.path.join(args.out, f"scene_{i:03d}.png")
            tags = plan.get("tags", [])
            reusable = find_reusable_image(library, tags)

            if reusable and os.path.exists(reusable["file"]):
                shutil.copyfile(reusable["file"], png_path)
                reused_count += 1
                print(f"Sahne {i}/{len(scenes)}: KÜTÜPHANEDEN yeniden kullanıldı "
                      f"-> {png_path} (eşleşen etiketler: {tags})")
            else:
                generate_image_with_fallback(client, plan.get("visual_prompt", ""), png_path)
                add_to_library(library, tags, png_path)
                ai_count += 1
                print(f"Sahne {i}/{len(scenes)}: AI görsel (yeni, kütüphaneye "
                      f"eklendi) -> {png_path}")

    print(f"\nToplam: {stock_count} stok video, {ai_count} yeni AI görsel, "
          f"{reused_count} kütüphaneden yeniden kullanıldı "
          f"({len(scenes)} sahne)")


if __name__ == "__main__":
    main()
