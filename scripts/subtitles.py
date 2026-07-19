"""
tts.py'nin ürettiği karakter-bazlı zaman damgalarından (gerçek, tahmini
DEĞİL) kelime gruplarına göre SRT altyazı üretir ve ffmpeg ile videoya
gömer. Karakterlerin gerçek başlama zamanını kullandığımız için altyazı
sesle birebir örtüşür, kayma olmaz.

Kullanım:
    python scripts/subtitles.py --alignment audio/voiceover_alignment.json \
        --video output/silent_with_audio.mp4 --out output/final_subtitled.mp4
"""
import argparse
import json
import subprocess

WORDS_PER_CAPTION = 6  # ekranda aynı anda gösterilecek kelime sayısı


def format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def chars_to_words(alignment):
    # Zaten kelime bazlıysa (align_subtitles.py / Whisper çıktısı)
    # direkt kullan, dönüştürme yapma.
    if alignment and "word" in alignment[0]:
        return alignment

    words, current_word, current_start = [], "", None
    for entry in alignment:
        char = entry["char"]
        if char.strip() == "":
            if current_word:
                words.append({"word": current_word, "start": current_start})
                current_word = ""
        else:
            if current_word == "":
                current_start = entry["start"]
            current_word += char
    if current_word:
        words.append({"word": current_word, "start": current_start})
    return words


def build_srt(words, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        idx = 1
        for i in range(0, len(words), WORDS_PER_CAPTION):
            group = words[i:i + WORDS_PER_CAPTION]
            start = group[0]["start"]
            # bitiş zamanı: bir sonraki grubun başlangıcı, yoksa +2sn
            if i + WORDS_PER_CAPTION < len(words):
                end = words[i + WORDS_PER_CAPTION]["start"]
            else:
                end = start + 2.0
            text = " ".join(w["word"] for w in group)
            f.write(f"{idx}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n\n")
            idx += 1


def burn_subtitles(video_path, srt_path, out_path):
    # force_style: okunabilir, kalın, gölgeli altyazı stili
    style = "FontName=Arial,FontSize=22,Bold=1,OutlineColour=&H80000000,BorderStyle=3"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"subtitles={srt_path}:force_style='{style}'",
            "-c:a", "copy",
            out_path,
        ],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alignment", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.alignment, "r", encoding="utf-8") as f:
        alignment = json.load(f)

    words = chars_to_words(alignment)
    srt_path = "captions.srt"
    build_srt(words, srt_path)
    burn_subtitles(args.video, srt_path, args.out)

    print(f"Altyazılı video hazır -> {args.out}")


if __name__ == "__main__":
    main()
