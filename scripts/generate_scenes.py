"""
Script'i sahnelere ayırır ve HER sahneyi Pexels STOK VİDEOSU ile
doldurur. AI görsel üretimi (Wiro / openai gpt-image-2) TAMAMEN
KALDIRILDI - artık hiçbir koşulda Wiro'ya task açılmaz, dolayısıyla
Wiro bakiyesi / eşzamanlılık limiti (code 96) bu adımı ASLA düşüremez.

SAHNE DOLDURMA STRATEJİSİ (her sahne için sırayla, ilk başarılı olan
kullanılır - hiçbir aşamada job KIRILMAZ):
  1. Claude'un ürettiği sahneye ÖZGÜ stok sorgusu (stock_query) ile
     Pexels aranır.
  2. Bulunamazsa, JENERİK YEDEK SORGULAR (GENERIC_FALLBACK_QUERIES)
     sırayla denenir - nişe uygun genel tech/gaming görüntüleri.
  3. O da bulunamazsa (ör. Pexels'te uygun kalmadıysa / hepsi bu koşuda
     kullanıldıysa), EN YAKIN (bir önceki başarılı) stok klibi kopyalanır.
  4. Hiç önceki klip yoksa (ör. ilk sahne herşeyde başarısız), en son
     çare olarak düz renkli kısa bir kart (ffmpeg) üretilir.

Böylece scene_images adımı HER ZAMAN eksiksiz bir sahne seti üretir,
tek bir sahne yüzünden onlarca tamamlanmış sahne çöpe gitmez.

assemble_video.py stok video (.mp4) sahnelerini trim/loop ile işler.

Claude API çağrıları geçici hatalara (500, rate limit, bağlantı
kopması) karşı otomatik olarak yeniden dener (bkz. call_claude).

Kullanım:
    python scripts/generate_scenes.py --script script.md --out scenes/
"""
import argparse
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

MODEL = "claude-sonnet-4-6"
MODEL_UTILITY = "claude-haiku-4-5-20251001"
SENTENCES_PER_SCENE = 2  # her sahne ~2 cümle - stok video sık değişsin
MAX_SCENES = 120  # güvenlik üst sınırı, render süresi patlamasın

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

MIN_STOCK_DURATION_RATIO = 0.7  # kaynak klip, hedef süresinin en az bu oranı kadar olmalı

# Sahneye özgü sorgu Pexels'te sonuç vermezse sırayla denenecek jenerik
# yedek sorgular. Nişe (tech/gaming/mystery) uygun, her zaman bol
# sonuç veren genel görüntüler. used_video_ids sayesinde aynı koşuda
# aynı klip iki kez seçilmez, bu yüzden bu havuz görsel tekrarını da
# minimize eder.
GENERIC_FALLBACK_QUERIES = [
    "abstract technology background",
    "digital data network",
    "glowing circuit board macro",
    "server room lights",
    "futuristic technology motion",
    "retro electronics close up",
    "dark cinematic tech background",
    "computer hardware components",
]

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)


def estimate_target_clip_length(scene_count: int) -> float | None:
    """
    audio/final_mix.mp3 mevcutsa süresini okuyup sahne sayısına bölerek
    HER klibin yaklaşık ne kadar süreceğini tahmin eder; Pexels'ten
    SEÇERKEN çok kısa klipleri elemek için kullanılır. NOT: bu adım
    (scene_images) pipeline'da final_mix'i indirmediği için dosya
    çoğu zaman burada YOKTUR - o durumda None döner ve süre filtresi
    devre dışı kalır (sorun değil, tekli-klip yedeği devreye girer).
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
    Script'i CÜMLE bazlı böler, her sahne SENTENCES_PER_SCENE cümle
    içerir - böylece stok video sık sık değişir, tek bir görüntü uzun
    süre ekranda kalmaz. MAX_SCENES sadece bir güvenlik üst sınırı.
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


def make_scene_query(client, narration_paragraph: str) -> str:
    """
    Sahne için İngilizce, 2-4 kelimelik JENERİK bir Pexels stok video
    arama sorgusu üretir (ucuz Haiku modeliyle). Herhangi bir hata /
    parse sorununda boş string döner - çağıran taraf jenerik yedek
    sorgulara düşer, pipeline kırılmaz.
    """
    prompt = f"""Aşağıdaki YouTube anlatım paragrafı için, Pexels'te
STOK VİDEO aramaya uygun İngilizce, 2-4 kelimelik JENERİK bir arama
sorgusu üret. Marka / gerçek kişi / oyun adı KULLANMA. Genel, kolay
bulunur bir görüntü olsun (ör. "server room lights", "retro game
console", "typing keyboard close up", "circuit board macro").

ANLATIM PARAGRAFI:
{narration_paragraph}

Çıktı SADECE JSON: {{"stock_query": "..."}}"""
    try:
        raw = call_claude(client, prompt, model=MODEL_UTILITY, max_tokens=120)
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned)
        q = str(data.get("stock_query", "")).strip()
        return q
    except Exception:
        return ""


def concat_clips(clip_paths: list, out_path: str) -> bool:
    """
    Birden fazla stok klibi (farklı çözünürlük/fps olabilir) tek bir
    videoda birleştirir - tek kısa klibi döngüye sokmak yerine FARKLI
    klipleri art arda göstererek hedef süreyi doldurur. Başarısızsa
    False döner.
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
    Pexels'te sorguyu arar. Aynı çalıştırma içinde DAHA ÖNCE kullanılmış
    bir klip tekrar seçilmez. Üç kademeli: (1) yeterince uzun tek klip,
    (2) birden fazla kısa klibi birleştir, (3) tek kısa klip. Hiçbir
    uygun/kullanılmamış klip yoksa False döner.
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
                    # concat başarısız, aşağıdaki tekli-klip yedeğine düş
                finally:
                    for p in temp_paths:
                        if os.path.exists(p):
                            os.remove(p)

        # 3) En son çare: tek kısa klip
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


def find_stock_for_scene(client, scene_text: str, pexels_key: str, mp4_path: str,
                         used_video_ids: set, target_clip_length: float | None,
                         scene_index: int):
    """
    Bir sahne için stok video bulmayı dener: önce sahneye özgü sorgu,
    olmazsa jenerik yedek sorgular (her sahnede farklı bir sıradan
    başlar ki hep aynı klip tükenmesin). Başarılı olursa kullanılan
    sorgunun jenerik olup olmadığını (bool) ve True döner; hiçbiri
    tutmazsa (None, False) döner.
    """
    specific_query = make_scene_query(client, scene_text)

    queries = []
    if specific_query:
        queries.append(specific_query)

    # Jenerik havuzu sahne indeksine göre döndür (rotate) - böylece her
    # sahne farklı bir jenerik sorguyla başlar, ilk sorgular tükenmez.
    rot = scene_index % len(GENERIC_FALLBACK_QUERIES)
    rotated_generics = GENERIC_FALLBACK_QUERIES[rot:] + GENERIC_FALLBACK_QUERIES[:rot]
    queries += rotated_generics

    for qi, query in enumerate(queries):
        if search_pexels_video(query, pexels_key, mp4_path, used_video_ids, target_clip_length):
            is_generic = (specific_query == "") or (qi > 0)
            return query, is_generic, True

    return None, False, False


def make_placeholder_clip(out_path: str, duration: float | None):
    """En son çare: hiç stok bulunamaz ve kopyalanacak önceki klip de
    yoksa, düz koyu renkli kısa bir kart üretir - böylece sahne dosyası
    yine de oluşur ve pipeline kırılmaz."""
    dur = duration if (duration and duration > 0) else 5.0
    dur = max(3.0, min(dur, 20.0))
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c=0x0a0e14:s=1280x720:r=25:d={dur:.1f}",
         "-pix_fmt", "yuv420p", out_path],
        check=True, capture_output=True,
    )


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
    used_video_ids = set()  # aynı çalıştırma içinde tekrar klip seçilmesin
    target_clip_length = estimate_target_clip_length(len(scenes))
    if target_clip_length:
        print(f"Tahmini hedef klip süresi: ~{target_clip_length:.1f}sn "
              f"(kısa klipler döngü sorununu önlemek için elenecek)")

    stock_count = 0
    generic_count = 0
    reused_count = 0
    placeholder_count = 0
    last_good_clip = None  # yalnızca GERÇEK indirilen stok klip

    for i, scene_text in enumerate(scenes, start=1):
        mp4_path = os.path.join(args.out, f"scene_{i:03d}.mp4")

        query, is_generic, got_stock = find_stock_for_scene(
            client, scene_text, pexels_key, mp4_path,
            used_video_ids, target_clip_length, scene_index=i - 1,
        )

        if got_stock:
            stock_count += 1
            if is_generic:
                generic_count += 1
            tag = "STOK video (jenerik yedek)" if is_generic else "STOK video"
            print(f"Sahne {i}/{len(scenes)}: {tag} -> {mp4_path} (sorgu: \"{query}\")")
            last_good_clip = mp4_path
            continue

        # Stok hiç bulunamadı - en yakın (önceki) klibi kopyala
        if last_good_clip and os.path.exists(last_good_clip):
            shutil.copyfile(last_good_clip, mp4_path)
            reused_count += 1
            print(f"Sahne {i}/{len(scenes)}: stok bulunamadı, EN YAKIN önceki "
                  f"klip kopyalandı -> {mp4_path}")
            continue

        # Hiç önceki klip de yok - en son çare düz renk kartı
        make_placeholder_clip(mp4_path, target_clip_length)
        placeholder_count += 1
        print(f"Sahne {i}/{len(scenes)}: stok/önceki klip yok, düz renk "
              f"kartı üretildi -> {mp4_path}")

    print(f"\nToplam: {stock_count} stok video "
          f"({generic_count} jenerik yedek sorguyla), "
          f"{reused_count} en-yakın-klip kopyası, "
          f"{placeholder_count} renk kartı ({len(scenes)} sahne)")


if __name__ == "__main__":
    main()
