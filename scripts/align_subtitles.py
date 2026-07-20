"""
Wiro'nun voice-clone modeli zaman damgası döndürmediği için, üretilen
sesi Whisper (yerel, ücretsiz) ile yeniden dinleyip kelime bazlı gerçek
zaman damgalarını çıkarır. Bu yöntem hangi TTS sağlayıcısını
kullanırsan kullan çalışır — sağlayıcıdan bağımsız senkron.

Kullanım:"""
Wiro'nun voice-clone modeli zaman damgası döndürmediği için, üretilen
sesi Whisper (yerel, ücretsiz) ile yeniden dinleyip kelime bazlı gerçek
zaman damgalarını çıkarır. Bu yöntem hangi TTS sağlayıcısını
kullanırsan kullan çalışır — sağlayıcıdan bağımsız senkron.

Kullanım:
    python scripts/align_subtitles.py --audio audio/voiceover.mp3 --out audio/alignment.json
"""
import argparse
import json

from faster_whisper import WhisperModel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-size", default="base",
                         help="tiny/base/small — küçük model GitHub runner'da daha hızlı")
    args = parser.parse_args()

    model = WhisperModel(args.model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(args.audio, word_timestamps=True, language="en")

    words = []
    for segment in segments:
        for word in segment.words:
            words.append({"word": word.word.strip(), "start": word.start})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)

    print(f"Zaman damgası çıkarıldı -> {args.out} ({len(words)} kelime)")


if __name__ == "__main__":
    main()
    python scripts/align_subtitles.py --audio audio/voiceover.mp3 --out audio/alignment.json
"""
import argparse
import json

from faster_whisper import WhisperModel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-size", default="base",
                         help="tiny/base/small — küçük model GitHub runner'da daha hızlı")
    args = parser.parse_args()

    model = WhisperModel(args.model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(args.audio, word_timestamps=True)

    words = []
    for segment in segments:
        for word in segment.words:
            words.append({"word": word.word.strip(), "start": word.start})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)

    print(f"Zaman damgası çıkarıldı -> {args.out} ({len(words)} kelime)")


if __name__ == "__main__":
    main()
