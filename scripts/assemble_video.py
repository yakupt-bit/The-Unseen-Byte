"""
scenes/ klasöründeki görselleri VE stok video kliplerini, sesin süresine
göre klipler haline getirip aralarına 15 farklı geçiş efektinden birini
koyarak birleştirir.

İki sahne türü desteklenir:
  - scene_XXX.png (AI görsel): SÜREKLİ hareket eden Ken Burns animasyonu
    (zoom-in/zoom-out + pan) uygulanır - klip boyunca asla sabit kalmaz.
  - scene_XXX.mp4 (Pexels stok video): orijinal hareketi korunur, klip
    süresine göre trim/loop edilir (kendi hareketi zaten var, ayrıca
    Ken Burns uygulanmaz).
Her iki türde de animasyon/geçiş stili art arda gelen klipler arasında
TEKRARLANMAZ.

--- BUGÜNKÜ EKLEME: İLK 30 SANİYEDE YOĞUN GÖRSEL TEMPOSU ---
Rakip analizinde (Ömer/Akgün eğitimi) gözlemlenen ve kanıtlanmış bir
teknik: izleyiciyi videoya "kilitlemek" için ilk 30 saniyede normalden
ÇOK DAHA SIK sahne geçişi (5-6 görsel) kullanılıyor, sonrasında tempo
normale dönüyor. Eskiden TÜM klipler eşit süreye bölünüyordu
(clip_len = total_duration / n) - artık DEĞİL:
  - İlk INTRO_SCENE_COUNT kadar sahne, INTRO_SECONDS'a sıkıştırılıyor
    (kısa, hızlı klipler).
  - Kalan sahneler, kalan süreye normal (daha uzun) klip süresiyle
    dağıtılıyor.
  - Bu, HİÇBİR YENİ GÖRSEL ÜRETMEDEN yapılıyor - generate_scenes.py'nin
    ürettiği sahne sayısı aynı kalıyor, sadece HANGİ sahnenin ne kadar
    süre ekranda kalacağı değişiyor. Maliyet artışı yok.
  - xfade geçiş offsetleri artık DEĞİŞKEN klip sürelerine göre genel
    bir formülle hesaplanıyor (bkz. chain_with_xfade) - eskiden sadece
    eşit süreler için çalışan sabit formül vardı.

Ekstra bindirmeler:
- VURGU KARTI (sağ üst): çarpıcı bir sayı/istatistik geçtiğinde kısa
  süreliğine görünür.
- BİLGİ KARTI (sol üst, logonun ALTINDA): bir kısaltma/kurum adı (ESRB,
  NASA gibi) geçtiğinde ne olduğunu kısaca açıklayan küçük bir kart -
  köşe logosuyla çakışmaması için y=140'tan başlar.
- KÖŞE LOGOSU (sol ÜST, köşeye yakın ama tam köşede değil): video
  boyunca kalıcı, hafif saydam kanal logosu.

Claude API çağrısı geçici hatalara (500, rate limit, bağlantı kopması)
karşı otomatik olarak yeniden dener (bkz. call_claude).

Kullanım:
    python scripts/assemble_video.py --audio audio/final_mix.mp3 \
        --scenes scenes/ --script script.md --out output/raw.mp4
"""
import argparse
import glob
import json
import os
import random
import re
import subprocess
import time

import anthropic
from mutagen.mp3 import MP3

FPS = 25
RESOLUTION = "1280x720"
XFADE_DURATION = 0.6
FONT_PATH = "assets/fonts/Anton-Regular.ttf"
LOGO_PATH = "assets/branding/logo.png"
SENTENCES_PER_SCENE = 2  # generate_scenes.py ile aynı olmalı
MAX_SCENES = 90  # generate_scenes.py ile aynı olmalı

# --- İlk 30 saniye yoğun tempo ayarları ---
INTRO_SECONDS = 30  # bu süre içinde hızlı geçiş uygulanacak
INTRO_SCENE_COUNT = 6  # ilk 30 saniyeye kaç sahne sıkıştırılsın (n yeterliyse)

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)

TRANSITIONS = [
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circleopen", "circleclose", "smoothleft", "smoothright",
    "diagbl", "diagtr",
]

ANIMATION_STYLES = [
    "zoom_in_left", "zoom_in_right", "zoom_in_up", "zoom_in_down",
    "zoom_out_left", "zoom_out_right", "zoom_out_up", "zoom_out_down",
]


def call_claude(client, prompt, model="claude-haiku-4-5-20251001", max_tokens=800):
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


def get_audio_duration(path: str) -> float:
    return MP3(path).info.length


def split_into_scenes(script_text: str):
    """
    generate_scenes.py ile BİREBİR AYNI mantık (cümle bazlı bölme,
    SENTENCES_PER_SCENE cümlede bir sahne) - aksi halde sahne sayısı
    iki dosya arasında uyumsuz olur ve overlay/klip eşleşmesi bozulur.
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


def gather_scene_files(scenes_dir: str):
    """scene_XXX.png ve scene_XXX.mp4 dosyalarını birlikte, sıra
    numarasına göre sıralanmış olarak döndürür."""
    files = (
        glob.glob(os.path.join(scenes_dir, "scene_*.png"))
        + glob.glob(os.path.join(scenes_dir, "scene_*.mp4"))
    )

    def scene_index(path):
        base = os.path.basename(path)
        num_part = base.split("_", 1)[1].split(".")[0]
        return int(num_part)

    return sorted(files, key=scene_index)


def compute_clip_lengths(n: int, total_duration: float) -> list:
    """
    Her klibin süresini hesaplar - artık EŞİT DEĞİL. İlk
    INTRO_SCENE_COUNT kadar sahne, INTRO_SECONDS'a sıkıştırılarak HIZLI
    bir giriş temposu yaratır (izleyiciyi kilitlemek için); kalan
    sahneler kalan süreye normal (daha uzun) klip süresiyle dağıtılır.

    Toplam sahne sayısı çok azsa (ör. n <= INTRO_SCENE_COUNT) ya da
    ses süresi INTRO_SECONDS'tan kısaysa, güvenli şekilde eski (eşit
    dağıtım) davranışına döner - pipeline hiçbir durumda kırılmaz.
    """
    if n <= INTRO_SCENE_COUNT or total_duration <= INTRO_SECONDS:
        clip_len = (total_duration + (n - 1) * XFADE_DURATION) / n
        clip_len = max(clip_len, XFADE_DURATION + 0.5)
        return [clip_len] * n

    intro_count = INTRO_SCENE_COUNT
    outro_count = n - intro_count

    intro_clip_len = (INTRO_SECONDS + (intro_count - 1) * XFADE_DURATION) / intro_count
    intro_clip_len = max(intro_clip_len, XFADE_DURATION + 0.3)

    remaining_duration = total_duration - INTRO_SECONDS
    outro_clip_len = (remaining_duration + (outro_count - 1) * XFADE_DURATION) / outro_count
    outro_clip_len = max(outro_clip_len, XFADE_DURATION + 0.5)

    return [intro_clip_len] * intro_count + [outro_clip_len] * outro_count


def extract_overlays(client, scenes):
    joined = "\n---\n".join(f"[{i}] {s}" for i, s in enumerate(scenes))
    prompt = f"""Aşağıda numaralı script paragrafları var. Her biri için:

1. "callout": EĞER paragrafta çarpıcı bir SAYI/istatistik varsa, ekranda
   kısaca gösterilecek ÇOK KISA (max 4 kelime) bir metin. Yoksa boş
   string. Her paragrafta olmak zorunda değil.

2. "info_card": EĞER paragrafta bir kısaltma/kurum adı (örn. ESRB, NASA,
   CPU) geçiyorsa, "KISALTMA: kısa açıklama" formatında ÇOK KISA
   (max 8 kelime açıklama) bir bilgi kartı metni. Yoksa boş string.

PARAGRAFLAR:
{joined}

Çıktı SADECE JSON dizi (paragraf sırasıyla, İngilizce metinlerle):
[{{"callout": "27%", "info_card": ""}}, {{"callout": "", "info_card": "ESRB: video game content rating organization"}}, ...]"""

    raw = call_claude(client, prompt)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        overlays = json.loads(cleaned)
    except json.JSONDecodeError:
        overlays = [{"callout": "", "info_card": ""}] * len(scenes)
    if len(overlays) < len(scenes):
        overlays += [{"callout": "", "info_card": ""}] * (len(scenes) - len(overlays))
    return overlays[:len(scenes)]


def build_zoompan_expr(style: str, frames: int):
    pan_amount = 60
    zoom_in_target = 1.35
    zoom_out_start = 1.35

    direction = style.split("_", 2)[2]
    is_zoom_in = style.startswith("zoom_in")

    if is_zoom_in:
        z_expr = f"1+({zoom_in_target}-1)*on/{frames}"
    else:
        z_expr = f"{zoom_out_start}-({zoom_out_start}-1)*on/{frames}"

    if direction == "left":
        x_expr = f"iw/2-(iw/zoom/2)-({pan_amount}*on/{frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif direction == "right":
        x_expr = f"iw/2-(iw/zoom/2)+({pan_amount}*on/{frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif direction == "up":
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)-({pan_amount}*on/{frames})"
    else:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)+({pan_amount}*on/{frames})"

    return z_expr, x_expr, y_expr


def overlay_vf_chain(vf_chain: str, callout_text: str, info_card_text: str,
                      duration: float, out_path: str) -> str:
    def fade_alpha_expr(show_dur):
        return (
            f"if(lt(t,0.25),t/0.25,"
            f"if(lt(t,{show_dur}-0.25),1,"
            f"if(lt(t,{show_dur}),({show_dur}-t)/0.25,0)))"
        )

    if callout_text:
        show_dur = min(3.0, duration * 0.6)
        callout_file = out_path + ".callout.txt"
        with open(callout_file, "w", encoding="utf-8") as f:
            f.write(callout_text)
        vf_chain += (
            f",drawtext=fontfile={FONT_PATH}:textfile={callout_file}:"
            f"fontsize=56:fontcolor=white:box=1:boxcolor=black@0.55:"
            f"boxborderw=16:x=w-tw-50:y=50:alpha='{fade_alpha_expr(show_dur)}'"
        )

    if info_card_text:
        show_dur = min(4.0, duration * 0.6)
        info_file = out_path + ".info.txt"
        with open(info_file, "w", encoding="utf-8") as f:
            f.write(info_card_text)
        vf_chain += (
            f",drawtext=fontfile={FONT_PATH}:textfile={info_file}:"
            f"fontsize=30:fontcolor=white:box=1:boxcolor=black@0.6:"
            f"boxborderw=14:x=50:y=140:alpha='{fade_alpha_expr(show_dur)}'"
        )

    return vf_chain


def make_ken_burns_clip(image_path: str, duration: float, callout_text: str,
                         info_card_text: str, style: str, out_path: str):
    frames = int(duration * FPS)
    z_expr, x_expr, y_expr = build_zoompan_expr(style, frames)

    vf_chain = (
        f"scale=1920:-1,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={RESOLUTION}:fps={FPS}"
    )
    vf_chain = overlay_vf_chain(vf_chain, callout_text, info_card_text, duration, out_path)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-vf", vf_chain,
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            out_path,
        ],
        check=True,
        capture_output=True,
    )


def make_stock_video_clip(video_path: str, duration: float, callout_text: str,
                           info_card_text: str, out_path: str):
    """Stok video klibini hedef çözünürlüğe kırpar/ölçekler, gerekirse
    döngüye alarak (loop) tam olarak `duration` saniyeye sabitler.
    Kendi doğal hareketi olduğu için Ken Burns uygulanmaz."""
    w, h = RESOLUTION.split("x")
    vf_chain = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps={FPS}"
    )
    vf_chain = overlay_vf_chain(vf_chain, callout_text, info_card_text, duration, out_path)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", video_path,
            "-vf", vf_chain,
            "-t", str(duration),
            "-an",
            "-pix_fmt", "yuv420p",
            out_path,
        ],
        check=True,
        capture_output=True,
    )


def chain_with_xfade(clip_paths, clip_lens, out_path):
    """
    Klipleri xfade geçişleriyle birleştirir. ARTIK klip süreleri EŞİT
    OLMAK ZORUNDA DEĞİL (bkz. compute_clip_lengths) - offset hesaplaması
    genel bir kümülatif formülle yapılıyor: offset_i = (i'ye kadarki
    klip sürelerinin toplamı) - i*XFADE_DURATION. Eşit sürelerde bu
    eski sabit formülle (i * (clip_len - XFADE_DURATION)) birebir
    aynı sonucu verir, yani geriye dönük uyumlu.
    """
    n = len(clip_paths)
    if n == 1:
        subprocess.run(["ffmpeg", "-y", "-i", clip_paths[0], "-c", "copy", out_path], check=True)
        return

    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]

    filter_parts = []
    prev_label = "0:v"
    last_transition = None
    cumulative = clip_lens[0]

    for i in range(1, n):
        offset = cumulative - i * XFADE_DURATION
        out_label = f"v{i}" if i < n - 1 else "vout"

        choices = [t for t in TRANSITIONS if t != last_transition]
        transition = random.choice(choices)
        last_transition = transition

        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition={transition}:"
            f"duration={XFADE_DURATION}:offset={offset:.3f}[{out_label}]"
        )
        prev_label = out_label
        cumulative += clip_lens[i]

    filter_complex = ";".join(filter_parts)

    subprocess.run(
        [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-pix_fmt", "yuv420p",
            out_path,
        ],
        check=True,
    )


def add_logo_and_audio(silent_video: str, audio_path: str, out_path: str):
    has_logo = os.path.exists(LOGO_PATH)

    if has_logo:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", silent_video,
                "-loop", "1", "-i", LOGO_PATH,
                "-i", audio_path,
                "-filter_complex",
                "[1:v]scale=80:-1,format=rgba,colorchannelmixer=aa=0.8[logo];"
                "[0:v][logo]overlay=x=30:y=30:shortest=1[vout]",
                "-map", "[vout]", "-map", "2:a",
                "-c:v", "libx264", "-c:a", "aac",
                "-shortest",
                out_path,
            ],
            check=True,
        )
    else:
        print(f"  UYARI: {LOGO_PATH} bulunamadı, logo bindirmesi atlanıyor")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", silent_video,
                "-i", audio_path,
                "-c:v", "libx264", "-c:a", "aac",
                "-shortest",
                out_path,
            ],
            check=True,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--scenes", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    scene_files = gather_scene_files(args.scenes)
    if not scene_files:
        raise SystemExit("scenes/ klasöründe görsel/video bulunamadı")

    with open(args.script, "r", encoding="utf-8") as f:
        script_text = f.read()
    scenes_text = split_into_scenes(script_text)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    overlays = extract_overlays(client, scenes_text[:len(scene_files)])

    total_duration = get_audio_duration(args.audio)
    n = len(scene_files)

    clip_lens = compute_clip_lengths(n, total_duration)

    intro_n = min(INTRO_SCENE_COUNT, n) if (n > INTRO_SCENE_COUNT and total_duration > INTRO_SECONDS) else 0
    if intro_n:
        print(f"{n} sahne -> ilk {intro_n} sahne {INTRO_SECONDS}sn'ye sıkıştırıldı "
              f"(~{clip_lens[0]:.1f}sn/klip, hızlı giriş temposu), "
              f"kalan {n - intro_n} sahne ~{clip_lens[-1]:.1f}sn/klip "
              f"(toplam ~{total_duration:.1f}sn)")
    else:
        print(f"{n} sahne, her biri ~{clip_lens[0]:.1f}sn (geçişlerle toplam ~{total_duration:.1f}sn) "
              f"[giriş yoğunlaştırması atlandı: sahne/süre yetersiz]")

    clip_paths = []
    last_style = None
    stock_count = 0
    ai_count = 0

    for i, scene_file in enumerate(scene_files):
        clip_path = f"clip_{i:03d}.mp4"
        clip_len_i = clip_lens[i]
        callout = overlays[i]["callout"] if i < len(overlays) else ""
        info_card = overlays[i]["info_card"] if i < len(overlays) else ""

        is_stock_video = scene_file.lower().endswith(".mp4")

        if is_stock_video:
            make_stock_video_clip(scene_file, clip_len_i, callout, info_card, clip_path)
            stock_count += 1
            style_label = "stok video (doğal hareket)"
        else:
            style_choices = [s for s in ANIMATION_STYLES if s != last_style]
            style = random.choice(style_choices)
            last_style = style
            make_ken_burns_clip(scene_file, clip_len_i, callout, info_card, style, clip_path)
            ai_count += 1
            style_label = f"animasyon:{style}"

        clip_paths.append(clip_path)
        tags = [style_label, f"{clip_len_i:.1f}sn"]
        if callout:
            tags.append(f"vurgu:'{callout}'")
        if info_card:
            tags.append(f"bilgi:'{info_card}'")
        print(f"  Klip {i+1}/{n} hazır ({', '.join(tags)})")

    print(f"({stock_count} stok video klip, {ai_count} AI görsel klip)")

    silent_video = "silent_video.mp4"
    chain_with_xfade(clip_paths, clip_lens, silent_video)

    add_logo_and_audio(silent_video, args.audio, args.out)

    print(f"Final video hazır -> {args.out}")


if __name__ == "__main__":
    main()
