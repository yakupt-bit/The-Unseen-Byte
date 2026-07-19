"""
scenes/ klasöründeki görselleri, audio dosyasının süresine göre eşit
aralıklarla sıralayıp ffmpeg ile tek bir video haline getirir.

Basit bir "slideshow + crossfade" mantığı kullanır. Daha gelişmiş
efektler (Ken Burns zoom, geçiş animasyonları) için ffmpeg filtre
zincirini genişletebilirsin.

Kullanım:
    python scripts/assemble_video.py --audio audio/voiceover.mp3 \
        --scenes scenes/ --out output/final.mp4
"""
import argparse
import glob
import os
import subprocess

from mutagen.mp3 import MP3


def get_audio_duration(path: str) -> float:
    return MP3(path).info.length


def build_concat_file(scene_files, seconds_per_scene, concat_path):
    with open(concat_path, "w") as f:
        for scene in scene_files:
            f.write(f"file '{os.path.abspath(scene)}'\n")
            f.write(f"duration {seconds_per_scene:.2f}\n")
        # ffmpeg concat demuxer son dosyayı bir kez daha ister
        f.write(f"file '{os.path.abspath(scene_files[-1])}'\n")


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

    duration = get_audio_duration(args.audio)
    seconds_per_scene = duration / len(scene_files)

    concat_path = "concat_list.txt"
    build_concat_file(scene_files, seconds_per_scene, concat_path)

    silent_video = "silent_slideshow.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_path,
            "-vsync", "vfr", "-pix_fmt", "yuv420p",
            silent_video,
        ],
        check=True,
    )

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", silent_video,
            "-i", args.audio,
            "-c:v", "copy", "-c:a", "aac",
            "-shortest",
            args.out,
        ],
        check=True,
    )

    print(f"Final video hazır -> {args.out}")


if __name__ == "__main__":
    main()
