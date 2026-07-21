"""
Script metnini klonlanmış sesle seslendirir - Wiro API (wiro/voice-clone,
Coqui XTTS tabanlı). Sesini klonlamak için 6 saniyelik bir ses örneği
gerekiyor; bu örnek assets/voice/reference.wav olarak repoda duruyor
ve Wiro'ya PUBLIC RAW GITHUB URL'İ olarak veriliyor (base64 değil).

ÖNEMLİ TEKNİK SINIR: Coqui XTTS tek çağrıda en fazla ~400 token
(~250 karakter, ~20-22 saniyelik ses) üretebiliyor. Bu modelin sabit
bir limiti, değiştirilemez. Bu yüzden script küçük parçalara bölünüp
her biri AYRI AYRI seslendirilip sonra BİRLEŞTİRİLİYOR.

Kullanım:
    python scripts/tts.py --script script.md --out audio/voiceover.mp3
"""
import argparse
import os
import re
import subprocess

from wiro_client import run_model, download_output

CHUNK_SIZE = 200


def clean_script(raw: str) -> str:
    text = re.sub(r"```.*?```", "", raw, flags=re.S)
    return text.strip()


def chunk_text(text: str, size: int):
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunk, chunks = "", []
    for sentence in sentences:
        if len(chunk) + len(sentence) > size and chunk:
            chunks.append(chunk.strip())
            chunk = ""
        chunk += sentence + " "
    if chunk.strip():
        chunks.append(chunk.strip())
    return chunks


def synthesize_chunk(text: str, out_path: str):
    voice_reference_url = os.environ["VOICE_REFERENCE_URL"]
    result = run_model("wiro", "voice-clone", {
        "prompt": text,
        "inputAudioUrl": voice_reference_url,
        "language": "en",
    })
    download_output(result, out_path)


def get_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def normalize_audio(in_path: str, out_path: str):
    """Her parçayı AYNI codec/sample-rate/kanal sayısına getirir ve
    başına/sonuna kısa bir fade uygular. Fade olmadan parçalar art
    arda eklenince ani kesme/tık sesi oluşuyor, bu da hem kulağa
    "boğuk/tıkanık" geliyor hem de Whisper'ı yanıltıp saçma altyazı
    (örn. anlamsız sembol/yüzde işaretleri) üretmesine sebep oluyor."""
    raw_duration = get_duration(in_path)
    fade_out_start = max(raw_duration - 0.08, 0)

    subprocess.run(
        ["ffmpeg", "-y", "-i", in_path,
         "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le",
         "-af", f"afade=t=in:st=0:d=0.05,afade=t=out:st={fade_out_start:.3f}:d=0.08",
         out_path],
        check=True,
        capture_output=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    with open(args.script, "r", encoding="utf-8") as f:
        text = clean_script(f.read())

    chunks = chunk_text(text, CHUNK_SIZE)
    print(f"Script {len(chunks)} parçaya bölündü (her biri ~{CHUNK_SIZE} karakter)")

    silence_path = "tiny_silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "0.15", "-c:a", "pcm_s16le", silence_path],
        check=True, capture_output=True,
    )

    normalized_paths = []
    total_expected_duration = 0.0

    for i, chunk in enumerate(chunks):
        raw_path = f"{args.out}.raw{i}.mp3"
        norm_path = f"{args.out}.norm{i}.wav"

        synthesize_chunk(chunk, raw_path)
        normalize_audio(raw_path, norm_path)

        duration = get_duration(norm_path)
        total_expected_duration += duration
        print(f"  Parça {i+1}/{len(chunks)}: {duration:.1f} saniye")

        normalized_paths.append(norm_path)
        if i < len(chunks) - 1:
            normalized_paths.append(silence_path)

    concat_list = "audio_concat_list.txt"
    with open(concat_list, "w") as f:
        for p in normalized_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c:a", "libmp3lame", "-q:a", "2", args.out],
        check=True,
    )

    final_duration = get_duration(args.out)
    print(f"Seslendirme tamamlandı -> {args.out}")
    print(f"Beklenen toplam süre: {total_expected_duration:.1f}s, "
          f"gerçek dosya süresi: {final_duration:.1f}s")
    if abs(final_duration - total_expected_duration) > 2:
        print("UYARI: süre uyuşmuyor, birleştirmede sorun olabilir!")

    print("Not: zaman damgası yok, bir sonraki adımda align_subtitles.py çalıştır.")


if __name__ == "__main__":
    main()
