"""
scenes/ klasöründeki görselleri, sesin süresine göre Ken Burns
efektiyle (yavaş yakınlaşma + hafif kaydırma) animasyonlu klipler
haline getirir, aralarına yumuşak geçiş (crossfade) koyarak birleştirir
ve üzerine ses/altyazı öncesi ham videoyu üretir.

Statik slayt gösterisinden daha "canlı" bir video hissi vermek için:
- Her sahne: sabit görsel yerine yavaş zoom + hafif pan (Ken Burns)
- Sahneler arası: ani kesme yerine yumuşak crossfade geçişi
- Sahne yönü (sola/sağa pan) çift/tek indekse göre değişir, tekdüze
  olmasın diye

Kullanım:
    python scripts/assemble_video.py --audio audio/final_mix.mp3 \
        --scenes scenes/ --out output/raw.mp4
"""
import argparse
import glob
import os
import subprocess

from mutagen.mp3 import MP3

FPS = 25
RESOLUTION = "1280x720"
XFADE_DURATION = 0.6  # sahneler arası geçiş süresi (saniye)


def get_audio_duration(path: str) -> float:
    return MP3(path).info.length


def make_ken_burns_clip(image_path: str, duration: float, index: int, out_path: str):
    """Tek bir görseli yavaş zoom + hafif pan ile kısa bir video klibine
    çevirir. Çift/tek index'e göre pan yönü değişir (monotonluğu kırar)."""
    frames = int(duration * FPS)
    pan_direction = 1 if index % 2 == 0 else -1
    pan_amount = 40  # piksel cinsinden toplam kayma miktarı

    zoom_expr = "min(zoom+0.0012,1.4)"
    x_expr = f"iw/2-(iw/zoom/2)+({pan_direction}*{pan_amount}*on/{frames})"
    y_expr = "ih/2-(ih/zoom/2)"

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-vf", (
                f"scale=3000:-1,"
                f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
                f"d={frames}:s={RESOLUTION}:fps={FPS}"
            ),
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            out_path,
        ],
        check=True,
        capture_output=True,
    )


def chain_with_xfade(clip_paths, clip_len, out_path):
    """Klipleri crossfade geçişleriyle tek videoda birleştirir."""
    n = len(clip_paths)
    if n == 1:
        subprocess.run(["ffmpeg", "-y", "-i", clip_paths[0], "-c", "copy", out_path], check=True)
        return

    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]

    filter_parts = []
    prev_label = "0:v"
    for i in range(1, n):
        offset = i * (clip_len - XFADE_DURATION)
        out_label = f"v{i}" if i < n - 1 else "vout"
        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition=fade:"
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
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    scene_files = sorted(glob.glob(os.path.join(args.scenes, "scene_*.png")))
    if not scene_files:
        raise SystemExit("scenes/ klasöründe görsel bulunamadı")

    total_duration = get_audio_duration(args.audio)
    n = len(scene_files)

    clip_len = (total_duration + (n - 1) * XFADE_DURATION) / n
    clip_len = max(clip_len, XFADE_DURATION + 0.5)

    print(f"{n} sahne, her biri ~{clip_len:.1f}sn (geçişlerle toplam ~{total_duration:.1f}sn)")

    clip_paths = []
    for i, scene_file in enumerate(scene_files):
        clip_path = f"clip_{i:03d}.mp4"
        make_ken_burns_clip(scene_file, clip_len, i, clip_path)
        clip_paths.append(clip_path)
        print(f"  Klip {i+1}/{n} hazır")

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
