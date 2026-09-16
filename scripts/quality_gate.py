#!/usr/bin/env python3
"""
Quality Gate - Video üretiminden önce kontrol kapıları
Bölüm 28: Kontroller ve Filtreler
"""

import json
import sys
import os
from pathlib import Path

def load_json(filepath):
    """JSON dosyasını yükle, yoksa boş liste döndür"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_json(filepath, data):
    """JSON dosyasına kaydet"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def check_duplicate(topic, title, used_topics_file, used_titles_file):
    """Tekrar kontrolü - Bölüm 28"""
    used_topics = load_json(used_topics_file)
    used_titles = load_json(used_titles_file)
    
    # Basit benzerlik kontrolü (case-insensitive)
    topic_lower = topic.lower()
    title_lower = title.lower()
    
    for used_topic in used_topics:
        if topic_lower in used_topic.lower() or used_topic.lower() in topic_lower:
            return False, f"KONU TEKRARI: '{topic}' daha önce kullanılmış ({used_topic})"
    
    for used_title in used_titles:
        if title_lower in used_title.lower() or used_title.lower() in title_lower:
            return False, f"BAŞLIK TEKRARI: '{title}' daha önce kullanılmış ({used_title})"
    
    return True, "Tekrar kontrolü geçti"

def check_title_thumbnail_parallel(title, thumbnail_text):
    """Başlık ≠ Kapak kontrolü - Bölüm 7"""
    title_words = set(title.lower().split())
    thumbnail_words = set(thumbnail_text.lower().split())
    
    # Çok fazla örtüşme varsa uyarı
    overlap = title_words.intersection(thumbnail_words)
    if len(overlap) >= 3:
        return False, f"BAŞLİK-KAPAK ÇAKIŞMASI: '{title}' ve '{thumbnail_text}' çok benzer"
    
    return True, "Başlık-kapak paralelliği OK"

def check_script_length(script_text, min_words=1500, max_words=2250):
    """Script uzunluğu kontrolü (10-15 dk hedef)"""
    word_count = len(script_text.split())
    
    if word_count < min_words:
        return False, f"SCRIPT ÇOK KISA: {word_count} kelime (minimum {min_words})"
    elif word_count > max_words:
        return False, f"SCRIPT ÇOK UZUN: {word_count} kelime (maximum {max_words})"
    
    return True, f"Script uzunluğu OK ({word_count} kelime)"

def check_first_30_seconds(script_text):
    """İlk 30 saniye anatomisi kontrolü - Bölüm 4"""
    lines = script_text.strip().split('\n')
    first_500_chars = script_text[:500].lower()
    
    # Yasaklı açılışlar
    forbidden = [
        "imagine if",
        "picture this",
        "have you ever wondered",
        "in the world of",
        "for as long as",
        "for decades",
        "merhaba",
        "bugün",
        "bu videoda"
    ]
    
    for forbidden_phrase in forbidden:
        if forbidden_phrase in first_500_chars:
            return False, f"İLKSANİYE HATASI: '{forbidden_phrase}' gibi zayıf açılış tespit edildi"
    
    # Somut açılış kontrolü (sayı, tarih, isim içermeli)
    has_concrete = any(char.isdigit() for char in first_500_chars[:100])
    if not has_concrete:
        return False, "İLKSANİYE HATASI: İlk cümle somut değil (sayı/tarih/isim içermiyor)"
    
    return True, "İlk 30 saniye anatomisi OK"

def main():
    """Ana kontrol fonksiyonu"""
    if len(sys.argv) < 3:
        print("KULLANIM: python quality_gate.py <topic> <title> [thumbnail_text] [script_file]")
        sys.exit(1)
    
    topic = sys.argv[1]
    title = sys.argv[2]
    thumbnail_text = sys.argv[3] if len(sys.argv) > 3 else ""
    script_file = sys.argv[4] if len(sys.argv) > 4 else None
    
    # Dosya yolları
    repo_root = Path(__file__).parent.parent
    used_topics_file = repo_root / "used_topics.json"
    used_titles_file = repo_root / "used_titles.json"
    
    all_passed = True
    messages = []
    
    # KONTROL 1: Tekrar
    passed, msg = check_duplicate(topic, title, used_topics_file, used_titles_file)
    messages.append(msg)
    if not passed:
        all_passed = False
        print(f"❌ {msg}")
        sys.exit(1)
    else:
        print(f"✅ {msg}")
    
    # KONTROL 2: Başlık-Kapak Paralelliği
    if thumbnail_text:
        passed, msg = check_title_thumbnail_parallel(title, thumbnail_text)
        messages.append(msg)
        if not passed:
            all_passed = False
            print(f"❌ {msg}")
        else:
            print(f"✅ {msg}")
    
    # KONTROL 3: Script uzunluğu ve ilk 30 saniye
    if script_file and os.path.exists(script_file):
        with open(script_file, 'r', encoding='utf-8') as f:
            script_text = f.read()
        
        passed, msg = check_script_length(script_text)
        messages.append(msg)
        print(f"{'✅' if passed else '❌'} {msg}")
        if not passed:
            all_passed = False
        
        passed, msg = check_first_30_seconds(script_text)
        messages.append(msg)
        print(f"{'✅' if passed else '❌'} {msg}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n🎉 TÜM KALİTE KAPILARI GEÇİLDİ - ÜRETİME GEÇ")
        sys.exit(0)
    else:
        print("\n❌ BAZI KONTROLLER BAŞARISIZ - DÜZELTME GEREKLİ")
        sys.exit(1)

if __name__ == "__main__":
    main()
