# İHA Gözcü — Android (Chaquopy)

Raspberry Pi üzerinde çalışan renk tanıma algoritmasını **Python kodunu yeniden yazmadan**
Android telefonda gerçek zamanlı çalıştırır.

```
CameraX (RGBA_8888)
   ↓  ByteArray (yeniden kullanılan tampon, Bitmap yok)
PythonBridge  →  Chaquopy  →  vision.py
   ↓                              ↓  OpenCV
DetectionResult  ←────────────────┘
   ↓
VisionViewModel (StateFlow)
   ↓
OverlayView (Canvas) + Material debug paneli
```

## Kurulum

1. Android Studio ile bu klasörü açın (`AndroidRenkAlgilama`).
2. `local.properties` içindeki `sdk.dir` kendi SDK yolunuzu göstersin.
3. `Run` — ilk derleme OpenCV tekerleklerini indirdiği için birkaç dakika sürer.

Komut satırından:

```bash
./gradlew :app:assembleDebug
./gradlew :app:installDebug   # cihaz bağlıyken
```

## Önemli sürüm kısıtı

Chaquopy paket deposunda OpenCV yalnızca **cp38** ve **cp310** için derlenmiş tekerlek
(wheel) olarak bulunuyor. Bu yüzden:

| Ayar | Değer | Neden |
|---|---|---|
| `chaquopy.defaultConfig.version` | `3.10` | OpenCV tekerleği yalnızca cp38/cp310 |
| `opencv-python-headless` | `4.5.1.48` | Depodaki en yeni Android tekerleği |
| `numpy` | `1.26.2` | cp310 Android tekerleği mevcut |
| `minSdk` | `24` | OpenCV cp310 tekerlekleri `android_24` |
| ABI | `arm64-v8a`, `x86_64` | Cihaz + emülatör |

**Sürümleri sabit bırakın.** Sabitlenmezse pip, Android tekerleği olmayan yeni bir sürüm
seçer ve kaynaktan derlemeye çalışıp derlemeyi kırar.

## Python tarafı

`app/src/main/python/vision.py`

Raspberry Pi kodundan **aynen** taşınan fonksiyonlar:

- `aydinlatma_dengele(frame)` — CLAHE ile LAB uzayında aydınlatma dengeleme
- `renk_maskeleri(hsv)` — KIRMIZI / YESIL / SIYAH HSV maskeleri
- `hedef_bul(hsv, ks, kb)` — morfoloji + kontur + alan/en-boy filtresi

Kaldırılan Raspberry Pi'ye özel bölümler: `Picamera2`, `cv2.VideoWriter`, `while True`
ana döngüsü, `signal`, `pymavlink`, dosyaya video kaydı, `cv2.putText` çizimleri.

Eklenen tek şey iki giriş noktasıdır:

- `process_frame(frame)` — BGR NumPy karesi alır, istenen sözlüğü döndürür
- `process_rgba(buf, w, h, row_stride, rotation)` — CameraX ham tamponunu BGR'ye
  çevirip `process_frame`'e verir (ara Bitmap oluşturulmaz)

```python
{"detected": True, "color": "KIRMIZI", "x": 120, "y": 85, "width": 140, "height": 130}
{"detected": False}
```

Eşikleri değiştirmek için yalnızca `vision.py` başındaki AYARLAR bloğunu düzenleyin;
Kotlin tarafına dokunmanız gerekmez.

## Kotlin tarafı

| Dosya | Görevi |
|---|---|
| `MainActivity.kt` | İzin, CameraX bağlama, durum → ekran |
| `VisionViewModel.kt` | `StateFlow<VisionUiState>`, Python ömrü |
| `CameraAnalyzer.kt` | `ImageProxy` → tampon → Python, FPS/süre ölçümü |
| `PythonBridge.kt` | Tekil Python yorumlayıcısı, `vision.process_rgba` referansı |
| `OverlayView.kt` | Kutu + etiket çizimi (FILL_CENTER hizalaması) |
| `Result.kt` | `DetectionResult`, `FrameStats`, `VisionUiState` |

## Performans notları

- Kare `OUTPUT_IMAGE_FORMAT_RGBA_8888` olarak alınır → YUV dönüşümü ve `Bitmap` yok.
- RGBA tamponu bir kez ayrılır, her karede yeniden kullanılır → GC baskısı yok.
- Python yorumlayıcısı ve `vision` modülü uygulama ömrü boyunca bir kez açılır.
- `createCLAHE` ve morfoloji çekirdekleri modül seviyesinde bir kez oluşturulur.
- Hedef 15 FPS; fazlası analiz katmanında işlenmeden atılır.
- `STRATEGY_KEEP_ONLY_LATEST` → gecikme birikmez, kare atlanır.
