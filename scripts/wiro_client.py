"""
Wiro AI için ortak, tek dosyalık istemci. Gemini/ElevenLabs yerine
görsel üretimi, kapak üretimi ve seslendirme için bunu kullanıyoruz -
tek API anahtarı, tek fatura.

Auth: "API Key Only" (basit) yöntemi kullanılıyor - sadece x-api-key
header'ı yeterli.

GERÇEK API DAVRANIŞI (JSON şemadan doğrulandı, ilk versiyonumdaki
"wait" parametresi YANLIŞTI, API'de öyle bir şey yok):
  1. POST /Run/{owner}/{model} -> SADECE görev oluşturur, hemen
     {"result": true/false, "taskid": "...", "errors": [...]} döner.
     Bu "result" bir BAŞARI BAYRAĞI (bool), çıktı nesnesi değil.
  2. Gerçek çıktıyı almak için POST /Task/Detail ile taskid'i
     durumu "task_postprocess_end" olana kadar POLL etmek gerekiyor.
  3. Bitince tasklist[0]["outputs"] içinde dosya URL'leri olur.

GÜVENİLİRLİK NOTU: poll_task artık polling sırasında geçici ağ
hatalarını (requests.RequestException) tek seferlik hata sayıp
görevi başarısız İLAN ETMİYOR - bu istekleri de kendi içinde
yeniden deniyor. Böylece Wiro'nun API'sinde anlık bir "hıçkırık"
olması, aslında görevin arka planda tamamlanmakta olduğu bir
durumda bile scripti gereksiz yere çökertmiyor.
"""
import os
import time

import requests

BASE_URL = "https://api.wiro.ai/v1"

# Polling sırasında geçici ağ hatasına (timeout, bağlantı kopması vb.)
# karşı kaç kez art arda deneneceği - bu ayrı bir sayaç, max_wait'in
# içinde erir, poll_task'ı bitirmez.
POLL_ERROR_RETRY_LIMIT = 5


def _headers():
    return {
        "Content-Type": "application/json",
        "x-api-key": os.environ["WIRO_API_KEY"],
    }


def run_model(owner: str, model: str, params: dict, poll_interval: int = 3, max_wait: int = 420) -> dict:
    resp = requests.post(
        f"{BASE_URL}/Run/{owner}/{model}",
        headers=_headers(),
        json=params,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("result") or data.get("errors"):
        raise RuntimeError(f"Wiro görev oluşturma başarısız: {data.get('errors')}")

    taskid = data["taskid"]
    return poll_task(taskid, poll_interval=poll_interval, max_wait=max_wait)


def poll_task(taskid: str, poll_interval: int = 3, max_wait: int = 420) -> dict:
    terminal_statuses = {"task_postprocess_end", "task_cancel"}
    elapsed = 0
    consecutive_poll_errors = 0

    while elapsed < max_wait:
        try:
            resp = requests.post(
                f"{BASE_URL}/Task/Detail",
                headers=_headers(),
                json={"taskid": taskid},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            consecutive_poll_errors = 0  # başarılı istek, sayaç sıfırlanır
        except requests.RequestException as e:
            consecutive_poll_errors += 1
            if consecutive_poll_errors > POLL_ERROR_RETRY_LIMIT:
                raise RuntimeError(
                    f"Task {taskid} durumu {POLL_ERROR_RETRY_LIMIT} kez üst "
                    f"üste sorgulanamadı ({type(e).__name__}), vazgeçiliyor."
                )
            print(f"  UYARI: poll isteği başarısız ({type(e).__name__}), "
                  f"tekrar denenecek ({consecutive_poll_errors}/{POLL_ERROR_RETRY_LIMIT})")
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue

        tasklist = data.get("tasklist", [])
        if not tasklist:
            raise RuntimeError(f"Task {taskid} bulunamadı: {data}")

        task = tasklist[0]
        status = task.get("status")

        if status in terminal_statuses:
            pexit = task.get("pexit")
            if status == "task_cancel" or (pexit is not None and str(pexit) != "0"):
                debug = task.get("debugoutput", "")
                errors = data.get("errors", [])
                raise RuntimeError(
                    f"Wiro görevi başarısız (status={status}, pexit={pexit})\n"
                    f"errors: {errors}\n"
                    f"debugoutput: {debug[:2000] if debug else '(boş)'}"
                )
            return task

        time.sleep(poll_interval)
        elapsed += poll_interval

    raise TimeoutError(f"Task {taskid} {max_wait} saniyede tamamlanmadı")


def download_output(task: dict, out_path: str, index: int = 0):
    outputs = task.get("outputs", [])
    if not outputs:
        raise ValueError("Bu görevde indirilecek çıktı yok")
    url = outputs[index]["url"] if isinstance(outputs[index], dict) else outputs[index]

    r = requests.get(url, timeout=120)
    r.raise_for_status()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(r.content)
