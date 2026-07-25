"""
Üretilen videoyu YouTube'a OAuth ile yükler. Güvenlik için varsayılan
olarak "private" (gizli) yükler - otomasyon hiçbir zaman doğrudan
herkese açık yayınlamaz, sen Studio'dan kontrol edip yayınlarsın.

AI-üretilen içerik olduğu için "containsSyntheticMedia" bayrağı
otomatik True gönderiliyor (YouTube'un 2024 sonrası zorunlu kıldığı
sentetik/değiştirilmiş içerik beyanı).

Kullanım:
    python scripts/upload_youtube.py --video output/final.mp4 \
        --titles titles.json --thumbnail output/thumbnails/thumbnail_1.png
"""
import argparse
import json
import os

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"

DEFAULT_CATEGORY_ID = "28"  # Science & Technology - Studio'dan değiştirilebilir
DESCRIPTION_TEMPLATE = (
    "{title}\n\n"
    "The Unseen Byte digs into the science, psychology, and hidden "
    "history that shape the games you play and the technology you use "
    "every day.\n\n"
    "This video was produced with AI-assisted narration and visuals.\n\n"
    "New videos weekly."
)


def get_access_token() -> str:
    resp = requests.post(TOKEN_URL, data={
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def initiate_upload(access_token: str, title: str, description: str, video_size: int) -> str:
    metadata = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": DEFAULT_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
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
    return resp.headers["Location"]


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

    description = DESCRIPTION_TEMPLATE.format(title=title)
    video_size = os.path.getsize(args.video)

    print("Access token alınıyor...")
    access_token = get_access_token()

    print(f"Yükleme başlatılıyor: \"{title}\"")
    upload_url = initiate_upload(access_token, title, description, video_size)

    print("Video yükleniyor (bu birkaç dakika sürebilir)...")
    result = upload_video_bytes(upload_url, args.video)
    video_id = result["id"]
    print(f"Video yüklendi -> https://youtube.com/watch?v={video_id} (private)")

    if args.thumbnail and os.path.exists(args.thumbnail):
        print("Kapak ayarlanıyor...")
        set_thumbnail(access_token, video_id, args.thumbnail)
        print("Kapak ayarlandı.")

    print("\nTAMAMLANDI. Video 'private' (gizli) olarak yüklendi.")
    print("YouTube Studio'ya girip kontrol et, uygunsa yayına al ve")
    print("A/B Testing (Test & Compare) ile diğer 2 kapak/başlığı da ekle.")


if __name__ == "__main__":
    main()
