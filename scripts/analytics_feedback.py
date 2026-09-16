#!/usr/bin/env python3
"""
Analytics Feedback - YouTube Analytics'den veri çekip sonraki videoyu optimize et
Bölüm 29: Otomasyon ve Geri Besleme
"""

import os
import json
from pathlib import Path
from datetime import datetime, timedelta

# YouTube Data API v3 kullanımı için
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

def get_youtube_analytics(video_id, days=7):
    """
    YouTube Analytics API'den video performansını çek
    Gerekli: YOUTUBE_ANALYTICS_API_KEY ve OAuth
    """
    try:
        # YouTube Analytics API
        youtube = build('youtubeAnalytics', 'v2', developerKey=os.getenv('YOUTUBE_ANALYTICS_API_KEY'))
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        response = youtube.reports().query(
            ids='channel==MINE',
            startDate=start_date.strftime('%Y-%m-%d'),
            endDate=end_date.strftime('%Y-%m-%d'),
            metrics='views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained',
            dimensions='day',
            filters=f'video=={video_id}'
        ).execute()
        
        return response
    
    except HttpError as e:
        print(f"YouTube Analytics API hatası: {e}")
        return None

def calculate_performance_score(ctr, avg_view_duration_percentage, has_viral=False):
    """
    Video performans skoru hesapla
    Bölüm 18: Studio ve Algoritma
    """
    score = 0
    
    # CTR skoru (max 40 puan)
    if ctr >= 13:
        score += 40  # Mükemmel
    elif ctr >= 10:
        score += 30  # İyi
    elif ctr >= 8:
        score += 20  # Optimal
    elif ctr >= 5:
        score += 10  # Kötü ama kurtarılabilir
    else:
        score += 0   # Çok kötü
    
    # AVD skoru (max 40 puan)
    if avg_view_duration_percentage >= 40:
        score += 40  # Mükemmel
    elif avg_view_duration_percentage >= 30:
        score += 30  # Optimal
    elif avg_view_duration_percentage >= 20:
        score += 20  # Kötü
    else:
        score += 0   # Çok kötü
    
    # Viral bonus (max 20 puan)
    if has_viral:
        score += 20
    
    return score

def generate_recommendations(ctr, avd, script_file=None):
    """
    Performans verilerine göre öneri üret
    """
    recommendations = []
    
    # CTR önerileri
    if ctr < 8:
        recommendations.append({
            "priority": "HIGH",
            "area": "CAPA_BASLIK",
            "action": "Kapak ve başlık kombinasyonunu değiştir",
            "detail": f"CTR %{ctr} çok düşük (hedef: %{8}+). Yeni kapak formülü dene (Bölüm 7)."
        })
    elif ctr < 10:
        recommendations.append({
            "priority": "MEDIUM",
            "area": "CAPA_BASLIK",
            "action": "Kapak varyasyonu test et",
            "detail": f"CTR %{ctr} optimal sınırda. A/B test ile iyileştir."
        })
    
    # AVD önerileri
    if avd < 30:
        recommendations.append({
            "priority": "HIGH",
            "area": "SCRIPT_HOOK",
            "action": "İlk 30 saniyeyi yeniden yaz",
            "detail": f"AVD %{avd} çok düşük. İlk 30 saniye anatomisini (Bölüm 4) sert şekilde uygula."
        })
        
        if script_file:
            recommendations.append({
                "priority": "ACTION",
                "area": "SCRIPT_FILE",
                "action": f"{script_file} dosyasını düzenle",
                "detail": "Cold-open somut mu? İlk cümle sayı/tarih içeriyor mu? Kontrol et."
            })
    
    elif avd < 40:
        recommendations.append({
            "priority": "MEDIUM",
            "area": "RETENTION",
            "action": "Mini-reveal ritmini sıklaştır",
            "detail": f"AVD %{avd} iyi ama her 2-2.5 dakikada mini-ödül ekle."
        })
    
    # Ölçekleme önerisi
    if ctr >= 10 and avd >= 40:
        recommendations.append({
            "priority": "SUCCESS",
            "area": "SCALE",
            "action": "Bu formatı ölçekle",
            "detail": "Mükemmel performans! Aynı niş/başlık formülüyle 3-5 video daha üret."
        })
    
    return recommendations

def update_prompt_based_on_analytics(recommendations, prompt_file=None):
    """
    Analytics verilerine göre script promptunu güncelle
    """
    if not recommendations:
        return
    
    # Prompt güncelleme mantığı
    updates = []
    
    for rec in recommendations:
        if rec["area"] == "SCRIPT_HOOK" and rec["priority"] == "HIGH":
            updates.append("İLK 30 SANİYE SIKILAŞTIRILDI: İlk cümle daha somut, daha spesifik")
        elif rec["area"] == "RETENTION":
            updates.append("RETENTION RİTMİ EKLENDİ: Her 2 dakikada mini-reveal")
    
    if updates:
        print("\n PROMPT GÜNCELLEMELERİ:")
        for update in updates:
            print(f"  • {update}")

def main():
    """Ana fonksiyon - GitHub Actions'dan çağrılır"""
    video_id = os.getenv('VIDEO_ID')
    script_file = os.getenv('SCRIPT_FILE')
    
    if not video_id:
        print("HATA: VIDEO_ID ortam değişkeni gerekli")
        sys.exit(1)
    
    print(f"📊 Video {video_id} analizi başlatılıyor...")
    
    # Analytics çek
    analytics = get_youtube_analytics(video_id, days=7)
    
    if not analytics:
        print("Analytics verisi alınamadı")
        sys.exit(1)
    
    # Metrikleri parse et (basitleştirilmiş)
    # Gerçek implementasyonda rows'danCTR ve AVD hesapla
    ctr = float(os.getenv('CTR', 5.0))  # Placeholder
    avd = float(os.getenv('AVD', 25.0))  # Placeholder
    
    print(f"\n📈 Performans Metrikleri:")
    print(f"  CTR: %{ctr}")
    print(f"  AVD: %{avd}")
    
    # Skor hesapla
    score = calculate_performance_score(ctr, avd)
    print(f"\n🎯 Performans Skoru: {score}/100")
    
    # Öneri üret
    recommendations = generate_recommendations(ctr, avd, script_file)
    
    print(f"\n💡 Öneriler ({len(recommendations)}):")
    for rec in recommendations:
        emoji = "🔴" if rec["priority"] == "HIGH" else "🟡" if rec["priority"] == "MEDIUM" else ""
        print(f"  {emoji} [{rec['priority']}] {rec['action']}")
        print(f"      {rec['detail']}")
    
    # Prompt güncelle
    update_prompt_based_on_analytics(recommendations, script_file)
    
    # Sonucu JSON olarak kaydet (sonraki video için)
    output_file = Path(__file__).parent.parent / "analytics_feedback.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "video_id": video_id,
            "ctr": ctr,
            "avd": avd,
            "score": score,
            "recommendations": recommendations,
            "timestamp": datetime.now().isoformat()
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Sonuçlar analytics_feedback.json dosyasına kaydedildi")

if __name__ == "__main__":
    import sys
    main()
