# YouTube Uzun Video Otomasyon Pipeline'ı (örnek iskelet)

Niş: **Gaming & Tech Mysteries — Untold Science** (bkz. `prompts/niche.md`),
tamamen evergreen, belgesel/gizem tonunda.

Bu repo, uzun format bir YouTube kanalı için araştırma → script →
başlık → seslendirme → altyazı → sahne görseli → kapak → render →
müzik miksajı adımlarını GitHub Actions ile otomatikleştiren bir
**örnek/başlangıç iskeleti**. Çalıştırmadan önce kendi API anahtarlarını
eklemen ve script'leri test edip ince ayar yapman gerekiyor.

## Dürüst olmak gerekirse

- **Hiçbir sistem viral videoyu garanti edemez.** Kurduğum şey CTR'ı
  istatistiksel olarak artıran unsurları (merak açığı başlıklar,
  yüksek kontrast kapaklar, güçlü hook) otomatikleştiriyor — bu bir
  garanti değil, olasılığı iyileştiren bir sistem.
- **"Mükemmel niş" diye bir şey de yok.** Seçtiğim niş kanıtlanmış
  formatları birleştiren, rekabeti düşük, mantıklı bir başlangıç —
  ama piyasa tepkisini gerçek videolarla görmeden kimse önceden
  "bu kesin patlar" diyemez.
- **Script'i de otomatikleştirdim** ama `generate_script.py` içine bir
  kalite kontrol döngüsü (AI kendi kendini eleştirip düşükse yeniden
  yazıyor) ekledim. Bu bir güvenlik ağı, insan gözünün yerini tam
  tutmuyor. YouTube'un 2026 "özgün olmayan içerik" politikası şablon/
  kitlesel üretime karşı sıkı — ilk birkaç haftanın çıktısını mutlaka
  izle, otomasyona güvendikçe kontrolü azalt.

## İki workflow seçeneğin var

- **`weekly-research.yml` + `produce-video.yml`**: script'i sen yazarsın
  (issue üzerinden), kalan her şey otomatik. Daha güvenli, daha yavaş.
  ⚠️ Bu ikisi hâlâ eski Gemini/ElevenLabs kurulumunu referans alıyor,
  Wiro'ya geçmek istersen `produce-video-full-auto.yml`'deki adımları
  örnek alarak güncellemen gerekir.
- **`produce-video-full-auto.yml`**: baştan sona hiç dokunmadan çalışır
  (haftada 2 kez otomatik tetiklenir), Wiro API ile güncel. Daha hızlı
  büyüme potansiyeli ama kalite riski daha yüksek. Sadece birini aktif
  tut, ikisini aynı anda çalıştırma.

## Tam otomatik akış (`produce-video-full-auto.yml`)

1. **Araştırma** — Claude, niş tanımına göre az bilinen gerçekleri bulur
2. **Script + kalite kontrolü** — taslak yazılır, eleştirilir, gerekirse
   yeniden yazılır (en fazla 2 tur)
3. **Başlık** — 8 aday üretilir, CTR kriterlerine göre en iyisi seçilir
4. **Seslendirme** — klonlanmış sesle, doğal (robotik olmayan) ayarlarla,
   kelime bazlı gerçek zaman damgalarıyla birlikte
5. **Arka plan müziği** — sidechain ducking ile sesin altına, konuşmayı
   bastırmadan mikslenir
6. **Sahne görselleri** — script'e göre otomatik üretilir
7. **Kapak (thumbnail)** — yüksek kontrast görsel + kalın metin bindirme
8. **Render** — ffmpeg ile birleştirilir
9. **Altyazı** — gerçek zaman damgalarından üretilir ve gömülür, kayma
   olmaz (tahmini süre değil, sesin kendi ürettiği zamanlama kullanılır)

## Kurulum

1. Repoyu GitHub'a yükle (Actions dakikaları için **public** repo öner,
   private repo'da ayda sadece 2.000 dakika ücretsiz).
2. Repo → Settings → Secrets and variables → Actions altına ekle:
   - `ANTHROPIC_API_KEY`
   - `WIRO_API_KEY` (görsel üretimi + seslendirme, tek anahtar)
   - `YOUTUBE_API_KEY` (trend analizi)
   - (opsiyonel) `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` — otomatik YouTube
     yüklemesi istersen
3. `assets/music/background.mp3` — kendi telifsiz/lisanslı arka plan
   müziğini buraya koy (Content ID sorunu yaşamamak için önemli).
4. `assets/fonts/Anton-Regular.ttf` — kapak metni için kalın bir font
   ekle (Google Fonts'tan ücretsiz indirilebilir).
5. Actions sekmesinden istediğin workflow'u manuel tetikleyip
   (`workflow_dispatch`) küçük bir test yap, sonucu izle.

## Wiro AI entegrasyonu (yeni — Gemini/ElevenLabs yerine)

Görsel üretimi (sahne + kapak) ve seslendirme artık **tek bir Wiro API
anahtarıyla** çalışıyor (`WIRO_API_KEY` secret). `scripts/wiro_client.py`
ortak istemci — auth, çalıştırma, sonuç indirme hepsi orada.

- **Görsel üretimi:** `reve/generate` modeli (`generate_scenes.py`,
  `generate_thumbnail.py`)
- **Seslendirme:** `wiro/voice-clone` modeli, 6 saniyelik ses
  örneğinle klonlama (`tts.py`) — `assets/voice/reference.wav` olarak
  kendi ses örneğini koy.

⚠️ **Bilmen gereken teknik fark:** ElevenLabs kelime bazlı zaman
damgası (timestamp) döndürüyordu, Wiro'nun bu modeli döndürmüyor. Bu
yüzden `align_subtitles.py` adımını ekledim — üretilen sesi yerel,
ücretsiz Whisper modeliyle yeniden dinleyip kelime zamanlarını kendi
çıkarıyoruz. Bu aslında daha esnek bir çözüm: hangi TTS sağlayıcısını
kullanırsan kullan (Wiro, ElevenLabs, başka biri) altyazı senkronu
aynı şekilde çalışır.

⚠️ **Doğrulaman gereken yer:** `tts.py` içindeki `reference_audio`
parametre adı tahmini — Wiro panelinde `wiro/voice-clone` modelinin
`POST /Tool/Detail` şemasına bakıp doğrula, model güncellemesiyle
parametre adı değişmiş olabilir.

## Kurulum (secrets)

- `ANTHROPIC_API_KEY`
- `WIRO_API_KEY`
- `YOUTUBE_API_KEY` (trend analizi için)
- (opsiyonel) `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` — otomatik YouTube yüklemesi

`assets/voice/reference.wav`, `assets/music/background.mp3`,
`assets/fonts/Anton-Regular.ttf` dosyalarını kendin eklemen gerekiyor.



- **`trend_analysis.py`** — YouTube Data API ile nişe yakın, son 60
  günde yüksek izlenen videoları çeker, başlık kalıplarını
  `generate_script.py`'ye referans olarak besler. Bunun için
  `YOUTUBE_API_KEY` secret'ı eklemen gerekir (Google Cloud Console'dan
  ücretsiz alınır, günlük kota var — bu scripti günde bir kereden
  fazla çalıştırmamaya dikkat et).
- **`generate_titles.py` ve `generate_thumbnail.py` artık 3 varyant
  üretiyor**, tek değil. Ama bu 3 varyantı hangisinin kazanacağına biz
  karar vermiyoruz — **YouTube Studio'nun native "A/B Testing" (Test &
  Compare) özelliğine** elle yüklüyorsun. YouTube gerçek izleyicilere
  farklı varyantı gösterip izlenme süresi payına göre kazananı kendi
  seçiyor. Bu, bizim kendi tahminimizden çok daha güvenilir çünkü
  gerçek izleyici davranışına dayanıyor. Bu özellik şu an sadece masaüstü
  Studio'da ve "gelişmiş özellikler" açık hesaplarda kullanılabiliyor,
  API üzerinden otomatik kurulamıyor — video yüklendikten sonra Studio
  > İçerik > video > A/B Testing bölümüne 5 dakikalık elle bir adım.

## Gerçekçi beklenti (tekrar hatırlatma)

Trend analizi ve A/B test, izlenme *olasılığını* artıran veriye dayalı
adımlar — "kesin izlenir" garantisi hâlâ yok. İlk videoların 1 ay
izlenmeyip 3. ayda patlaması senaryosu mümkün, ama bunun tersi de
mümkün (hiç patlamaması da). Otomasyonu kurup "kendi haline bırakmak"
riskli olabilir çünkü kalite kontrolü olmadan hatalı/düşük kaliteli
videolar birikirse kanal güvenilirliği ve YouTube'un algoritma
değerlendirmesi zarar görebilir — en azından ilk 4-6 haftada
çıktıları düzenli kontrol etmeni öneririm.

## Uyarlaman/test etmen gereken yerler

- `scripts/generate_thumbnail.py` — font yolu placeholder, kendi
  fontunu eklemen lazım.
- `scripts/tts.py` — `VOICE_SETTINGS` değerleri kendi klonladığın sese
  göre ince ayar ister; bir ses "absürt" duyuluyorsa önce `stability`
  ve `style` değerleriyle oynayarak test et.
- `scripts/generate_scenes.py` içindeki sahne bölme mantığı basit
  (paragraf = sahne); daha akıllı bölme için genişletilebilir.
- Maliyet: uzun bir video onlarca sahne görseli + uzun TTS metni +
  birden fazla Claude çağrısı demek — API maliyetini önce küçük bir
  testte ölç, sonra ölçekle.
