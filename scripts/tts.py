"""
Script metnini klonlanmış sesle seslendirir — Wiro API (wiro/voice-clone,
Coqui tabanlı). Sesini klonlamak için 6 saniyelik bir ses örneği
gerekiyor; bu örnek assets/voice/reference.wav olarak repoda duruyor
ve Wiro'ya PUBLIC RAW GITHUB URL'İ olarak veriliyor (base64 değil).

Gerçek parametre şeması (Wiro JSON Schema'dan doğrulandı):
  - prompt        -> seslendirilecek metin (ZORUNLU)
  - inputAudioUrl -> referans ses dosyasının erişilebilir URL'i
  - language      -> dil kodu ("en", "tr", vb.)

ÖNEMLİ FARK: ElevenLabs'in aksine bu model kelime bazlı zaman damgası
(timestamp) döndürmüyor. Bu yüzden altyazı senkronu için ayrı bir adım
var: align_subtitles.py, üretilen sesi Whisper ile yeniden dinleyip
kelime zamanlarını çıkarıyor.

Kullanım:
    python scripts/tts.py --script script.md --out audio/voiceover.mp3
"""
import argparse
import os
import re

from wiro_client import run_model, download_output

CHUNK_SIZE = 2500


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    with open(args.script, "r", encoding="utf-8") as f:
        text = clean_script(f.read())

    chunks = chunk_text(text, CHUNK_SIZE)
    chunk_paths = []

    for i, chunk in enumerate(chunks):
        chunk_path = f"{args.out}.part{i}.mp3"
        synthesize_chunk(chunk, chunk_path)
        chunk_paths.append(chunk_path)

    import subprocess
    concat_list = "audio_concat_list.txt"
    with open(concat_list, "w") as f:
        for p in chunk_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", args.out],
        check=True,
    )

    print(f"Seslendirme tamamlandı -> {args.out}")
    print("Not: zaman damgası yok, bir sonraki adımda align_subtitles.py çalıştır.")


if __name__ == "__main__":
    main()
