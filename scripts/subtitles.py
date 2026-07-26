"""
align_subtitles.py'nin ürettiği kelime bazlı gerçek zaman damgalarından
SRT altyazı üretir ve ffmpeg ile videoya gömer.

Kullanım:
    python scripts/subtitles.py --alignment audio/alignment.json \
        --video output/raw.mp4 --out output/final.mp4
"""
import argparse
import json
import re
import subprocess

WORDS_PER_CAPTION = 6
MIN_CONFIDENCE = 0.4


def format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def chars_to_words(alignment):
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


def merge_trailing_punctuation(words):
    merged = []
    for w in words:
        text = w["word"].strip()
        if merged and text and all(not ch.isalnum() for ch in text):
            merged[-1] = dict(merged[-1])
            merged[-1]["word"] = merged[-1]["word"] + text
        else:
            merged.append(w)
    return merged


def is_valid_word(word_entry: dict) -> bool:
    stripped = word_entry["word"].strip()
    if not stripped:
        return False
    if not any(ch.isalnum() for ch in stripped):
        return False
    prob = word_entry.get("prob")
    if prob is not None and prob < MIN_CONFIDENCE:
        return False
    return True


def build_srt(words, out_path):
    words = merge_trailing_punctuation(words)
    words = [w for w in words if is_valid_word(w)]
    sentence_end_re = re.compile(r'[.!?]"?$')

    with open(out_path, "w", encoding="utf-8") as f:
        idx = 1
        i = 0
        n = len(words)
        while i < n:
            group = []
            while len(group) < WORDS_PER_CAPTION and i < n:
                group.append(words[i])
                i += 1
                if sentence_end_re.search(group[-1]["word"]):
                    break

            start = group[0]["start"]
            end = words[i]["start"] if i < n else start + 2.0
            text = " ".join(w["word"] for w in group)
            f.write(f"{idx}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n\n")
            idx += 1


def burn_subtitles(video_path, srt_path, out_path):
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
