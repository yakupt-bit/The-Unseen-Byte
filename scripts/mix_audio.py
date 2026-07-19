"""
Arka plan müziğini seslendirmenin altına, konuşmayı bastırmayacak
şekilde (düşük ses seviyesi + sidechain ducking) miksler.

assets/music/ klasörüne BİRDEN FAZLA telifsiz/lisanslı parça koy
(örn. background_1.mp3, background_2.mp3, ...) — her video üretiminde
bu havuzdan rastgele biri seçilir. Tek bir parça kullanmak, düzenli
izleyicilerin aynı müziği fark edip sıkılmasına yol açabilir.

Kullanım:
    python scripts/mix_audio.py --voice audio/voiceover.mp3 \
        --music-dir assets/music/ --out audio/final_mix.mp3
"""
import argparse
import glob
import random
import subprocess


def pick_random_track(music_dir: str) -> str:
    tracks = glob.glob(f"{music_dir.rstrip('/')}/*.mp3")
    if not tracks:
        raise FileNotFoundError(
            f"{music_dir} içinde .mp3 dosyası bulunamadı — en az bir "
            "arka plan müziği ekle (örn. assets/music/background_1.mp3)"
        )
    chosen = random.choice(tracks)
    print(f"Seçilen arka plan müziği: {chosen}")
    return chosen


def mix(voice_path, music_path, out_path, music_volume_db=-24):
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
    parser.add_argument("--music-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    music_path = pick_random_track(args.music_dir)
    mix(args.voice, music_path, args.out)
    print(f"Miks tamamlandı -> {args.out}")


if __name__ == "__main__":
    main()
