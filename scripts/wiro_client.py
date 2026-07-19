"""
Wiro AI için ortak, tek dosyalık istemci. Gemini/ElevenLabs yerine
görsel üretimi, kapak üretimi ve seslendirme için bunu kullanıyoruz —
tek API anahtarı, tek fatura.

Auth: "API Key Only" (basit) yöntemi kullanılıyor — sadece x-api-key
header'ı yeterli. Daha güvenli imza-bazlı (HMAC) yöntem de var ama
sunucu tarafı (GitHub Actions) kullanımında API-key-only pratikte
yeterli ve daha basit.

Not: Her Wiro modelinin kendi parametre isimleri var (örn. "prompt",
"text", "reference_audio" gibi farklılık gösterebilir). Yeni bir model
kullanmadan önce https://wiro.ai/docs adresinden veya panelden
POST /Tool/Detail ile o modelin tam parametre şemasına bakıp
MODEL isimlerini/parametrelerini bu dosyada doğrula.
"""
import os
import time

import requests

BASE_URL = "https://api.wiro.ai/v1"


def _headers():
    return {
        "Content-Type": "application/json",
        "x-api-key": os.environ["WIRO_API_KEY"],
    }


def run_model(owner: str, model: str, params: dict, wait: bool = True, timeout: int = 180) -> dict:
    """
    owner/model örn: "reve", "generate" -> POST /v1/Run/reve/generate
    wait=True ise Wiro sonucu kendi tarafında bekleyip döndürür (basit
    kullanım için önerilir). Uzun süren video işlerinde wait=False
    kullanıp poll_task ile takip etmek daha güvenli olabilir.
    """
    payload = dict(params)
    payload["wait"] = wait

    resp = requests.post(
        f"{BASE_URL}/Run/{owner}/{model}",
        headers=_headers(),
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    if not wait:
        return data  # taskid/tasktoken içerir, poll_task ile takip et

    return _extract_result(data)


def poll_task(tasktoken: str, poll_interval: int = 3, max_wait: int = 300) -> dict:
    elapsed = 0
    while elapsed < max_wait:
        resp = requests.post(
            f"{BASE_URL}/Task/Detail",
            headers=_headers(),
            json={"tasktoken": tasktoken},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("result", {}).get("pexit") is not None:
            return _extract_result(data)
        time.sleep(poll_interval)
        elapsed += poll_interval

    raise TimeoutError(f"Task {tasktoken} {max_wait} saniyede tamamlanmadı")


def _extract_result(data: dict) -> dict:
    result = data.get("result", data)
    pexit = result.get("pexit")
    if pexit is not None and str(pexit) != "0":
        raise RuntimeError(f"Wiro görevi başarısız (pexit={pexit}): {result.get('errors')}")
    return result


def download_output(result: dict, out_path: str, index: int = 0):
    """result['outputs'] içindeki CDN URL'sini indirir. Wiro CDN
    çıktıları belirli bir süre sonra silinebilir, hemen indirmek önemli."""
    outputs = result.get("outputs", [])
    if not outputs:
        raise ValueError("Bu görevde indirilecek çıktı yok")
    url = outputs[index] if isinstance(outputs[index], str) else outputs[index].get("url")

    r = requests.get(url, timeout=120)
    r.raise_for_status()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(r.content)
