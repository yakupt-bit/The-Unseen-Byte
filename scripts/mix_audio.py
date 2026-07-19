"""
Arka plan müziğini seslendirmenin altına, konuşmayı bastırmayacak
şekilde (düşük ses seviyesi + sidechain ducking) miksler.

Kendi arka plan müziğini assets/music/ klasörüne koyup
BACKGROUND_MUSIC_PATH'i güncelle (telifsiz/lisanslı bir parça kullan —
YouTube Content ID sorununa girmemek için).

Kullanım:
    python scripts/mix_audio.py --voice audio/voiceover.mp3 \
        --music assets/music/background.mp3 --out audio/final_mix.mp3
"""
import argparse
import subprocess


def mix(voice_path, music_path, out_path, music_volume_db=-24):
    # sidechaincompress: müzik, sesin konuştuğu anlarda otomatik kısılır
    # (ducking) — profesyonel podcast/video seslerinde kullanılan teknik
    filter_complex = (
        f"[1:a]volume={music_volume_db}dB[music];"
        f"[music][0:a]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=300[ducked];"
        f"[0:a][ducked]amix=inputs=2:duration=first:weights=1 1[out]"
    )
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", _get_duration(voice_path),
            out_path,
        ],
        check=True,
    )


def _get_duration(path: str) -> str:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", required=True)
    parser.add_argument("--music", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    mix(args.voice, args.music, args.out)
    print(f"Miks tamamlandı -> {args.out}")


if __name__ == "__main__":
    main()
