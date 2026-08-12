"""
YouTube Data API ile, o koşunun konusuna göre Claude'un ürettiği
arama terimleriyle, KONUYLA ALAKALI ve yüksek izlenmeli videoları
çeker; başlık kalıplarını ve performans verisini trend.json'a
kaydeder. Bu dosya sonra generate_script.py ve generate_thumbnail.py
tarafından "şu an gerçekten patlayan formatlar" referansı olarak
kullanılır.

KAPAK GÖRSELİ REFERANSI: Her kaydedilen videonun GERÇEK kapak görseli
URL'i de (thumbnail_url) tutuluyor - generate_thumbnail.py bunları
Claude'a (vision) gösterip SADECE stil/kompozisyon paternini
(kontrast, renk, konu yerleşimi) çıkarıyor, hiçbir görseli birebir
kopyalamıyor. Detaylar için generate_thumbnail.py'deki
analyze_thumbnail_patterns fonksiyonuna bakın.

NEDEN SABİT KANAL LİSTESİ DEĞİL: Sabit bir rakip kanal listesi zamanla
hep aynı sonuçları getirir (tekrar riski). Bunun yerine, HER KOŞUDA
Claude o videonun konusuna özel 1-3 arama terimi üretir, bu terimlerle
YouTube'da GERÇEKTEN alakalı ve GERÇEKTEN BÜYÜK videolar aranır - hem
tekrar riski yok hem de her zaman güncel.

KADEMELİ ARAMA (bugün eklendi): Tek bir zaman/eşik kombinasyonu yerine,
üç seviyeli bir arama yapılıyor - her seviye AYNI konuya özel arama
terimlerini kullanır, sadece zaman penceresi ve izlenme eşiği gevşer:
  1. Son 90 gün, 250.000+ izlenme (TIER1_DAYS / TIER1_MIN_VIEWS)
  2. Boşsa: son 365 gün, 2.000.000+ izlenme (TIER2_DAYS / TIER2_MIN_VIEWS)
  3. O da boşsa: son 365 gün, eşiksiz (son çare - ama YİNE DE konuya
     özel arama terimleriyle, ASLA alakasız/genel bir aramaya düşülmez)

ESKİ DAVRANIŞ NEDEN DEĞİŞTİ: Önceki sürümde, yüksek-performans araması
boş dönerse "genel niş aramasına" düşülüyordu - bu genel arama, konu
başlığının bir kısmını (topic_hint.split(" - ")[0]) DOĞRUDAN YouTube'a
sorgu olarak gönderiyordu, izlenme eşiği YOKTU, ve alakasızlık riski
kontrol edilmiyordu. Pratikte bu durum, "1967 Six-Day War" veya
"3 August Army Meeting" gibi gaming ile HİÇ ilgisi olmayan videoların
trend referansı olarak kullanılmasına yol açtı. Yeni mantıkta, HER
seviye aynı (Claude'un konuya özel ürettiği) arama terimlerini
kullanıyor - sadece zaman/eşik gevşiyor, konu odağı hiç kaybolmuyor.

Not: YouTube Data API günlük kota sınırlıdır (varsayılan 10.000 birim/gün,
search.list çağrısı 100 birim tutar). Bu script çalışma başına en fazla
~9 search.list çağrısı yapar (3 seviye x en fazla 3 anahtar kelime),
güvenli aralıkta kalır.

Kullanım:
    python scripts/trend_analysis.py --out trend.json
    python scripts/trend_analysis.py --query "gaming psychology facts" --out trend.json
"""
import argparse
import datetime
import json
import os
import random

import anthropic
import requests

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# Kademeli arama seviyeleri: (gün, minimum izlenme, kaynak etiketi)
TIER1_DAYS, TIER1_MIN_VIEWS = 90, 250_000
TIER2_DAYS, TIER2_MIN_VIEWS = 365, 2_000_000
TIER3_DAYS, TIER3_MIN_VIEWS = 365, 0  # son çare - eşiksiz ama YİNE konuya özel

MAX_KEYWORDS = 3
RESULTS_PER_KEYWORD = 15

# Sadece Claude anahtar kelime üretemezse (TOPIC_HINT hiç yoksa,
# elle/manuel çalıştırma) devreye giren yedek havuz - bu durumda bile
# arama terimleri niş ile ilgili kalır, tamamen rastgele değildir.
QUERY_POOL = [
    "gaming psychology facts documentary",
    "video game history mystery",
    "tech facts you didn't know",
    "hidden history technology",
    "gaming industry secrets explained",
    "science of video games",
    "retro gaming untold story",
    "why games are addictive science",
    "esports documentary secrets",
    "lost cancelled video games",
    "video game preservation history",
    "corporate gaming industry decisions",
]


def generate_search_keywords(topic_hint: str, client) -> list:
    """Konuya özel, YouTube'da arama yapmaya uygun 1-3 kısa terim
    üretir (Claude Haiku ile, ucuz). Başarısız olursa boş liste döner
    - çağıran taraf QUERY_POOL'a düşer, pipeline kırılmaz."""
    if not topic_hint:
        return []
    try:
        prompt = f"""Bu YouTube video konusuna göre, YouTube arama
kutusunda kullanılacak 1 ila 3 arasında KISA (2-4 kelime), spesifik
arama terimi üret. Amaç: bu konuyla GERÇEKTEN alakalı, popüler
videoları bulmak.

KONU: {topic_hint}

Çıktı SADECE JSON dizi (İngilizce terimler): ["terim 1", "terim 2"]"""
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        keywords = json.loads(cleaned)
        if isinstance(keywords, list) and keywords:
            return [str(k).strip() for k in keywords if str(k).strip()][:MAX_KEYWORDS]
    except Exception as e:
        print(f"  UYARI: arama terimi üretimi başarısız ({type(e).__name__})")
    return []


def days_ago_iso(days: int) -> str:
    """Bugünden geriye doğru N günlük hareketli bir pencere başlangıcı
    döndürür - ayın/yılın hangi gününde olursak olalım tutarlı sonuç
    verir."""
    start = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    return start.isoformat("T") + "Z"


def search_video_ids(query: str, api_key: str, published_after: str = None,
                      max_results: int = 15) -> list:
    """Bir sorgu için video ID listesi döner (istatistik içermez).
    Hata durumunda boş liste döner."""
    params = {
        "key": api_key,
        "part": "snippet",
        "type": "video",
        "q": query,
        "order": "viewCount",
        "maxResults": max_results,
        "relevanceLanguage": "en",
    }
    if published_after:
        params["publishedAfter"] = published_after
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
        return [item["id"]["videoId"] for item in resp.json().get("items", [])]
    except requests.RequestException as e:
        print(f"  UYARI: arama başarısız ({type(e).__name__}) - sorgu: \"{query}\"")
        return []


def extract_thumbnail_url(snippet: dict) -> str:
    """Snippet içindeki thumbnails objesinden en yüksek çözünürlüklü
    URL'i çıkarır (maxres > high > medium > default). Hiçbiri yoksa
    boş string döner - pipeline kırılmaz."""
    thumbs = snippet.get("thumbnails", {})
    for quality in ("maxres", "high", "medium", "default"):
        url = thumbs.get(quality, {}).get("url", "")
        if url:
            return url
    return ""


def fetch_stats(video_ids: list, api_key: str, source_label: str) -> list:
    """Video ID listesinden gerçek istatistikleri çeker, her kayda
    'source' etiketi ekler."""
    if not video_ids:
        return []
    results = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            resp = requests.get(VIDEOS_URL, params={
                "key": api_key,
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(batch),
            }, timeout=30)
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                results.append({
                    "title": item["snippet"]["title"],
                    "channel": item["snippet"]["channelTitle"],
                    "published_at": item["snippet"]["publishedAt"],
                    "views": int(item["statistics"].get("viewCount", 0)),
                    "likes": int(item["statistics"].get("likeCount", 0)),
                    "duration": item["contentDetails"]["duration"],
                    "thumbnail_url": extract_thumbnail_url(item["snippet"]),
                    "source": source_label,
                })
        except requests.RequestException as e:
            print(f"  UYARI: istatistik çekme başarısız ({type(e).__name__}), bu grup atlanıyor")
    return results


def fetch_tier(keywords: list, api_key: str, days: int, min_views: int,
               source_label: str) -> list:
    """Verilen arama terimleriyle, belirtilen gün penceresi içinde
    yayınlanmış ve min_views üzerinde izlenmiş videoları bulur.
    min_views=0 ise eşik uygulanmaz (son çare seviyesi için)."""
    published_after = days_ago_iso(days)

    all_ids = set()
    for kw in keywords:
        ids = search_video_ids(kw, api_key, published_after, RESULTS_PER_KEYWORD)
        all_ids.update(ids)

    if not all_ids:
        return []

    all_stats = fetch_stats(list(all_ids), api_key, source_label=source_label)
    filtered = [v for v in all_stats if v["views"] >= min_views]
    return sorted(filtered, key=lambda x: x["views"], reverse=True)


def fetch_trending_for_topic(topic_hint: str, api_key: str, client) -> list:
    """Kademeli arama: önce son 90 gün + 250k, boşsa son 365 gün + 2M,
    o da boşsa son 365 gün eşiksiz (ama HER ZAMAN aynı konuya özel
    arama terimleriyle - alakasız bir genel aramaya asla düşülmez).
    Anahtar kelime üretilemezse QUERY_POOL'dan rastgele niş-ilişkili
    bir terim kullanılır (tamamen alakasız değildir)."""
    keywords = generate_search_keywords(topic_hint, client)
    if not keywords:
        keywords = [random.choice(QUERY_POOL)]
        print(f"  Konuya özel terim üretilemedi, niş havuzundan kullanılıyor: {keywords}")
    else:
        print(f"  Üretilen arama terimleri: {keywords}")

    print(f"  [Seviye 1] Son {TIER1_DAYS} gün, {TIER1_MIN_VIEWS:,}+ izlenme aranıyor...")
    result = fetch_tier(keywords, api_key, TIER1_DAYS, TIER1_MIN_VIEWS, "tier1_quarter_high_performer")
    if result:
        return result

    print(f"  Seviye 1 sonuçsuz. [Seviye 2] Son {TIER2_DAYS} gün, {TIER2_MIN_VIEWS:,}+ izlenme aranıyor...")
    result = fetch_tier(keywords, api_key, TIER2_DAYS, TIER2_MIN_VIEWS, "tier2_yearly_high_performer")
    if result:
        return result

    print(f"  Seviye 2 sonuçsuz. [Seviye 3 - son çare] Son {TIER3_DAYS} gün, eşiksiz aranıyor...")
    result = fetch_tier(keywords, api_key, TIER3_DAYS, TIER3_MIN_VIEWS, "tier3_relaxed")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=False, default="",
                         help="Elle arama terimi verilirse, o terimle doğrudan kademeli arama yapılır")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    api_key = os.environ["YOUTUBE_API_KEY"]
    topic_hint = os.environ.get("TOPIC_HINT", "").strip()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    if args.query.strip():
        print(f"Elle sorgu verildi: \"{args.query.strip()}\" - kademeli arama yapılıyor")
        keywords = [args.query.strip()]
        print(f"  [Seviye 1] Son {TIER1_DAYS} gün, {TIER1_MIN_VIEWS:,}+ izlenme aranıyor...")
        trend_data = fetch_tier(keywords, api_key, TIER1_DAYS, TIER1_MIN_VIEWS, "tier1_quarter_high_performer")
        if not trend_data:
            print(f"  Seviye 1 sonuçsuz. [Seviye 2] Son {TIER2_DAYS} gün, {TIER2_MIN_VIEWS:,}+ izlenme aranıyor...")
            trend_data = fetch_tier(keywords, api_key, TIER2_DAYS, TIER2_MIN_VIEWS, "tier2_yearly_high_performer")
        if not trend_data:
            print(f"  Seviye 2 sonuçsuz. [Seviye 3 - son çare] Son {TIER3_DAYS} gün, eşiksiz aranıyor...")
            trend_data = fetch_tier(keywords, api_key, TIER3_DAYS, TIER3_MIN_VIEWS, "tier3_relaxed")
    else:
        print(f"Konu: \"{topic_hint or '(belirtilmemiş)'}\" - kademeli trend araması başlıyor...")
        trend_data = fetch_trending_for_topic(topic_hint, api_key, client)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(trend_data, f, ensure_ascii=False, indent=2)

    print(f"\nTrend analizi tamamlandı -> {args.out} ({len(trend_data)} video)")
    tier_labels = {
        "tier1_quarter_high_performer": "[SON 90 GÜN, 250K+]",
        "tier2_yearly_high_performer": "[SON 1 YIL, 2M+]",
        "tier3_relaxed": "[SON 1 YIL, EŞİKSİZ]",
    }
    for v in trend_data[:8]:
        tag = tier_labels.get(v["source"], "[BİLİNMEYEN]")
        print(f"  {tag} {v['views']:>10,} izlenme - {v['title']}")


if __name__ == "__main__":
    main()
