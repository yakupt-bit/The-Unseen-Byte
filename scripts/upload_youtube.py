"""
Üretilen videoyu YouTube'a OAuth ile yükler.

Zamanlama mantığı:
- Pipeline saat 17:00 TR'den ÖNCE biterse -> video "private" olarak
  yüklenir, publishAt = bugün 17:00 TR olarak ayarlanır, YouTube o
  saatte otomatik herkese açık yapar.
- Pipeline saat 17:00 TR'yi GEÇTİKTEN sonra biterse (örn. 17:13'te
  hazır olduysa) -> zamanlama yapılmaz, video doğrudan "public"
  olarak hemen yayınlanır (geçmiş bir saate zamanlamak YouTube'da
  hataya yol açar, bu yüzden bu durumda direkt yayına alıyoruz).

AI-üretilen içerik olduğu için "containsSyntheticMedia" bayrağı
otomatik True gönderiliyor (YouTube'un 2024 sonrası zorunlu kıldığı
sentetik/değiştirilmiş içerik beyanı).

Açıklamaya, VİDEOYA ÖZEL 5 hashtag eklenir (Claude ile, başlığa göre
üretilir - artık her videoda aynı 3 sabit hashtag değil). Etiketler
(tags) kanalın anahtar kelime listesinden dolduruluyor (YouTube'un
500 karakter toplam sınırına uyacak şekilde).

Kullanım:
    python scripts/upload_youtube.py --video output/final.mp4 \
        --titles titles.json --thumbnail output/thumbnails/thumbnail_1.png
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import anthropic
import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"

DEFAULT_CATEGORY_ID = "28"
TARGET_PUBLISH_HOUR_TR = 21  # Hedef yayın saati (Türkiye saati) - global (ABD/İngiltere) İngilizce kitle için optimize edildi
FALLBACK_HASHTAGS = ["#GamingScience", "#TechMysteries", "#GamingFacts", "#Gaming", "#TechHistory"]

DESCRIPTION_BODY = (
    "{title}\n\n"
    "The Unseen Byte digs into the science, psychology, and hidden "
    "history that shape the games you play and the technology you use "
    "every day.\n\n"
    "This video was produced with AI-assisted narration and visuals.\n\n"
    "New videos weekly.\n\n"
    "{hashtags}"
)

TAGS = [
    "gaming science", "tech mysteries", "gaming psychology",
    "hidden history technology", "gaming facts explained",
    "science of video games", "tech documentary", "video game history",
    "retro gaming facts", "esports science", "why games are addictive",
    "video game industry secrets", "tech history documentary",
    "hardware myths debunked", "gaming brain science",
    "unsolved tech mysteries", "gaming culture explained",
    "tech facts you didn't know", "forgotten technology",
    "gaming neuroscience",
]


def generate_hashtags(client, title: str) -> list:
    """Başlığa göre videoya ÖZEL 5 hashtag üretir (sabit liste değil).
    Herhangi bir hata/parse sorununda FALLBACK_HASHTAGS'e düşer, upload
    akışı asla bu yüzden kesilmez."""
    prompt = f"""Bu YouTube video başlığına göre, açıklamaya eklenecek
5 tane spesifik, videoya özel İngilizce hashtag üret (genel/sabit
hashtag değil, bu videonun konusuna gerçekten uygun olsun).

Başlık: "{title}"

SADECE JSON dizi formatında yaz, başka hiçbir şey yazma:
["#Etiket1", "#Etiket2", "#Etiket3", "#Etiket4", "#Etiket5"]"""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        tags = json.loads(cleaned)
        if isinstance(tags, list) and len(tags) >= 3:
            return tags[:5]
    except Exception as e:
        print(f"  UYARI: hashtag üretimi başarısız ({type(e).__name__}), "
              f"sabit yedek hashtag'ler kullanılıyor")
    return FALLBACK_HASHTAGS


def build_tags_within_limit(tags: list, limit: int = 480) -> list:
    result, total = [], 0
    for tag in tags:
        total += len(tag) + 1
        if total > limit:
            break
        result.append(tag)
    return result


def compute_publish_at() -> str | None:
    """
    Şu an TR saatiyle hedef saati (17:00) geçmediyse, bugün 17:00 TR'nin
    UTC/RFC3339 karşılığını döndürür (zamanlı yayın için).
    Hedef saat zaten geçtiyse None döner (bu, "hemen yayınla" demektir).
    """
    tr_tz = timezone(timedelta(hours=3))  # Europe/Istanbul (DST'siz sabit ofset)
    now_tr = datetime.now(tr_tz)
    target_tr = now_tr.replace(
        hour=TARGET_PUBLISH_HOUR_TR, minute=0, second=0, microsecond=0
    )

    if now_tr >= target_tr:
        # Hedef saat zaten geçmiş (örn. 17:13'te hazır oldu) -> hemen yayınla
        return None

    return target_tr.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_access_token() -> str:
    resp = requests.post(TOKEN_URL, data={
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def initiate_upload(access_token: str, title: str, description: str, video_size: int) -> tuple:
    publish_at = compute_publish_at()

    status = {
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    else:
        status["privacyStatus"] = "public"

    metadata = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": build_tags_within_limit(TAGS),
            "categoryId": DEFAULT_CATEGORY_ID,
        },
        "status": status,
    }
    resp = requests.post(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(video_size),
            "X-Upload-Content-Type": "video/mp4",
        },
        json=metadata,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.headers["Location"], publish_at


def upload_video_bytes(upload_url: str, video_path: str) -> dict:
    with open(video_path, "rb") as f:
        video_data = f.read()
    resp = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=video_data,
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()


def set_thumbnail(access_token: str, video_id: str, thumbnail_path: str):
    with open(thumbnail_path, "rb") as f:
        image_data = f.read()
    resp = requests.post(
        f"{THUMBNAIL_URL}?videoId={video_id}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "image/png",
        },
        data=image_data,
        timeout=60,
    )
    resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--titles", required=True)
    parser.add_argument("--thumbnail", required=False, default=None)
    args = parser.parse_args()

    with open(args.titles, "r", encoding="utf-8") as f:
        titles_data = json.load(f)
    title = titles_data["selected"][0]

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    hashtags = generate_hashtags(client, title)
    print(f"Üretilen hashtag'ler: {' '.join(hashtags)}")

    description = DESCRIPTION_BODY.format(title=title, hashtags=" ".join(hashtags))
    video_size = os.path.getsize(args.video)

    print("Access token alınıyor...")
    access_token = get_access_token()

    print(f"Yükleme başlatılıyor: \"{title}\"")
    upload_url, publish_at = initiate_upload(access_token, title, description, video_size)

    print("Video yükleniyor (bu birkaç dakika sürebilir)...")
    result = upload_video_bytes(upload_url, args.video)
    video_id = result["id"]

    if publish_at:
        print(f"Video yüklendi -> https://youtube.com/watch?v={video_id}")
        print(f"(private, {publish_at} UTC'de -bugün 17:00 TR- otomatik yayına girecek)")
    else:
        print(f"Video yüklendi -> https://youtube.com/watch?v={video_id} (public, hemen yayınlandı)")

    if args.thumbnail and os.path.exists(args.thumbnail):
        print("Kapak ayarlanıyor...")
        set_thumbnail(access_token, video_id, args.thumbnail)
        print("Kapak ayarlandı.")

    if publish_at:
        print("\nTAMAMLANDI. Video 17:00 TR saatinde otomatik yayına girecek.")
    else:
        print("\nTAMAMLANDI. Saat 17:00 TR zaten geçtiği için video hemen HERKESE AÇIK olarak yayınlandı.")
    print("İstersen YouTube Studio'dan A/B Testing (Test & Compare) ile")
    print("diğer 2 kapak/başlığı da ekleyebilirsin.")


if __name__ == "__main__":
    main()
