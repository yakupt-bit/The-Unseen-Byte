"""
Script için 8 başlık adayı üretir, içlerinden EN GÜÇLÜ olanı seçer ve
titles.json'a kaydeder.

MODEL DEĞİŞİKLİĞİ: Bu script artık Claude yerine Gemini API kullanıyor
(google-genai SDK). Üretim için gemini-3.6-flash, seçim/sıralama için
gemini-3.1-flash-lite kullanılıyor.

NOT: YouTube'un native "Test & Compare" (A/B testing) özelliği API
üzerinden erişilemiyor, bu yüzden sistem doğrudan en güçlü tek başlığı
seçip kullanıyor.

TREND REFERANSI (--trends verilirse): trend_analysis.py'nin çektiği
GERÇEK, DOĞRULANMIŞ performans verisi hem üretim hem SEÇİM adımına
besleniyor.

--- BUGÜNKÜ EKLEME: BAŞLIK ŞABLONU TEKRARI ÖNLEME ---
Kanalın gerçek yayınlanmış başlıklarına bakıldığında ("One Room Changed
Everything", "One Call Changed Everything" gibi) AYNI CÜMLE KALIBININ
tekrar tekrar kullanıldığı görüldü - konular farklı olsa bile yapı hep
aynı kaldığı için izleyiciye "şablon/doldurma içerik" hissi veriyordu.
research.py'nin used_topics.json ile konu tekrarını önlemesiyle AYNI
mantık burada da uygulandı:
  - used_titles.json (repo kökünde, her koşuda güncellenir) geçmişte
    seçilmiş TÜM başlıkları tutar.
  - Yeni başlık adayları üretilirken bu liste Claude'a değil Gemini'ye
    "bu YAPISAL KALIPLARI (ör. 'One X Changed Everything', 'The X That
    Y') TEKRAR ETME, her seferinde gramatik olarak FARKLI bir cümle
    yapısı kullan" talimatıyla veriliyor.
  - Ton talimatı da AGRESİF/İDDİALI olacak şekilde güçlendirildi -
    yumuşak/genel ifadeler yerine doğrudan, çarpıcı, "durdurucu"
    cümleler istendi.
JSON parse artık extract_json_array/object ile daha sağlam - eskiden
saf json.loads Gemini yanıtın başına/sonuna metin eklediğinde sessizce
çöküyordu.

Kullanım:
    python scripts/generate_titles.py --script script.md --out titles.json
    python scripts/generate_titles.py --script script.md --out titles.json --trends trend.json
"""
import argparse
import json
import os
import time

from google import genai
from google.genai import types

MODEL_CREATIVE = "gemini-3.6-flash"
MODEL_UTILITY = "gemini-3.1-flash-lite"
BRAND_SUFFIX = " | The Unseen Byte"

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

MAX_TREND_REFERENCES = 8  # prompt'a en fazla kaç kanıtlanmış örnek eklensin

USED_TITLES_FILE = "used_titles.json"
MAX_TITLES_IN_PROMPT = 30  # şablon tekrarı kontrolü için en fazla kaç geçmiş başlık gösterilsin


def extract_json_array(raw: str):
    """Modelin çıktısındaki JSON dizisini, öncesinde/sonrasında
    açıklama metni olsa bile güvenilir şekilde çıkarır."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise json.JSONDecodeError("JSON dizisi bulunamadı", cleaned, 0)
    return json.loads(cleaned[start:end + 1])


def extract_json_object(raw: str) -> dict:
    """Modelin çıktısındaki JSON objesini, öncesinde/sonrasında
    açıklama metni olsa bile güvenilir şekilde çıkarır."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise json.JSONDecodeError("JSON objesi bulunamadı", cleaned, 0)
    return json.loads(cleaned[start:end + 1])


def load_used_titles() -> list:
    if not os.path.exists(USED_TITLES_FILE):
        return []
    try:
        with open(USED_TITLES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_used_titles(titles: list):
    with open(USED_TITLES_FILE, "w", encoding="utf-8") as f:
        json.dump(titles, f, ensure_ascii=False, indent=2)


def format_used_titles_block(used_titles: list) -> str:
    """Geçmiş başlıkları, YAPISAL KALIP tekrarını önlemek için prompt'a
    eklenecek bir metin bloğuna çevirir. Amaç kelime tekrarı değil,
    CÜMLE YAPISI/KALIBI tekrarını önlemek (ör. 'One X Changed
    Everything' formülü)."""
    if not used_titles:
        return ""
    recent = used_titles[-MAX_TITLES_IN_PROMPT:]
    lines = "\n".join(f"- {t}" for t in recent)
    return (
        "\n\nDAHA ÖNCE KULLANILMIŞ GERÇEK BAŞLIKLAR (bunları incele - "
        "eğer birden fazlası AYNI CÜMLE KALIBINI kullanıyorsa, ör. "
        "'One [Kelime] Changed Everything' ya da 'The [Kelime] That "
        "[Kelime]' gibi bir şablon fark edersen, YENİ ADAYLARINDA BU "
        "KALIBI KESİNLİKLE TEKRARLAMA - gramatik olarak TAMAMEN FARKLI "
        "bir cümle yapısı kullan. Kelime tekrarı sorun değil, ASIL "
        "SORUN CÜMLE İSKELETİNİN tekrar etmesi):\n"
        + lines
    )


def call_gemini(client, prompt, model, max_tokens=800):
    """Gemini'ye istek atar; geçici hatalarda üstel bekleme ile
    otomatik olarak yeniden dener."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=max_tokens),
            )
            return response.text or ""
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  UYARI: Gemini API geçici hata ({type(e).__name__}), "
                      f"{delay}sn sonra tekrar deneniyor "
                      f"(deneme {attempt + 1}/{MAX_RETRIES})...")
                time.sleep(delay)
    raise last_error


def load_trend_references(trends_path: str) -> list:
    """trend_analysis.py çıktısından başlık + GERÇEK performans verisini
    çıkarır."""
    if not trends_path or not os.path.exists(trends_path):
        return []
    try:
        with open(trends_path, "r", encoding="utf-8") as f:
            trend_data = json.load(f)
        if not isinstance(trend_data, list) or not trend_data:
            return []

        refs = []
        for v in trend_data:
            title = v.get("title", "")
            views = v.get("views", 0)
            likes = v.get("likes", 0)
            if not title or views <= 0:
                continue
            engagement = round((likes / views) * 100, 2) if views else 0.0
            refs.append({"title": title, "views": views, "engagement": engagement})

        refs.sort(key=lambda r: r["views"], reverse=True)
        return refs[:MAX_TREND_REFERENCES]
    except (json.JSONDecodeError, OSError, TypeError):
        return []


def format_trend_block(trend_refs: list) -> str:
    if not trend_refs:
        return ""
    lines = []
    for r in trend_refs:
        views_str = f"{r['views']:,}".replace(",", ".")
        lines.append(f"- \"{r['title']}\" -> {views_str} izlenme, %{r['engagement']} etkileşim oranı")
    return (
        "\n\nBU NİŞTE SON DÖNEMDE GERÇEKTEN BÜYÜK İZLENME ALMIŞ "
        "BAŞLIKLAR - GERÇEK, DOĞRULANMIŞ VERİ (YouTube Data API):\n"
        + "\n".join(lines)
        + "\n\nBunları BİREBİR KOPYALAMA/tekrar etme. Ama şunu analiz et: "
        "bu başlıklarda ortak hangi YAPI (soru mu, iddia mı, sayı mı), "
        "hangi UZUNLUK, hangi MERAK AÇIĞI TEKNİĞİ var ve YÜKSEK etkileşim "
        "oranına sahip olanlar hangi farkı taşıyor - kendi özgün "
        "adaylarına bu kanıtlanmış sinyali yansıt."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--trends", required=False, default="trend.json",
                         help="trend_analysis.py çıktısı")
    args = parser.parse_args()

    with open(args.script, "r", encoding="utf-8") as f:
        script = f.read()

    trend_refs = load_trend_references(args.trends)
    trend_block = format_trend_block(trend_refs)
    if trend_refs:
        print(f"  {len(trend_refs)} kanıtlanmış (gerçek izlenme verili) başlık referans olarak kullanılıyor")
    else:
        print("  Trend referansı yok/boş, kalıp-tabanlı üretime devam ediliyor")

    used_titles = load_used_titles()
    used_titles_block = format_used_titles_block(used_titles)
    print(f"  Geçmiş başlık sayısı (şablon tekrarı kontrolü için): {len(used_titles)}")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    gen_prompt = f"""Bu script için 8 farklı YouTube başlığı öner.
ÖNEMLİ: Başlıkların TAMAMI İNGİLİZCE olmalı, tek bir Türkçe kelime bile
kullanma - kanal İngilizce ve global bir kitleye hitap ediyor.

TON: AGRESİF ve İDDİALI ol. Yumuşak, nazik, genel-geçer ifadelerden
KAÇIN. Her başlık okuyanı DURDURMALI - doğrudan, çarpıcı, biraz
küstah bir özgüvenle yazılmış olsun (ör. "nobody talks about this",
"they don't want you to know", "everyone got this wrong"). Klişe
belgesel yumuşaklığından uzak dur.

Her biri merak açığı yaratmalı (bilgiyi tam vermeden merak uyandırmalı),
abartılı/yalan olmamalı, 60 karakteri geçmemeli, tık tuzağı olmamalı.

KRİTİK KURAL - başlık kapağın CEVABI değil GELİŞMESİDİR:
Kapak görseli izleyiciye bir soru/gizem sunar. Başlık bu sorunun
CEVABINI VERMEZ, sadece konunun BAĞLAMINI/GELİŞMESİNİ ekler - somut
bir çelişki, rakam veya anomali içerir. Asıl cevap/sonuç SADECE
videoyu izleyince ortaya çıkmalı.

CÜMLE YAPISI ÇEŞİTLİLİĞİ ZORUNLU: 8 adayın HER BİRİ FARKLI bir gramatik
yapı kullanmalı - aynı iskeleti ("One [X] Changed Everything" gibi)
birden fazla adayda TEKRARLAMA. Global çapta kanıtlanmış gizem/belgesel
kanallarından çıkarılan FARKLI kalıpları kullan:
1. Soru formatı ("Is X Really Y?", "Why Does X Happen?")
2. "Ne oldu" gizem çerçevesi ("What Really Happened to X")
3. Güçlü iddia + merak açığı ("The Real Reason X Never Y")
4. Sayı/liste formatı ("X Things You Didn't Know About Y")
5. Doğrudan izleyiciye hitap eden meydan okuma tarzı
6. İtiraf/açığa çıkarma tarzı ("Nobody Tells You X")
7. Zıtlık/çelişki vurgusu ("X Looked Y. It Wasn't.")
8. Zaman baskısı/aciliyet tarzı ("For Years, X Hid This")
{trend_block}
{used_titles_block}

SCRIPT:
{script}

Çıktı SADECE JSON dizi, İngilizce başlıklarla: ["title1", "title2", ...]"""

    raw_candidates = call_gemini(client, gen_prompt, MODEL_CREATIVE, max_tokens=1200)
    try:
        candidates = extract_json_array(raw_candidates)
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("boş/liste değil")
    except (json.JSONDecodeError, ValueError):
        print(f"  UYARI: başlık adayları parse edilemedi, jenerik yedek kullanılıyor. "
              f"Ham yanıt: {raw_candidates[:200]!r}")
        candidates = ["What Really Happened Here"]

    rank_prompt = f"""Aşağıdaki İngilizce YouTube başlık adaylarından EN
GÜÇLÜ tek bir tanesini seç. Kriterler: merak açığı gücü, netlik,
özgünlük hissi, AGRESİFLİK/iddialılık (yumuşak/genel ifadeler DÜŞÜK
puan almalı), tık tuzağı olmaması.
{trend_block}
{used_titles_block}
Yukarıdaki kanıtlanmış (gerçek izlenme verili) başlıklarla yapısal
benzerlik taşıyan adaylara hafif öncelik ver - ama DAHA ÖNCE
KULLANILMIŞ ŞABLONLARLA aynı cümle iskeletini taşıyan bir aday varsa
o adayı SEÇME, diğer adayları tercih et.

ADAYLAR: {json.dumps(candidates, ensure_ascii=False)}

Çıktı SADECE JSON (başlık İngilizce kalacak, gerekçe Türkçe olabilir):
{{"selected": "title", "reason": "gerekçe"}}"""

    raw_rank = call_gemini(client, rank_prompt, MODEL_UTILITY, max_tokens=600)
    try:
        result = extract_json_object(raw_rank)
    except json.JSONDecodeError:
        print(f"  UYARI: seçim yanıtı parse edilemedi, ilk aday başlık kullanılıyor. "
              f"Ham yanıt: {raw_rank[:200]!r}")
        result = {"selected": candidates[0], "reason": "otomatik yedek seçim (parse hatası)"}

    final_title = result["selected"]
    if not final_title.endswith(BRAND_SUFFIX):
        final_title += BRAND_SUFFIX

    output = {"selected": [final_title], "reasons": [result["reason"]]}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Seçilen başlık, gelecekteki şablon tekrarı kontrolü için kaydedilir.
    used_titles.append(final_title)
    save_used_titles(used_titles)

    print(f"Seçilen başlık: {final_title}  ({result['reason']})")


if __name__ == "__main__":
    main()
