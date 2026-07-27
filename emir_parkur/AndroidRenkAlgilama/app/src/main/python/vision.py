"""İHA Gözcü - Android (Chaquopy) sürümü.

Raspberry Pi kodundaki görüntü işleme algoritması AYNEN korunmuştur.
Kaldırılan Raspberry Pi'ye özel bölümler:
  - Picamera2 / Kamera sınıfı   -> kare Android CameraX tarafından geliyor
  - cv2.VideoWriter             -> video kaydı yok
  - while True ana döngüsü      -> her kare için process_frame() çağrılıyor
  - signal / SIGTERM handler    -> Android yaşam döngüsü yönetiyor
  - pymavlink bağlantısı        -> telemetri yok
  - log_yaz / cv2.putText çizimi -> çizim Kotlin Canvas tarafında

Değiştirilmeyen fonksiyonlar:
  aydinlatma_dengele(frame)
  renk_maskeleri(hsv)
  hedef_bul(hsv, ks, kb)
"""

import cv2
import numpy as np

# ============================ AYARLAR ============================
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

# --- Tespit ---
MIN_ALAN = 500
MAX_ALAN_ORANI = 0.70
EN_BOY_MIN, EN_BOY_MAX = 0.3, 3.3
USE_CLAHE = True

# Karanlık kare eşiği (Pi kodundaki `np.mean(frame) >= 15.0` kontrolü)
MIN_PARLAKLIK = 15.0
# ================================================================

CIZIM_RENKLERI = {"KIRMIZI": (0, 0, 255), "YESIL": (0, 255, 0), "SIYAH": (0, 255, 255)}
_clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))

# Morfoloji çekirdekleri bir kez oluşturulur ve her karede yeniden kullanılır.
# (Pi kodunda main() içinde bir kez oluşturuluyordu.)
_KS = np.ones((3, 3), np.uint8)
_KB = np.ones((7, 7), np.uint8)

# Tespit yokken tekrar tekrar sözlük ayırmamak için sabit sonuç.
_BULUNAMADI = {"detected": False}


# ======================= ALGORİTMA (DEĞİŞMEDİ) =======================

def aydinlatma_dengele(frame):
    if not USE_CLAHE:
        return frame
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    return cv2.cvtColor(cv2.merge((_clahe.apply(l), a, b)), cv2.COLOR_LAB2BGR)


def renk_maskeleri(hsv):
    mask_red = cv2.bitwise_or(
        cv2.inRange(hsv, np.array([0, 140, 50]),   np.array([10, 255, 255])),
        cv2.inRange(hsv, np.array([170, 140, 50]), np.array([180, 255, 255])),
    )
    mask_green = cv2.inRange(hsv, np.array([45, 100, 40]), np.array([80, 255, 255]))
    mask_black = cv2.inRange(hsv, np.array([0, 0, 0]),   np.array([180, 60, 50]))
    return {"KIRMIZI": mask_red, "YESIL": mask_green, "SIYAH": mask_black}


def hedef_bul(hsv, ks, kb):
    tespit, max_alan, box = None, 0, None
    ust = FRAME_WIDTH * FRAME_HEIGHT * MAX_ALAN_ORANI
    for renk_adi, maske in renk_maskeleri(hsv).items():
        maske = cv2.morphologyEx(maske, cv2.MORPH_OPEN, ks, iterations=1)
        maske = cv2.morphologyEx(maske, cv2.MORPH_CLOSE, kb, iterations=2)
        contours, _ = cv2.findContours(maske, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            alan = cv2.contourArea(cnt)
            if not (MIN_ALAN < alan < ust) or alan <= max_alan:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            oran = float(w) / float(h) if h > 0 else 0
            if EN_BOY_MIN <= oran <= EN_BOY_MAX:
                max_alan, tespit, box = alan, renk_adi, (x, y, w, h)
    return tespit, box


# ======================= ANDROID GİRİŞ NOKTASI =======================

def process_frame(frame):
    """Tek bir BGR NumPy karesini işler.

    Pi kodundaki ana döngünün tespit bloğunun birebir karşılığıdır.

    Dönüş:
        {"detected": True, "color": "KIRMIZI", "x": .., "y": .., "width": .., "height": ..}
        veya {"detected": False}
    """
    if frame is None or frame.size == 0:
        return _BULUNAMADI

    if np.mean(frame) < MIN_PARLAKLIK:
        return _BULUNAMADI

    # Kenarları korumak için Gaussian Blur yerine Median Blur kullanıldı
    islenmis = cv2.medianBlur(aydinlatma_dengele(frame), 5)
    hsv = cv2.cvtColor(islenmis, cv2.COLOR_BGR2HSV)
    tespit, box = hedef_bul(hsv, _KS, _KB)

    if tespit is None or box is None:
        return _BULUNAMADI

    x, y, w, h = box
    return {
        "detected": True,
        "color": tespit,
        "x": int(x),
        "y": int(y),
        "width": int(w),
        "height": int(h),
    }


def process_rgba(buf, width, height, row_stride, rotation):
    """CameraX'ten gelen ham RGBA_8888 tamponunu BGR'ye çevirip process_frame'e verir.

    Bitmap oluşturulmaz; tampon doğrudan NumPy görünümü olarak okunur (kopyasız).

    Args:
        buf: RGBA_8888 bayt dizisi (Kotlin ByteArray -> Python bytes)
        width, height: kare boyutları
        row_stride: satır adımı (bayt); genelde width*4'ten büyük olabilir
        rotation: 0/90/180/270 - kareyi dik konuma getirmek için

    Dönüş: process_frame() ile aynı sözlük.
    """
    arr = np.frombuffer(buf, dtype=np.uint8)
    arr = arr.reshape(height, row_stride // 4, 4)[:, :width, :]

    frame = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

    if rotation == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rotation == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

    return process_frame(frame)
