"""
Seçilen tek başlık için TEK bir kapak (thumbnail) üretir (Wiro API,
openai/gpt-image-2) ve üzerine metin + avatar bindirir (PIL).

ÖNEMLİ (kapak-başlık ilişkisi):
Kapak, başlığın bir tekrarı DEĞİLDİR. Kapak, izleyiciye ham bir
SORU/GİZEM sunar (görsel + 2-3 kelimelik kışkırtıcı metin); başlık bu
sorunun bağlamını/gelişmesini verir ama cevabı vermez; videonun kendisi
asıl cevabı verir. Bu yüzden kapak için Claude'dan başlıktan bağımsız,
daha ham ve daha az bilgi veren bir "hook" konsepti üretiliyor.

TREND REFERANSI (--trends verilirse): trend_analysis.py'nin çektiği
nişte GERÇEKTEN tutan videoların BAŞLIKLARI (görselleri DEĞİL - telif
riski olmasın diye sadece metin kalıpları) bağlam olarak Claude'a
veriliyor. Amaç birebir kopyalamak değil, "bu nişte şu enerji/ton işe
yarıyor" sinyalini kapak konseptine yansıtmak - hâlâ tamamen orijinal
bir görsel/metin üretiliyor.

NOT: YouTube'un native A/B testi (Test & Compare) API'den erişilemiyor
ve YPP üyeliği gerektiriyor, bu yüzden sistem artık çoklu kapak yerine
tek, en güçlü kapağı üretiyor (bkz. generate_titles.py).

GÖRSEL DİL:
- Ok/daire vurgusu KALDIRILDI (AI modeli tutarlı/alakalı konuma
  yerleştiremiyordu).
- Metin sol altta, kenardan uzakta, koyu/yüksek kontrast bir kutu
  üzerinde, EN FAZLA 3 KELİME - CTR artırmak için daha punch'lı.
- Sağ altta, HER VİDEOYA ÖZEL üretilen avatar yüzü - karakterin genel
  kimliği (yüz/gözlük/saç tarzı) korunur, sadece YÜZ İFADESİ videonun
  konusuna göre değişir (şok, merak, endişe vb.), tekrar önlenir.
  Gözlük/ceket TAM renk eşleşmesi zorlanmıyor (üretim başarısızlığını
  azaltmak için) - asıl önemli olan yüz/karakter kimliğinin tanınabilir
  kalması. Sadece yüz+omuz üretiliyor, el YOK (AI'da tutarsız
  çıkabiliyor, riskten kaçınmak için tarif dışında bırakıldı).
  Üretim başarısız olursa sabit assets/avatar/open_blink.png'ye
  düşülür; o da yoksa avatar hiç eklenmez - pipeline asla kırılmaz.
- Dört kenara kanal logosunun renginde ince bir çerçeve (marka
  tutarlılığı için).

Claude API çağrısı geçici hatalara (500, rate limit, bağlantı kopması)
karşı otomatik olarak yeniden dener (bkz. call_claude).

Kullanım:
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/ --script script.md --trends trend.json
"""
import argparse
import json
import os
import random
import textwrap
import time

import anthropic
from PIL import Image, ImageDraw, ImageFont

from wiro_client import run_model, download_output

MODEL_CREATIVE = "claude-sonnet-4-6"

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)

THUMBNAIL_STYLE = (
    "dramatic mystery-documentary YouTube thumbnail aesthetic, bold "
    "high-contrast lighting, strong single subject that fills a "
    "significant portion of the frame, cinematic reveal-moment "
    "composition, rich saturated color grading (deep reds, blacks, "
    "warm ambers, or cold dramatic blues work well), sharp and "
    "eye-catching even at small size, tech/gaming themed, leaves clear "
    "empty space in one area for text overlay, no existing text in the "
    "image, 16:9"
)

FONT_PATH = "assets/fonts/Anton-Regular.ttf"

# Kanal logosunun rengi (sarı-yeşil/lime) - çerçeve için kullanılıyor.
BORDER_COLOR = (205, 220, 57)
BORDER_WIDTH = 4  # piksel

TEXT_COLOR_PALETTE = [
    (255, 255, 255),  # beyaz - klasik, her zaman okunaklı
    (255, 214, 0),    # sarı - dikkat çekici, gizem/uyarı hissi
    (255, 59, 48),    # kırmızı - dramatik, acil/şok hissi
]

# Karakterin genel kimlik tarifi - avatar_overlay.py'de kullanılan
# görsellerle aynı karakteri referans alıyor. Gözlük/ceket TAM renk
# eşleşmesi zorlanmıyor (üretim başarısızlığını azaltmak için); asıl
# önemli olan yüz/karakter kimliğinin tanınabilir kalması.
CHARACTER_DESCRIPTION = (
    "a man in his late twenties to mid-thirties, glasses, short neat "
    "brown hair, a dark tech-themed jacket, warm skin tone, calm and "
    "intelligent baseline demeanor, stylized semi-realistic digital "
    "illustration style - exact glasses/jacket color can vary slightly "
    "between generations, the face and overall character identity "
    "should stay recognizable and consistent"
)

# Dinamik avatar üretimi başarısız olursa düşülecek sabit yedek.
FALLBACK_AVATAR_PATH = "assets/avatar/open_blink.png"

AVATAR_HEIGHT_FRAC = 0.62  # kapak yüksekliğinin ne kadarını kaplasın

GREEN_SCREEN_BG = (
    "SOLID FLAT PURE GREEN BACKGROUND (chroma key green screen, "
    "#00FF00, completely uniform, no gradient, no shadow, no texture)"
)

MAX_TREND_TITLES = 8  # prompt'a en fazla kaç trend başlığı eklensin


def call_claude(client, prompt, model=MODEL_CREATIVE, max_tokens=600):
    """Claude'a istek atar; geçici hatalarda (500/rate limit/bağlantı)
    üstel bekleme ile otomatik olarak yeniden dener."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in response.content if b.type == "text")
        except RETRYABLE_EXCEPTIONS as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  UYARI: Claude API geçici hata ({type(e).__name__}), "
                      f"{delay}sn sonra tekrar deneniyor "
                      f"(deneme {attempt + 1}/{MAX_RETRIES})...")
                time.sleep(delay)
    raise last_error


def load_trend_titles(trends_path: str) -> list:
    """trend_analysis.py çıktısından SADECE başlıkları (görsel/link
    değil) çıkarır - en çok izlenen ilk MAX_TREND_TITLES kadarını.
    Dosya yoksa ya da okunamazsa boş liste döner, pipeline kırılmaz."""
    if not trends_path or not os.path.exists(trends_path):
        return []
    try:
        with open(trends_path, "r", encoding="utf-8") as f:
            trend_data = json.load(f)
        if not isinstance(trend_data, list):
            return []
        return [v.get("title", "") for v in trend_data[:MAX_TREND_TITLES] if v.get("title")]
    except (json.JSONDecodeError, OSError):
        return []


def generate_thumbnail_concept(client, title: str, script_excerpt: str,
                                trend_titles: list) -> dict:
    """
    Başlıktan BAĞIMSIZ bir kapak konsepti üretir: bir görsel açıklama
    (image prompt), çok kısa bir kışkırtıcı metin (hook text) VE bu
    videoya özel bir avatar yüz ifadesi tarifi.
    """
    trend_block = ""
    if trend_titles:
        trend_block = (
            "\n\nBU NİŞTE ŞU AN GERÇEKTEN TUTAN VİDEO BAŞLIKLARI (referans "
            "için - bunları KOPYALAMA, birebir tekrar etme, sadece hangi "
            "TON/ENERJİ/KELİME TARZI işe yaradığını anla ve kendi özgün "
            "hook_text'ine o enerjiyi yansıt):\n"
            + "\n".join(f"- {t}" for t in trend_titles)
        )

    prompt = f"""Bir YouTube kapak görseli (thumbnail) konsepti üret.

KESİN KURAL: Bu kapak, aşağıdaki başlıkla AYNI bilgiyi VERMEMELİ.
Başlık zaten konunun gelişmesini/bağlamını açıklıyor. Kapağın görevi
SADECE ham bir soru/gizem/çelişki sunmak - izleyici "bu ne, ne oluyor"
desin, başlıktaki bilgiyi henüz bilmesin.

BAŞLIK (kapakta bunu tekrar etme, bundan bağımsız düşün): {title}

SCRIPT'TEN KISA ALINTI (konunun özünü anlamak için, kapak metnine
doğrudan kopyalama): {script_excerpt[:800]}
{trend_block}

Üret:
1. "visual_prompt": İngilizce, somut bir SAHNE/NESNE/AN tarifi (ör. bir
   nesnenin garip bir detayı, açıklanamayan bir an). Kişi/karakter
   isimlerinden kaçın, jenerik ve görsel olarak net olsun.
2. "hook_text": İngilizce, TÜM BÜYÜK HARF, EN FAZLA 3 KELİME (2 kelime
   daha da güçlü olur), soru işareti kullanmadan, ŞOK EDİCİ/İDDİALI bir
   ifade - "interesting" değil "impossible to ignore" hissi versin
   (ör. "NEVER EXPLAINED", "THE REAL REASON", "HIDDEN COST"). Kelimeler
   ne kadar az, punch o kadar güçlü. Başlıktaki kelimeleri birebir
   tekrarlama.
3. "avatar_expression": İngilizce, TEK CÜMLE, bu videonun konusuna
   uygun bir YÜZ İFADESİ tarifi (ör. "eyes wide with shock, mouth
   slightly open in disbelief", "one eyebrow raised, intensely
   curious and skeptical expression", "concerned, slightly worried
   expression, brow furrowed"). Sadece yüz ifadesini tarif et, kıyafet/
   saç/gözlük gibi diğer detayları tarif ETME (onlar zaten sabit).

Çıktı SADECE JSON: {{"visual_prompt": "...", "hook_text": "...", "avatar_expression": "..."}}"""

    raw = call_claude(client, prompt)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Güvenli düşüş: jenerik bir konsept
        return {
            "visual_prompt": "a mysterious object under dramatic lighting, "
                              "close-up on one strange unexplained detail",
            "hook_text": "NEVER EXPLAINED",
            "avatar_expression": "eyes wide with shock, mouth slightly open",
        }


def generate_background(prompt: str, out_path: str):
    full_prompt = f"{THUMBNAIL_STYLE}. Subject: {prompt}"
    try:
        task = run_model("openai", "gpt-image-2", {
            "prompt": full_prompt,
            "resolution": "1k",
            "ratio": "16:9",
            "quality": "medium",
            "samples": 1,
        })
        download_output(task, out_path)
    except RuntimeError as e:
        if "safety system" in str(e).lower():
            print("  UYARI: kapak için güvenlik reddi, jenerik tarifle yeniden deniyorum...")
            fallback_prompt = (
                f"{THUMBNAIL_STYLE}. Subject: an abstract, dramatic "
                "technology-themed scene, glowing shapes, no people, no text"
            )
            task = run_model("openai", "gpt-image-2", {
                "prompt": fallback_prompt,
                "resolution": "1k",
                "ratio": "16:9",
                "quality": "medium",
                "samples": 1,
            })
            download_output(task, out_path)
        else:
            raise


def remove_green_screen(image_path: str, out_path: str):
    """Düz yeşil ekran arka planını gerçek şeffaflığa çevirir (numpy
    gerekmeden, saf PIL piksel erişimiyle - tek seferlik, video başına
    1 kez çalıştığı için performans sorun değil)."""
    img = Image.open(image_path).convert("RGBA")
    pixels = img.load()
    w, h = img.size

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            # Yeşil ekran testi: yeşil kanal kırmızı/mavi kanallardan
            # belirgin şekilde yüksekse şeffaf yap.
            if g > 100 and g > r + 40 and g > b + 40:
                pixels[x, y] = (r, g, b, 0)

    img.save(out_path)


def generate_avatar_face(expression: str, out_path: str) -> bool:
    """Bu videoya özel, karakterin genel kimliğini koruyan ama yüz
    ifadesi videoya göre değişen bir avatar üretir. Başarılı olursa
    True, olmazsa False döner (çağıran taraf sabit yedeğe düşer)."""
    prompt = (
        f"A stylized digital illustration portrait of {CHARACTER_DESCRIPTION}, "
        f"face and shoulders only, NO hands or arms visible in frame, "
        f"{expression}, front-facing, centered composition, "
        f"{GREEN_SCREEN_BG}, no text"
    )
    try:
        raw_path = out_path + ".raw.png"
        task = run_model("openai", "gpt-image-2", {
            "prompt": prompt,
            "resolution": "1k",
            "ratio": "1:1",
            "quality": "medium",
            "samples": 1,
        })
        download_output(task, raw_path)
        remove_green_screen(raw_path, out_path)
        os.remove(raw_path)
        return True
    except Exception as e:
        print(f"  UYARI: özel avatar üretimi başarısız ({e}), sabit yedeğe düşülüyor")
        return False


def draw_border(draw, img_w, img_h):
    """Dört kenara, kanal logosunun renginde ince bir çerçeve çizer
    (marka tutarlılığı için)."""
    for i in range(BORDER_WIDTH):
        draw.rectangle(
            [i, i, img_w - 1 - i, img_h - 1 - i],
            outline=BORDER_COLOR,
        )


def paste_avatar(img: Image.Image, avatar_path: str):
    """Kapağın sağ alt köşesine avatar karakterini bindirir. Dosya
    yoksa/yüklenemezse sessizce atlanır - pipeline asla kırılmaz."""
    if not os.path.exists(avatar_path):
        print(f"  UYARI: {avatar_path} bulunamadı, kapakta avatar olmadan devam ediliyor")
        return img.convert("RGB")

    try:
        avatar = Image.open(avatar_path).convert("RGBA")
    except Exception as e:
        print(f"  UYARI: avatar yüklenemedi ({e}), kapakta avatar olmadan devam ediliyor")
        return img.convert("RGB")

    target_h = int(img.height * AVATAR_HEIGHT_FRAC)
    scale = target_h / avatar.height
    target_w = int(avatar.width * scale)
    avatar = avatar.resize((target_w, target_h), Image.LANCZOS)

    # Sağ kenara hafifçe taşacak, alt kenara yapışık - dramatik "ekrandan
    # fırlıyor" hissi. Metin sol altta olduğu için çakışma yok.
    x = img.width - target_w + int(target_w * 0.08)
    y = img.height - target_h

    base = img.convert("RGBA")
    base.alpha_composite(avatar, (x, y))
    return base.convert("RGB")


def overlay_text(image_path: str, hook_text: str, avatar_path: str, out_path: str):
    img = Image.open(image_path).convert("RGB")

    # Avatar EN ÖNCE eklenir ki metin/çerçeve onun üstünde net kalsın
    img = paste_avatar(img, avatar_path)

    draw = ImageDraw.Draw(img, "RGBA")

    max_text_width = int(img.width * 0.6)  # avatar sağı kapladığı için daraltıldı
    max_text_height = int(img.height * 0.38)
    x_margin = int(img.width * 0.04)
    bottom_margin = int(img.height * 0.14)

    short_text = hook_text.strip().upper()

    font_size = int(img.height * 0.16)
    min_font_size = int(img.height * 0.06)

    while font_size > min_font_size:
        font = ImageFont.truetype(FONT_PATH, font_size)
        avg_char_w = font.getbbox("A")[2] - font.getbbox("A")[0]
        wrap_width = max(4, max_text_width // max(avg_char_w, 1))
        wrapped = textwrap.fill(short_text, width=wrap_width)

        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_w <= max_text_width and text_h <= max_text_height:
            break
        font_size -= 4
    else:
        font = ImageFont.truetype(FONT_PATH, min_font_size)
        wrapped = textwrap.fill(short_text, width=10)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10)
        text_h = bbox[3] - bbox[1]

    x = x_margin
    y = img.height - bottom_margin - text_h
    pad = 16
    draw.rectangle(
        [x - pad, y - pad, x + (bbox[2] - bbox[0]) + pad, y + text_h + pad],
        fill=(0, 0, 0, 210),
    )

    for dx in (-3, -1, 0, 1, 3):
        for dy in (-3, -1, 0, 1, 3):
            draw.multiline_text((x + dx, y + dy), wrapped, font=font,
                                 fill=(0, 0, 0, 255), spacing=10)
    text_color = random.choice(TEXT_COLOR_PALETTE)
    draw.multiline_text((x, y), wrapped, font=font, fill=(*text_color, 255), spacing=10)

    # En son, her şeyin üstüne kanal renginde ince çerçeve
    draw_border(draw, img.width, img.height)

    img.save(out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", required=True, help="generate_titles.py çıktısı")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--script", required=False, default="script.md",
                         help="Kapak konsepti için bağlam olarak kullanılacak script dosyası")
    parser.add_argument("--trends", required=False, default="trend.json",
                         help="trend_analysis.py çıktısı - nişte tutan başlıkları referans almak için")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with open(args.titles, "r", encoding="utf-8") as f:
        titles_data = json.load(f)

    title = titles_data["selected"][0]

    script_excerpt = ""
    if os.path.exists(args.script):
        with open(args.script, "r", encoding="utf-8") as f:
            script_excerpt = f.read()

    trend_titles = load_trend_titles(args.trends)
    if trend_titles:
        print(f"  {len(trend_titles)} trend başlığı referans olarak kullanılıyor")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    concept = generate_thumbnail_concept(client, title, script_excerpt, trend_titles)

    raw_path = os.path.join(args.out_dir, "raw_1.png")
    generate_background(concept["visual_prompt"], raw_path)

    # Bu videoya özel avatar üretmeyi dene; başarısız olursa sabit yedeğe düş
    dynamic_avatar_path = os.path.join(args.out_dir, "avatar_face.png")
    avatar_ok = generate_avatar_face(concept.get("avatar_expression", ""), dynamic_avatar_path)
    avatar_path_to_use = dynamic_avatar_path if avatar_ok else FALLBACK_AVATAR_PATH

    final_path = os.path.join(args.out_dir, "thumbnail_1.png")
    overlay_text(raw_path, concept["hook_text"], avatar_path_to_use, final_path)
    print(f"Kapak hazır -> {final_path}  "
          f"(hook: \"{concept['hook_text']}\", avatar: {'özel üretildi' if avatar_ok else 'sabit yedek'}, "
          f"başlık: \"{title}\")")


if __name__ == "__main__":
    main()
