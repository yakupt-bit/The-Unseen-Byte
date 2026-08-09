"""
Script için 8 başlık adayı üretir, içlerinden EN GÜÇLÜ olanı seçer ve
titles.json'a kaydeder.

NOT: YouTube'un native "Test & Compare" (A/B testing) özelliği sadece
YouTube Studio arayüzünden (desktop, elle) kullanılabiliyor, API
üzerinden erişilemiyor; ayrıca YouTube Partner Program (YPP) üyeliği
gerektiriyor. Kanal bu eşiklere ulaşana kadar tek başlık üretmek daha
mantıklı - bu yüzden A/B akışı kaldırıldı, sistem artık doğrudan en
güçlü tek başlığı seçip kullanıyor.

TREND REFERANSI (--trends verilirse): trend_analysis.py'nin YouTube Data
API ile çektiği GERÇEK, DOĞRULANMIŞ performans verisi (son 30 günde
250k+ izlenme almış videoların başlığı + gerçek izlenme/beğeni sayısı)
hem üretim hem SEÇİM adımına besleniyor. Amaç birebir kopyalamak değil,
"bu nişte hangi başlık YAPISI/TONU gerçekten büyük izlenme çekmiş"
sinyalini kullanmak - hâlâ tamamen özgün başlıklar üretiliyor. Sadece
metin kalıbı kullanılıyor (görsel/link değil), telif riski yok.

Her Claude API çağrısı geçici hatalara (500, rate limit, bağlantı
kopması) karşı otomatik olarak yeniden dener (bkz. call_claude).

Kullanım:
    python scripts/generate_titles.py --script script.md --out titles.json
    python scripts/generate_titles.py --script script.md --out titles.json --trends trend.json
"""
import argparse
import json
import os
import time

import anthropic

MODEL_CREATIVE = "claude-sonnet-4-6"
MODEL_UTILITY = "claude-haiku-4-5-20251001"
BRAND_SUFFIX = " | The Unseen Byte"

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)

MAX_TREND_REFERENCES = 8  # prompt'a en fazla kaç kanıtlanmış örnek eklensin


def call_claude(client, prompt, model, max_tokens=800):
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


def load_trend_references(trends_path: str) -> list:
    """trend_analysis.py çıktısından başlık + GERÇEK performans verisini
    (izlenme, beğeni oranı) çıkarır. En yüksek izlenmeye göre sıralı,
    en fazla MAX_TREND_REFERENCES kadar. Dosya yoksa/okunamazsa ya da
    boşsa boş liste döner - pipeline kırılmaz, eski davranışa düşülür."""
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

        # En çok izlenene göre sırala - en güçlü kanıt en üstte
        refs.sort(key=lambda r: r["views"], reverse=True)
        return refs[:MAX_TREND_REFERENCES]
    except (json.JSONDecodeError, OSError, TypeError):
        return []


def format_trend_block(trend_refs: list) -> str:
    """Trend referanslarını, Claude'a hem üretim hem seçim adımında
    verilecek okunabilir bir metin bloğuna çevirir."""
    if not trend_refs:
        return ""
    lines = []
    for r in trend_refs:
        views_str = f"{r['views']:,}".replace(",", ".")
        lines.append(f"- \"{r['title']}\" -> {views_str} izlenme, %{r['engagement']} etkileşim oranı")
    return (
        "\n\nBU NİŞTE SON 30 GÜNDE GERÇEKTEN BÜYÜK İZLENME ALMIŞ (250k+) "
        "BAŞLIKLAR - GERÇEK, DOĞRULANMIŞ VERİ (YouTube Data API):\n"
        + "\n".join(lines)
        + "\n\nBunları BİREBİR KOPYALAMA/tekrar etme. Ama şunu analiz et: "
        "bu başlıklarda ortak hangi YAPI (soru mu, iddia mı, sayı mı), "
        "hangi UZUNLUK, hangi MERAK AÇIĞI TEKNİĞİ var ve YÜKSEK etkileşim "
        "oranına sahip olanlar (düşük etkileşimlilere göre) hangi "
        "farkı taşıyor - kendi özgün adaylarına bu kanıtlanmış sinyali yansıt."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--trends", required=False, default="trend.json",
                         help="trend_analysis.py çıktısı - nişte gerçekten izlenen başlıkları referans almak için")
    args = parser.parse_args()

    with open(args.script, "r", encoding="utf-8") as f:
        script = f.read()

    trend_refs = load_trend_references(args.trends)
    trend_block = format_trend_block(trend_refs)
    if trend_refs:
        print(f"  {len(trend_refs)} kanıtlanmış (gerçek izlenme verili) başlık referans olarak kullanılıyor")
    else:
        print("  Trend referansı yok/boş, kalıp-tabanlı üretime devam ediliyor")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    gen_prompt = f"""Bu script için 8 farklı YouTube başlığı öner.
ÖNEMLİ: Başlıkların TAMAMI İNGİLİZCE olmalı, tek bir Türkçe kelime bile
kullanma - kanal İngilizce ve global bir kitleye hitap ediyor.

Her biri merak açığı yaratmalı (bilgiyi tam vermeden merak uyandırmalı),
abartılı/yalan olmamalı, 60 karakteri geçmemeli, tık tuzağı olmamalı.

KRİTİK KURAL - başlık kapağın CEVABI değil GELİŞMESİDİR:
Kapak görseli izleyiciye bir soru/gizem sunar (örn. bir nesne, bir
çelişkili durum). Başlık bu sorunun CEVABINI VERMEZ, sadece konunun
BAĞLAMINI/GELİŞMESİNİ ekler - somut bir çelişki, rakam veya anomali
içerir (ör. "sold for $X", "hidden for Y years", "no one noticed").
Asıl cevap/sonuç SADECE videoyu izleyince ortaya çıkmalı. Başlık,
kapaktaki merakı KAPATMAMALI, bir adım daha derinleştirmeli.

Global çapta kanıtlanmış gizem/belgesel kanallarından çıkarılan
kalıpları kullan, adaylar bu FARKLI YAKLAŞIMLARI temsil etsin:
1. Soru formatı ("Is X Really Y?", "Why Does X Happen?")
2. "Ne oldu" gizem çerçevesi ("What Really Happened to X")
3. Güçlü iddia + merak açığı ("The Real Reason X Never Y")
4. Sayı/liste formatı ("X Things You Didn't Know About Y")
5. Doğrudan izleyiciye hitap eden meydan okuma tarzı
{trend_block}

SCRIPT:
{script}

Çıktı SADECE JSON dizi, İngilizce başlıklarla: ["title1", "title2", ...]"""

    raw_candidates = call_claude(client, gen_prompt, MODEL_CREATIVE)
    cleaned = raw_candidates.replace("```json", "").replace("```", "").strip()
    candidates = json.loads(cleaned)

    rank_prompt = f"""Aşağıdaki İngilizce YouTube başlık adaylarından EN
GÜÇLÜ tek bir tanesini seç. Kriterler: merak açığı gücü (cevabı
vermeden gelişmeyi vermesi), netlik, özgünlük hissi, tık tuzağı
olmaması.
{trend_block}
Yukarıdaki kanıtlanmış (gerçek izlenme verili) başlıklarla yapısal
benzerlik taşıyan adaylara, diğer her şey eşitken, hafif öncelik ver -
ama asıl kriter hâlâ merak açığı gücü ve özgünlük, sadece kanıtlanmış
patern eşleşmesi değil.

ADAYLAR: {json.dumps(candidates, ensure_ascii=False)}

Çıktı SADECE JSON (başlık İngilizce kalacak, gerekçe Türkçe olabilir):
{{"selected": "title", "reason": "gerekçe"}}"""

    raw_rank = call_claude(client, rank_prompt, MODEL_UTILITY, max_tokens=300)
    cleaned_rank = raw_rank.replace("```json", "").replace("```", "").strip()
    result = json.loads(cleaned_rank)

    final_title = result["selected"]
    if not final_title.endswith(BRAND_SUFFIX):
        final_title += BRAND_SUFFIX

    output = {"selected": [final_title], "reasons": [result["reason"]]}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Seçilen başlık: {final_title}  ({result['reason']})")


if __name__ == "__main__":
    main()
