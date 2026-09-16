#!/usr/bin/env python3
"""
Visual Style Selector - Her video için rastgele görsel stil seçimi
Bölüm 26: Hızlı & Agresif Başlangıç + Kalite Kalkanı
"""

import json
import random
from pathlib import Path

def select_visual_style(niche_category="general"):
    """
    Niş kategorisine uygun görsel stil seç
    Her videoda farklı stil = YouTube "kitlesel üretim" cezasından korunma
    """
    styles_file = Path(__file__).parent.parent / "assets" / "visual_styles.json"
    
    with open(styles_file, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    styles = config["styles"]
    
    # Kategoriye göre filtrele
    category_styles = [s for s in styles if s["category"] == niche_category or s["category"] == "general"]
    
    if not category_styles:
        category_styles = styles  # Fallback: tüm stiller
    
    # Rastgele seç (son kullanılan hariç)
    last_used = config.get("last_used_index", -1)
    available = [s for i, s in enumerate(category_styles) if i != last_used]
    
    if not available:
        available = category_styles  # Sadece 1 stil varsa
    
    selected = random.choice(available)
    
    # Son kullanılanı güncelle
    selected_index = category_styles.index(selected)
    config["last_used_index"] = selected_index
    
    with open(styles_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    return selected

def main():
    """Test için"""
    import sys
    
    niche = sys.argv[1] if len(sys.argv) > 1 else "general"
    style = select_visual_style(niche)
    
    print(f"Seçilen Stil: {style['name']}")
    print(f"Kategori: {style['category']}")
    print(f"Prompt Eki: {style['prompt_suffix']}")

if __name__ == "__main__":
    main()
