"""
Ucuz "sahte" lip-sync: statik anime avatarın ses şiddetine göre ağız
karesini değiştirip, periyodik göz kırpma ekleyip, hafif bir bob
(yukarı-aşağı salınım) animasyonuyla videonun köşesine şeffaf overlay
olarak bindirir.

Gerçek lip-sync API'si (Hedra, Sync Labs vb.) kullanmıyor - ekstra API
maliyeti YOK, GPU gerekmez, sadece ffmpeg + Python stdlib.

Beklenen klasör yapısı (bir kere, elle/Wiro ile üretilip repoya commitlenir):
    assets/avatar/closed.png        -> ağız kapalı
    assets/avatar/half.png          -> ağız yarı açık
    assets/avatar/open.png          -> ağız açık
    assets/avatar/closed_blink.png  -> göz kırpma + ağız kapalı
    assets/avatar/open_blink.png    -> göz kırpma + ağız açık
Hepsi aynı karakter, aynı boyut, aynı konumda olmalı (sadece ağız/göz farklı).
NOT: "half" durumunda göz kırpma anı, closed_blink ile yaklaşık gösterilir
(6. bir kare üretmeye gerek kalmadan, fark görsel olarak ihmal edilebilir).

Kullanım:
    python scripts/avatar_overlay.py \
        --video output/video.mp4 \
        --audio audio/final_mix.mp3 \
        --frames-dir assets/avatar \
        --out output/video_with_avatar.mp4

Opsiyonel:
    --position bottom-right|bottom-left|top-right|top-left  (varsayılan: top-right)
    --height-px 360      (avatarın ekrandaki yüksekliği, piksel)
    --margin-px 24       (kenardan boşluk)
    --window-ms 100      (ses analiz pencere uzunluğu, ms)
    --bob-amplitude-px 8 (bob salınımının genliği, piksel)
    --bob-period-s 2.5   (bir tam salınımın süresi, saniye)
"""
import argparse
import audioop
import os
import random
import subprocess
import sys
import tempfile
import wave

WINDOW_MS_DEFAULT = 100
BLINK_MIN_INTERVAL_S = 3.0
BLINK_MAX_INTERVAL_S = 6.0
BLINK_DURATION_S = 0.15


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("KOMUT BAŞARISIZ:", " ".join(cmd))
        print(result.stderr[-3000:])
        sys.exit(1)
    return result


def audio_to_wav(audio_path, wav_path, sample_rate=16000):
    run([
        "ffmpeg", "-y", "-i", audio_path,
        "-ar", str(sample_rate), "-ac", "1",
        wav_path,
    ])


def compute_rms_windows(wav_path, window_ms):
    with wave.open(wav_path, "rb") as wf:
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        n_frames = wf.getnframes()
        window_size = int(sample_rate * window_ms / 1000)
        rms_values = []
        while True:
            chunk = wf.readframes(window_size)
            if not chunk:
                break
            rms = audioop.rms(chunk, sample_width)
            rms_values.append(rms)
    duration = n_frames / sample_rate
    return rms_values, duration


def classify_mouth_states(rms_values):
    """RMS değerlerini closed/half/open durumlarına eşler. Eşikler, o
    sesin kendi dağılımına göre relatif olarak hesaplanır (her videonun
    ses seviyesi farklı olabileceği için sabit sayı yerine percentile
    kullanmak daha güvenli)."""
    non_silent = sorted(v for v in rms_values if v > 50)  # tam sessizlik gürültüsünü ele
    if not non_silent:
        return ["closed"] * len(rms_values)

    def percentile(data, p):
        idx = int(len(data) * p)
        idx = min(idx, len(data) - 1)
        return data[idx]

    low_thresh = percentile(non_silent, 0.35)
    high_thresh = percentile(non_silent, 0.75)

    states = []
    for v in rms_values:
        if v <= 50:
            states.append("closed")
        elif v <= low_thresh:
            states.append("half")
        elif v <= high_thresh:
            states.append("half")
        else:
            states.append("open")
    return states


def apply_blinks(mouth_states, window_ms):
    """Mevcut ağız durumlarının üzerine, sesle ilgisiz, periyodik göz
    kırpma anları bindirir. "half" durumundayken göz kırparsa
    closed_blink ile temsil edilir (yaklaşık, görsel olarak fark
    edilmez)."""
    window_s = window_ms / 1000.0
    total_duration = len(mouth_states) * window_s

    blink_starts = []
    t = random.uniform(BLINK_MIN_INTERVAL_S, BLINK_MAX_INTERVAL_S)
    while t < total_duration:
        blink_starts.append(t)
        t += random.uniform(BLINK_MIN_INTERVAL_S, BLINK_MAX_INTERVAL_S)

    final_states = list(mouth_states)
    for start in blink_starts:
        start_idx = int(start / window_s)
        end_idx = int((start + BLINK_DURATION_S) / window_s)
        for i in range(start_idx, min(end_idx + 1, len(final_states))):
            base = mouth_states[i]
            final_states[i] = "open_blink" if base == "open" else "closed_blink"

    return final_states


def states_to_concat_entries(states, window_ms):
    """Ardışık aynı durumları birleştirip ffmpeg concat için (dosya, süre)
    listesi üretir - gereksiz yere binlerce satır olmasın diye."""
    entries = []
    window_s = window_ms / 1000.0
    current_state = states[0]
    current_duration = window_s
    for state in states[1:]:
        if state == current_state:
            current_duration += window_s
        else:
            entries.append((current_state, current_duration))
            current_state = state
            current_duration = window_s
    entries.append((current_state, current_duration))
    return entries


def build_concat_file(entries, frames_dir, concat_path):
    frame_files = {
        "closed": os.path.abspath(os.path.join(frames_dir, "closed.png")),
        "half": os.path.abspath(os.path.join(frames_dir, "half.png")),
        "open": os.path.abspath(os.path.join(frames_dir, "open.png")),
        "closed_blink": os.path.abspath(os.path.join(frames_dir, "closed_blink.png")),
        "open_blink": os.path.abspath(os.path.join(frames_dir, "open_blink.png")),
    }
    for state, path in frame_files.items():
        if not os.path.exists(path):
            print(f"HATA: bulunamadı -> {path}")
            sys.exit(1)

    with open(concat_path, "w", encoding="utf-8") as f:
        for state, duration in entries:
            f.write(f"file '{frame_files[state]}'\n")
            f.write(f"duration {duration:.3f}\n")
        # ffmpeg concat quirk: son dosya duration olmadan bir kez daha yazılmalı
        f.write(f"file '{frame_files[entries[-1][0]]}'\n")


def build_avatar_overlay_video(concat_path, out_path, fps=25):
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_path,
        "-vf", f"fps={fps}",
        "-pix_fmt", "yuva420p",
        "-c:v", "qtrle",
        out_path,
    ])


POSITION_EXPR = {
    "bottom-right": ("W-w-{m}", "H-h-{m}"),
    "bottom-left": ("{m}", "H-h-{m}"),
    "top-right": ("W-w-{m}", "{m}"),
    "top-left": ("{m}", "{m}"),
}


def composite_final_video(video_path, avatar_video_path, out_path,
                           position, height_px, margin_px,
                           bob_amplitude_px, bob_period_s):
    x_expr, y_expr = POSITION_EXPR[position]
    x_expr = x_expr.format(m=margin_px)
    y_expr = y_expr.format(m=margin_px)

    # Hafif bob: y konumuna sinüs dalgasıyla küçük bir salınım ekler,
    # avatarın tamamen "yapıştırılmış" durmasını önler. eval=frame
    # olmadan overlay ifadesi sadece BİR kez hesaplanır, bu yüzden
    # zorunlu.
    bob_expr = f"{bob_amplitude_px}*sin(2*PI*t/{bob_period_s})"
    y_expr_with_bob = f"({y_expr})+({bob_expr})"

    filter_complex = (
        f"[1:v]scale=-1:{height_px}[ava];"
        f"[0:v][ava]overlay=x={x_expr}:y={y_expr_with_bob}:"
        f"eval=frame:shortest=1[out]"
    )
    run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", avatar_video_path,
        "-filter_complex", filter_complex,
        "-map", "[out]", "-map", "0:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        out_path,
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--position", default="top-right",
                         choices=list(POSITION_EXPR.keys()))
    parser.add_argument("--height-px", type=int, default=360)
    parser.add_argument("--margin-px", type=int, default=24)
    parser.add_argument("--window-ms", type=int, default=WINDOW_MS_DEFAULT)
    parser.add_argument("--bob-amplitude-px", type=int, default=8)
    parser.add_argument("--bob-period-s", type=float, default=2.5)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "audio16k.wav")
        concat_path = os.path.join(tmp, "concat.txt")
        avatar_video_path = os.path.join(tmp, "avatar_overlay.mov")

        print("1) Ses WAV'a çevriliyor...")
        audio_to_wav(args.audio, wav_path)

        print("2) RMS pencereleri hesaplanıyor...")
        rms_values, duration = compute_rms_windows(wav_path, args.window_ms)
        print(f"   Ses süresi: {duration:.1f}sn, {len(rms_values)} pencere")

        print("3) Ağız durumları sınıflandırılıyor...")
        mouth_states = classify_mouth_states(rms_values)

        print("4) Göz kırpma anları ekleniyor...")
        states = apply_blinks(mouth_states, args.window_ms)
        blink_count = sum(1 for s in states if s.endswith("_blink"))
        print(f"   {blink_count} pencerede göz kırpma uygulandı")

        print("5) Concat listesi oluşturuluyor...")
        entries = states_to_concat_entries(states, args.window_ms)
        print(f"   {len(entries)} ardışık segment")
        build_concat_file(entries, args.frames_dir, concat_path)

        print("6) Şeffaf avatar overlay videosu üretiliyor...")
        build_avatar_overlay_video(concat_path, avatar_video_path)

        print("7) Ana video ile birleştiriliyor (bob animasyonu dahil)...")
        composite_final_video(
            args.video, avatar_video_path, args.out,
            args.position, args.height_px, args.margin_px,
            args.bob_amplitude_px, args.bob_period_s,
        )

    print(f"Bitti -> {args.out}")


if __name__ == "__main__":
    main()
