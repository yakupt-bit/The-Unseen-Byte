"""
scenes/ klasöründeki görselleri, sesin süresine göre Ken Burns
efektiyle (yavaş yakınlaşma + rastgele yön pan) animasyonlu klipler
haline getirir, aralarına 15 farklı geçiş efektinden (art arda AYNISI
tekrarlanmayacak şekilde) birini koyarak birleştirir.

Ekstra bindirmeler:
- VURGU KARTI (sağ üst): çarpıcı bir sayı/istatistik geçtiğinde kısa
  süreliğine görünür.
- BİLGİ KARTI (sol üst): bir kısaltma/kurum adı (ESRB, NASA gibi)
  geçtiğinde ne olduğunu kısaca açıklayan küçük bir kart.
- KÖŞE LOGOSU (sol alt, köşeye yakın ama tam dipte değil): video
  boyunca kalıcı, hafif saydam kanal logosu.

Kullanım:
    python scripts/assemble_video.py --audio audio/final_mix.mp3 \
        --scenes scenes/ --script script.md --out output/raw.mp4
"""
import argparse
import glob
import json
import os
import random
import subprocess

import anthropic
from mutagen.mp3 import MP3

FPS = 25
RESOLUTION = "1280x720"
XFADE_DURATION = 0.6
FONT_PATH = "assets/fonts/Anton-Regular.ttf"
LOGO_PATH = "assets/branding/logo.png"
MAX_SCENES = 20

TRANSITIONS = [
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circleopen", "circleclose", "smoothleft", "smoothright",
    "diagbl", "diagtr",
]


def get_audio_duration(path: str) -> float:
    return MP3(path).info.length


def split_into_scenes(script_text: str):
    paragraphs = [p.strip() for p in script_text.split("\n\n") if p.strip()]
    if len(paragraphs) <= MAX_SCENES:
        return paragraphs

    merged = []
    group_size = -(-len(paragraphs) // MAX_SCENES)
    for i in range(0, len(paragraphs), group_size):
        merged.append("\n\n".join(paragraphs[i:i + group_size]))
    return merged


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

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        overlays = json.loads(cleaned)
    except json.JSONDecodeError:
        overlays = [{"callout": "", "info_card": ""}] * len(scenes)
    if len(overlays) < len(scenes):
        overlays += [{"callout": "", "info_card": ""}] * (len(scenes) - len(overlays))
    return overlays[:len(scenes)]


def make_ken_burns_clip(image_path: str, duration: float, callout_text: str,
                         info_card_text: str, out_path: str):
    frames = int(duration * FPS)

    pan_style = random.choice(["left", "right", "up", "down", "none"])
    pan_amount = 45
    if pan_style == "left":
        x_expr = f"iw/2-(iw/zoom/2)-({pan_amount}*on/{frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif pan_style == "right":
        x_expr = f"iw/2-(iw/zoom/2)+({pan_amount}*on/{frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif pan_style == "up":
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)-({pan_amount}*on/{frames})"
    elif pan_style == "down":
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)+({pan_amount}*on/{frames})"
    else:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    zoom_expr = "min(zoom+0.0012,1.4)"

    vf_chain = (
        f"scale=1920:-1,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={RESOLUTION}:fps={FPS}"
    )

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
            f"boxborderw=14:x=50:y=50:alpha='{fade_alpha_expr(show_dur)}'"
        )

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


def chain_with_xfade(clip_paths, clip_len, out_path):
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

    for i in range(1, n):
        offset = i * (clip_len - XFADE_DURATION)
        out_label = f"v{i}" if i < n - 1 else "vout"

        choices = [t for t in TRANSITIONS if t != last_transition]
        transition = random.choice(choices)
        last_transition = transition

        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition={transition}:"
            f"duration={XFADE_DURATION}:offset={offset:.3f}[{out_label}]"
        )
        prev_label = out_label

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
                "[1:v]scale=110:-1,format=rgba,colorchannelmixer=aa=0.8[logo];"
                "[0:v][logo]overlay=x=30:y=H-h-90:shortest=1[vout]",
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

    scene_files = sorted(glob.glob(os.path.join(args.scenes, "scene_*.png")))
    if not scene_files:
        raise SystemExit("scenes/ klasöründe görsel bulunamadı")

    with open(args.script, "r", encoding="utf-8") as f:
        script_text = f.read()
    scenes_text = split_into_scenes(script_text)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    overlays = extract_overlays(client, scenes_text[:len(scene_files)])

    total_duration = get_audio_duration(args.audio)
    n = len(scene_files)

    clip_len = (total_duration + (n - 1) * XFADE_DURATION) / n
    clip_len = max(clip_len, XFADE_DURATION + 0.5)

    print(f"{n} sahne, her biri ~{clip_len:.1f}sn (geçişlerle toplam ~{total_duration:.1f}sn)")

    clip_paths = []
    for i, scene_file in enumerate(scene_files):
        clip_path = f"clip_{i:03d}.mp4"
        callout = overlays[i]["callout"] if i < len(overlays) else ""
        info_card = overlays[i]["info_card"] if i < len(overlays) else ""
        make_ken_burns_clip(scene_file, clip_len, callout, info_card, clip_path)
        clip_paths.append(clip_path)
        tags = []
        if callout:
            tags.append(f"vurgu:'{callout}'")
        if info_card:
            tags.append(f"bilgi:'{info_card}'")
        tag_str = f" ({', '.join(tags)})" if tags else ""
        print(f"  Klip {i+1}/{n} hazır{tag_str}")

    silent_video = "silent_video.mp4"
    chain_with_xfade(clip_paths, clip_len, silent_video)

    add_logo_and_audio(silent_video, args.audio, args.out)

    print(f"Final video hazır -> {args.out}")


if __name__ == "__main__":
    main()
