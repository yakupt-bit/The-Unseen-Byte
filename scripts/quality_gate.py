#!/usr/bin/env python3
"""
Quality Gate - Video uretiminden once kontrol kapilari (Bolum 28).

Workflow bunu soyle cagirir:
    python scripts/quality_gate.py --script script.md --titles titles.json [--test]

Burada SADECE script uzunlugu + ilk 30 saniye acilis kontrolu yapilir.
Konu/baslik TEKRARI ve NIS uyumu ayri olarak niche_check.py'de yapilir
(used_titles.json bu asamada generate_titles tarafindan zaten guncellendigi
icin, basligi burada used_titles ile karsilastirmak her zaman yanlis pozitif
uretirdi - o yuzden tekrar kontrolu bilerek buradan cikarildi).

NOT: script_prompt.md sayilarin YAZIYLA yazilmasini istiyor ("ninety",
"nineteen ninety-four"), bu yuzden eski surumdeki "ilk cumlede rakam olmali"
kontrolu her uyumlu scripti hatali sekilde reddediyordu - o kontrol kaldirildi.

Cikis kodu: 0 = gecti, 1 = kaldi (workflow yeniden dener).
"""
import argparse
import json
import os
import sys


def load_title(titles_path):
    try:
        with open(titles_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            sel = data.get("selected")
            if isinstance(sel, list) and sel:
                return str(sel[0])
            for key in ("selected_title", "title"):
                if data.get(key):
                    return str(data[key])
        return str(data)
    except (json.JSONDecodeError, OSError):
        return ""


def check_script_length(script_text, min_words=1500, max_words=2250):
    wc = len(script_text.split())
    if wc < min_words:
        return False, f"SCRIPT COK KISA: {wc} kelime (min {min_words})"
    if wc > max_words:
        return False, f"SCRIPT COK UZUN: {wc} kelime (max {max_words})"
    return True, f"Script uzunlugu OK ({wc} kelime)"


def check_first_30_seconds(script_text):
    """Yasakli/klise acilislari tespit eder (Bolum 4). Sayi-somutlugu
    kontrolu YOK - sayilar yaziyla yazildigi icin rakam aramak yanlisti."""
    first = script_text[:500].lower()
    forbidden = [
        "imagine if", "picture this", "have you ever wondered",
        "in the world of", "for as long as", "for decades",
        "hello, today", "in this video", "welcome back",
        "merhaba", "bugun", "bu videoda",
    ]
    for phrase in forbidden:
        if phrase in first:
            return False, f"ZAYIF/KLISE ACILIS: '{phrase}' tespit edildi"
    return True, "Ilk 30 saniye acilis kontrolu OK"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--titles", required=True)
    parser.add_argument("--test", action="store_true",
                        help="Test modu: sadece scriptin uretilip uretilmedigine bakar")
    args = parser.parse_args()

    if not os.path.exists(args.script):
        print(f"\u274c script bulunamadi: {args.script}")
        sys.exit(1)
    with open(args.script, "r", encoding="utf-8") as f:
        script_text = f.read()

    title = load_title(args.titles)
    print(f"Kontrol edilen baslik: {title}")

    if args.test:
        if script_text.strip():
            print("\u2705 Test modu: script uretildi, kalite kapisi atlaniyor.")
            sys.exit(0)
        print("\u274c Test modu: script bos.")
        sys.exit(1)

    all_ok = True
    for ok, msg in (check_script_length(script_text),
                    check_first_30_seconds(script_text)):
        print(f"{'\u2705' if ok else '\u274c'} {msg}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\U0001f389 KALITE KAPISI GECILDI")
        sys.exit(0)
    print("\u274c KALITE KAPISI BASARISIZ")
    sys.exit(1)


if __name__ == "__main__":
    main()
