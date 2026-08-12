"""
Script metnini klonlanmış sesle seslendirir - Wiro API (wiro/voice-clone,
Coqui XTTS tabanlı). Sesini klonlamak için 6 saniyelik bir ses örneği
gerekiyor; bu örnek assets/voice/reference.wav olarak repoda duruyor
ve Wiro'ya PUBLIC RAW GITHUB URL'İ olarak veriliyor (base64 değil).

ÖNEMLİ TEKNİK SINIR: Coqui XTTS tek çağrıda en fazla ~400 token
(~250 karakter, ~20-22 saniyelik ses) üretebiliyor. Bu modelin sabit
bir limiti, değiştirilemez. Bu yüzden script küçük parçalara bölünüp
her biri AYRI AYRI seslendirilip sonra BİRLEŞTİRİLİYOR.

GÜVENİLİRLİK NOTU (parça bazlı yeniden deneme + checkpoint):
Script'ler 80-150+ parçaya bölünebiliyor, ve Wiro API'si ARADA SIRADA
tek bir parçada yavaş kalabiliyor (poll_task zaman aşımı). Öncesinde
TEK bir parçanın başarısız olması, o ana kadar üretilmiş TÜM parçaları
(ör. 88/113) çöpe atıyordu çünkü script tamamen çöküyordu. Artık:
  1. Her parça kendi başına en fazla RETRY_PER_CHUNK kez denenir
     (üstel bekleme ile) - geçici bir Wiro yavaşlığı artık tüm işi
     götürmüyor.
  2. Her parçanın ham çıktısı DİSKE yazıldıktan hemen sonra kontrol
     edilir - eğer bu script AYNI koşu içinde (ör. GitHub Actions
     "Re-run failed jobs" ile) yeniden başlatılırsa ve önceki
     denemeden kalma ham dosyalar hâlâ diskte duruyorsa, o parçalar
     ATLANIR, Wiro'ya tekrar ücret ödenmez. (Not: GitHub Actions
     job'ları arasında dosya sistemi kalıcı DEĞİLDİR, bu yalnızca
     aynı iş/container ömrü içindeki tekrar denemelerde işe yarar.)

SES AYARLARI (bugün güncellendi):
  - Hız: 1.10x -> 1.20x (SPEED_FACTOR)
  - Ses seviyesi: %15 artırıldı (VOLUME_FACTOR) - aşırıya kaçmadan
  - Stüdyo yankısı: hafif bir "oda tonu" eklendi (ECHO_FILTER) - kısa
    gecikme (35ms) ve düşük decay (0.25) ile abartılı kanyon-yankısı
    değil, ince bir stüdyo/oda hissi hedeflendi. Değerleri beğenmezsen
    ECHO_FILTER sabitini ayarlayıp deneyebilirsin (format:
    aecho=in_gain:out_gain:delay_ms:decay).

Kullanım:
    python scripts/tts.py --script script.md --out audio/voiceover.mp3
"""
import argparse
import os
import re
import subprocess
import time

from wiro_client import run_model, download_output

CHUNK_SIZE = 200
RETRY_PER_CHUNK = 3
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20

# --- Ses ayarları ---
SPEED_FACTOR = 1.20
VOLUME_FACTOR = 1.15
# Hafif stüdyo/oda yankısı: kısa gecikme + düşük decay = abartısız.
# aecho=in_gain:out_gain:delay_ms:decay
ECHO_FILTER = "aecho=0.6:0.5:35:0.25"


def clean_script(raw: str) -> str:
    text = re.sub(r"```.*?```", "", raw, flags=re.S)

    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue
        if stripped.startswith("#"):
            continue
        if re.match(r"^(Section|Chapter|Part)\s*\d*\s*:?\s*$", stripped, re.I):
            continue
        if re.match(r"^\[?\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\]?$", stripped):
            continue
        stripped = re.sub(r"\[?\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\]?", "", stripped)
        stripped = re.sub(r"^#+\s*", "", stripped)
        cleaned_lines.append(stripped)
    text = "\n".join(cleaned_lines)

    text = text.replace("&", " and ")
    text = text.replace("%", " percent")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
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


def synthesize_chunk_with_retry(text: str, out_path: str, chunk_num: int, total_chunks: int):
    """Bir parçayı en fazla RETRY_PER_CHUNK kez dener. Diskte zaten
    geçerli bir ham dosya varsa (aynı koşuda önceki denemeden kalma),
    tekrar üretmeden atlar - Wiro'ya gereksiz ücret ödenmez."""
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        print(f"  Parça {chunk_num}/{total_chunks}: zaten üretilmiş, atlanıyor (checkpoint)")
        return

    last_error = None
    for attempt in range(RETRY_PER_CHUNK):
        try:
            synthesize_chunk(text, out_path)
            return
        except Exception as e:
            last_error = e
            if attempt < RETRY_PER_CHUNK - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  UYARI: parça {chunk_num}/{total_chunks} başarısız "
                      f"({type(e).__name__}), {delay}sn sonra tekrar deneniyor "
                      f"(deneme {attempt + 2}/{RETRY_PER_CHUNK})...")
                time.sleep(delay)
    raise RuntimeError(
        f"Parça {chunk_num}/{total_chunks}, {RETRY_PER_CHUNK} denemenin "
        f"hepsinde başarısız oldu. Son hata: {last_error}"
    )


def normalize_audio(in_path: str, out_path: str):
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


def get_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


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

        synthesize_chunk_with_retry(chunk, raw_path, i + 1, len(chunks))
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

    # Hız + ses seviyesi + hafif stüdyo yankısı tek zincirde uygulanıyor.
    audio_filter = f"atempo={SPEED_FACTOR},volume={VOLUME_FACTOR},{ECHO_FILTER}"

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-af", audio_filter,
         "-c:a", "libmp3lame", "-q:a", "2", args.out],
        check=True,
    )

    final_duration = get_duration(args.out)
    expected_after_speedup = total_expected_duration / SPEED_FACTOR
    print(f"Seslendirme tamamlandı -> {args.out}")
    print(f"Ses ayarları: hız={SPEED_FACTOR}x, ses seviyesi=x{VOLUME_FACTOR}, "
          f"yankı=({ECHO_FILTER})")
    print(f"Beklenen toplam süre ({SPEED_FACTOR}x hız sonrası): {expected_after_speedup:.1f}s, "
          f"gerçek dosya süresi: {final_duration:.1f}s")
    if abs(final_duration - expected_after_speedup) > 3:
        print("UYARI: süre uyuşmuyor, birleştirmede sorun olabilir!")

    print("Not: zaman damgası yok, bir sonraki adımda align_subtitles.py çalıştır.")


if __name__ == "__main__":
    main()
