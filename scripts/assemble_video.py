"""
scenes/ klasöründeki görselleri, sesin süresine göre Ken Burns
efektiyle (yavaş yakınlaşma + rastgele yön pan) animasyonlu klipler
haline getirir, aralarına 15 farklı geçiş efektinden (art arda AYNISI
tekrarlanmayacak şekilde) birini koyarak birleştirir. Bazı sahnelere,
script'ten çıkarılan kısa "vurgu" metinlerini (istatistik/çarpıcı
rakam gibi) köşede kısa süreliğine gösteren hafif bir bindirme ekler
(her sahneye değil, bunaltıcı olmasın diye).

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

TRANSITIONS = [
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circleopen", "circleclose", "smoothleft", "smoothright",
    "diagbl", "diagtr",
]


def get_audio_duration(path: str) -> float:
    return MP3(path).info.length


def split_into_scenes(script_text: str):
    return [p.strip() for p in script_text.split("\n\n") if p.strip()]


def extract_callouts(client, scenes):
    """Her sahne için, EGER varsa, ekranda kısaca gösterilecek çarpıcı
    bir sayı/kısa vurgu metni çıkarır (yoksa boş bırakır)."""
    joined = "\n---\n".join(f"[{i}] {s}" for i, s in enumerate(scenes))
    prompt = f"""Aşağıda numaralı script paragrafları var. Her biri için,
EĞER paragraf içinde çarpıcı bir SAYI/istatistik/kısa vurgu ifadesi
varsa, ekranda 2-3 saniyeliğine gösterilecek ÇOK KISA (max 4 kelime,
tercihen bir sayı/yüzde/yıl gibi) bir metin öner. Paragrafta böyle bir
şey yoksa boş string bırak - HER paragrafta olmak zorunda değil, sadece
gerçekten çarpıcı bir rakam/veri varsa doldur.

PARAGRAFLAR:
{joined}

Çıktı SADECE JSON dizi (paragraf sırasıyla, İngilizce metinlerle):
["27%", "", "1986", "", ...]"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        callouts = json.loads(cleaned)
    except json.JSONDecodeError:
        callouts = [""] * len(scenes)
    if len(callouts) < len(scenes):
        callouts += [""] * (len(scenes) - len(callouts))
    return callouts[:len(scenes)]


def make_ken_burns_clip(image_path: str, duration: float, callout_text: str, out_path: str):
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
        f"scale=3000:-1,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={RESOLUTION}:fps={FPS}"
    )

    if callout_text:
        show_dur = min(3.0, duration * 0.6)
        callout_file = out_path + ".callout.txt"
        with open(callout_file, "w", encoding="utf-8") as f:
            f.write(callout_text)
        alpha_expr = (
            f"if(lt(t,0.25),t/0.25,"
            f"if(lt(t,{show_dur}-0.25),1,"
            f"if(lt(t,{show_dur}),({show_dur}-t)/0.25,0)))"
        )
        vf_chain += (
            f",drawtext=fontfile={FONT_PATH}:textfile={callout_file}:"
            f"fontsize=56:fontcolor=white:box=1:boxcolor=black@0.55:"
            f"boxborderw=16:x=w-tw-50:y=50:alpha='{alpha_expr}'"
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
    callouts = extract_callouts(client, scenes_text[:len(scene_files)])

    total_duration = get_audio_duration(args.audio)
    n = len(scene_files)

    clip_len = (total_duration + (n - 1) * XFADE_DURATION) / n
    clip_len = max(clip_len, XFADE_DURATION + 0.5)

    print(f"{n} sahne, her biri ~{clip_len:.1f}sn (geçişlerle toplam ~{total_duration:.1f}sn)")

    clip_paths = []
    for i, scene_file in enumerate(scene_files):
        clip_path = f"clip_{i:03d}.mp4"
        callout = callouts[i] if i < len(callouts) else ""
        make_ken_burns_clip(scene_file, clip_len, callout, clip_path)
        clip_paths.append(clip_path)
        tag = f" (vurgu: '{callout}')" if callout else ""
        print(f"  Klip {i+1}/{n} hazır{tag}")

    silent_video = "silent_video.mp4"
    chain_with_xfade(clip_paths, clip_len, silent_video)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", silent_video,
            "-i", args.audio,
            "-c:v", "libx264", "-c:a", "aac",
            "-shortest",
            args.out,
        ],
        check=True,
    )

    print(f"Final video hazır -> {args.out}")


if __name__ == "__main__":
    main()
