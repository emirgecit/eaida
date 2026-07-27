package com.emir.renkalgilama

import android.graphics.Color
import androidx.annotation.ColorInt

/**
 * Python tarafındaki `process_frame()` sözlüğünün Kotlin karşılığı.
 *
 * ```
 * {"detected": true, "color": "KIRMIZI", "x": 120, "y": 85, "width": 140, "height": 130}
 * {"detected": false}
 * ```
 *
 * Her karede yeni nesne üretmemek için tespit yokken [NONE] tekrar kullanılır.
 */
data class DetectionResult(
    val detected: Boolean,
    val color: String = "",
    val x: Int = 0,
    val y: Int = 0,
    val width: Int = 0,
    val height: Int = 0
) {
    /** Kutunun çizileceği renk. Raspberry Pi'deki CIZIM_RENKLERI ile aynı eşleşme. */
    @get:ColorInt
    val drawColor: Int
        get() = when (color) {
            "KIRMIZI" -> Color.rgb(255, 61, 61)
            "YESIL" -> Color.rgb(61, 255, 106)
            "SIYAH" -> Color.rgb(255, 214, 61)
            else -> Color.WHITE
        }

    companion object {
        val NONE = DetectionResult(detected = false)
    }
}

/**
 * Bir karenin ölçüm bilgileri. Debug panelinde gösterilir.
 *
 * @param fps yumuşatılmış (EMA) kare hızı
 * @param processMs Python tarafındaki işlem süresi (milisaniye)
 * @param frameWidth döndürme uygulandıktan sonraki kare genişliği
 * @param frameHeight döndürme uygulandıktan sonraki kare yüksekliği
 */
data class FrameStats(
    val fps: Double = 0.0,
    val processMs: Double = 0.0,
    val frameWidth: Int = 0,
    val frameHeight: Int = 0
) {
    companion object {
        val EMPTY = FrameStats()
    }
}

/** Ekranın tek doğruluk kaynağı (MVVM state). */
data class VisionUiState(
    val pythonReady: Boolean = false,
    val detection: DetectionResult = DetectionResult.NONE,
    val stats: FrameStats = FrameStats.EMPTY,
    val errorMessage: String? = null
)
