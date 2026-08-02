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
import re
import shutil
import subprocess
import tempfile
import time

import anthropic
import requests
from mutagen.mp3 import MP3

from wiro_client import run_model, download_output

STYLE_GUIDE = (
    "cinematic documentary B-roll style, warm dramatic lighting, "
    "tech/gaming themed, atmospheric and detailed, 16:9"
)

MODEL = "claude-sonnet-4-6"
MODEL_UTILITY = "claude-haiku-4-5-20251001"
SENTENCES_PER_SCENE = 3  # her sahne ~3 cümle - stok video sık değişsin
MAX_SCENES = 120 # güvenlik üst sınırı, render süresi patlamasın

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"

SCENE_LIBRARY_DIR = "assets/scene_library"
SCENE_LIBRARY_INDEX = os.path.join(SCENE_LIBRARY_DIR, "index.json")
MIN_TAG_OVERLAP = 2  # en az bu kadar ortak etiket varsa "benzer" say

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

MIN_STOCK_DURATION_RATIO = 0.7  # kaynak klip, hedef süresinin en az bu oranı kadar olmalı

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)


def estimate_target_clip_length(scene_count: int) -> float | None:
    """
    audio/final_mix.mp3 (6. adımda üretilir, bu script 7. adımda çalışır,
    yani dosya zaten hazırdır) süresini okuyup sahne sayısına bölerek
    HER klibin yaklaşık ne kadar süreceğini tahmin eder. Bu tahmin,
    Pexels'ten SEÇERKEN çok kısa (döngüye girip tekrar tekrar oynayacak)
    klipleri elemek için kullanılır - amaç, tek bir sahnenin kendi
    içinde aynı birkaç saniyelik görüntünün defalarca tekrarlanmasını
    önlemek.
    """
    audio_path = "audio/final_mix.mp3"
    if not os.path.exists(audio_path) or scene_count <= 0:
        return None
    try:
        duration = MP3(audio_path).info.length
        return duration / scene_count
    except Exception:
        return None


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
    """
    Script'i CÜMLE bazlı böler, her sahne SENTENCES_PER_SCENE (3) cümle
    içerir - böylece stok video sık sık (her 2-3 cümlede bir) değişir,
    tek bir görüntü uzun süre ekranda kalmaz. MAX_SCENES sadece bir
    güvenlik üst sınırı - script çok uzun çıkarsa render süresi/maliyeti
    kontrolsüz büyümesin diye devreye girer.
    """
    normalized = re.sub(r"\n{2,}", " ", script_text).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()]

    if not sentences:
        return [script_text.strip()] if script_text.strip() else []

    group_size = SENTENCES_PER_SCENE
    if -(-len(sentences) // group_size) > MAX_SCENES:
        group_size = -(-len(sentences) // MAX_SCENES)

    merged = []
    for i in range(0, len(sentences), group_size):
        merged.append(" ".join(sentences[i:i + group_size]))
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

    # NOT: Bu basit bir sınıflandırma/etiketleme işi, video başına 15-20
    # kez tekrarlandığı için ucuz modelle (Haiku) yapılıyor - Sonnet'e
    # göre çok daha düşük maliyetli, kalite kaybı bu iş için ihmal
    # edilebilir düzeyde.
    raw = call_claude(client, prompt, model=MODEL_UTILITY)
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


def concat_clips(clip_paths: list, out_path: str) -> bool:
    """
    Birden fazla stok klibi (farklı çözünürlük/fps olabilir) tek bir
    videoda birleştirir. Amaç: tek bir kısa klibi döngüye sokup aynı
    görüntüyü tekrar tekrar oynatmak yerine, FARKLI klipleri art arda
    göstererek hedef süreyi doldurmak. Başarısız olursa False döner.
    """
    try:
        inputs = []
        for p in clip_paths:
            inputs += ["-i", p]

        filter_parts = []
        labels = []
        for idx in range(len(clip_paths)):
            label = f"v{idx}"
            filter_parts.append(
                f"[{idx}:v]scale=1280:720:force_original_aspect_ratio=increase,"
                f"crop=1280:720,fps=25,setsar=1[{label}]"
            )
            labels.append(f"[{label}]")
        concat_expr = "".join(labels) + f"concat=n={len(clip_paths)}:v=1:a=0[outv]"
        filter_complex = ";".join(filter_parts) + ";" + concat_expr

        subprocess.run(
            [
                "ffmpeg", "-y", *inputs,
                "-filter_complex", filter_complex,
                "-map", "[outv]",
                "-an",
                "-pix_fmt", "yuv420p",
                out_path,
            ],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def search_pexels_video(query: str, api_key: str, out_path: str,
                         used_video_ids: set, target_clip_length: float | None) -> bool:
    """
    Pexels'te sorguyu arar. Aynı video (çalıştırma) içinde DAHA ÖNCE
    kullanılmış bir klip KESİNLİKLE tekrar seçilmez.

    ÖNEMLİ (döngü/tekrar sorunu önleme), üç kademeli strateji:
      1. Süresi hedefin en az %70'i kadar olan TEK bir klip varsa onu
         kullan (en temiz sonuç, döngüye hiç gerek kalmaz).
      2. Yoksa, birden fazla FARKLI kısa klibi birleştirerek (concat)
         hedef süreyi doldurmaya çalış - aynı görüntü değil, farklı
         görüntüler art arda gösterilir.
      3. O da olmazsa (nadir), en son çare olarak tek kısa klip kullanılır
         (assemble_video.py bunu döngüye sokar - eskisi gibi).

    Hiçbir uygun/kullanılmamış klip yoksa False döner (bu durumda
    çağıran taraf AI görsel üretimine düşer).
    """
    if not api_key or not query:
        return False

    try:
        resp = requests.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 15, "orientation": "landscape"},
            timeout=20,
        )
        resp.raise_for_status()
        results = resp.json().get("videos", [])
        if not results:
            return False

        def extract_best_file(video):
            candidates = [
                f for f in video.get("video_files", [])
                if f.get("file_type") == "video/mp4" and f.get("width")
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda f: (f["width"] < 1280, abs(f["width"] - 1280)))
            return candidates[0]

        min_duration = target_clip_length * MIN_STOCK_DURATION_RATIO if target_clip_length else None

        def collect_eligible(require_min_duration: bool):
            eligible = []
            for video in results:
                if video.get("id") in used_video_ids:
                    continue
                if require_min_duration and min_duration:
                    if video.get("duration", 0) < min_duration:
                        continue
                best_file = extract_best_file(video)
                if best_file:
                    eligible.append((video, best_file))
                if len(eligible) >= 5:
                    break
            return eligible

        # 1) Tek başına yeterince uzun bir klip var mı?
        eligible_long = collect_eligible(require_min_duration=True)
        if eligible_long:
            chosen_video, chosen_file = random.choice(eligible_long)
            video_resp = requests.get(chosen_file["link"], stream=True, timeout=60)
            video_resp.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
            used_video_ids.add(chosen_video.get("id"))
            return True

        # 2) Tek uzun klip yok - kısa klipleri BİRLEŞTİRMEYİ dene
        eligible_short = collect_eligible(require_min_duration=False)
        if not eligible_short:
            return False

        if min_duration and len(eligible_short) > 1:
            combo, total = [], 0.0
            for video, file in eligible_short:
                combo.append((video, file))
                total += video.get("duration", 0) or 0
                if total >= min_duration:
                    break

            if total >= min_duration and len(combo) > 1:
                temp_paths = []
                try:
                    for video, file in combo:
                        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                        tmp.close()
                        r = requests.get(file["link"], stream=True, timeout=60)
                        r.raise_for_status()
                        with open(tmp.name, "wb") as f:
                            for chunk in r.iter_content(chunk_size=1 << 16):
                                f.write(chunk)
                        temp_paths.append(tmp.name)

                    if concat_clips(temp_paths, out_path):
                        for video, _ in combo:
                            used_video_ids.add(video.get("id"))
                        return True
                    # concat başarısız oldu, aşağıdaki tekli-klip
                    # yedeğine düş
                finally:
                    for p in temp_paths:
                        if os.path.exists(p):
                            os.remove(p)

        # 3) En son çare: tek kısa klip (döngüye girecek, ama hiç
        # görüntü olmamasından iyidir)
        chosen_video, chosen_file = random.choice(eligible_short)
        video_resp = requests.get(chosen_file["link"], stream=True, timeout=60)
        video_resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in video_resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
        used_video_ids.add(chosen_video.get("id"))
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
    used_video_ids = set()  # aynı çalıştırma içinde tekrar klip seçilmesin
    target_clip_length = estimate_target_clip_length(len(scenes))
    if target_clip_length:
        print(f"Tahmini hedef klip süresi: ~{target_clip_length:.1f}sn "
              f"(kısa klipler döngü sorununu önlemek için elenecek)")

    stock_count = 0
    ai_count = 0
    reused_count = 0

    for i, scene_text in enumerate(scenes, start=1):
        plan = plan_scene(client, scene_text)

        got_stock = False
        if plan.get("use_stock"):
            mp4_path = os.path.join(args.out, f"scene_{i:03d}.mp4")
            got_stock = search_pexels_video(plan.get("stock_query", ""), pexels_key,
                                             mp4_path, used_video_ids, target_clip_length)
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
