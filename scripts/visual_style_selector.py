#!/usr/bin/env python3
"""
Visual Style Selector - Her video icin gorsel stil secimi
Bolum 26: Hizli & Agresif Baslangic + Kalite Kalkani

Workflow bunu `--niche "<konu>"` ile cagirir. Eskiden main() sys.argv[1]'i
okudugu icin niche olarak literal "--niche" bayragini aliyordu; artik
argparse ile duzgun okunuyor.
"""

import argparse
import json
import random
from pathlib import Path


def select_visual_style(niche_category="general"):
    """Nise uygun gorsel stil sec. Her videoda farkli stil = YouTube
    'kitlesel uretim' cezasindan korunma."""
    styles_file = Path(__file__).parent.parent / "assets" / "visual_styles.json"

    with open(styles_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    styles = config["styles"]

    category_styles = [
        s for s in styles
        if s["category"] == niche_category or s["category"] == "general"
    ]
    if not category_styles:
        category_styles = styles  # Fallback: tum stiller

    last_used = config.get("last_used_index", -1)
    available = [s for i, s in enumerate(category_styles) if i != last_used]
    if not available:
        available = category_styles  # Sadece 1 stil varsa

    selected = random.choice(available)

    config["last_used_index"] = category_styles.index(selected)
    with open(styles_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--niche", default="general",
                        help="Nis/kategori (bulunamazsa 'general' stillere duser)")
    args = parser.parse_args()

    style = select_visual_style(args.niche)
    print(f"Secilen Stil: {style['name']}")
    print(f"Kategori: {style['category']}")
    print(f"Prompt Eki: {style['prompt_suffix']}")


if __name__ == "__main__":
    main()
