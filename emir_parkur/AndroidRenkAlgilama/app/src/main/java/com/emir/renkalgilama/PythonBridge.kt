package com.emir.renkalgilama

import android.content.Context
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * Kotlin <-> Python (vision.py) arasındaki tek köprü.
 *
 * Yorumlayıcı uygulama ömrü boyunca **bir kez** başlatılır; `vision` modülü ve
 * `process_rgba` fonksiyon referansı alan olarak saklanır, böylece her karede
 * modül arama / attribute çözümleme maliyeti ödenmez.
 *
 * [getInstance] ilk çağrıda birkaç yüz milisaniye sürebilir (Python açılışı),
 * bu yüzden arka plan iş parçacığından çağrılmalıdır.
 */
class PythonBridge private constructor(visionModule: PyObject) {

    /** vision.process_rgba fonksiyonuna doğrudan referans. */
    private val processRgba: PyObject = visionModule["process_rgba"]
        ?: error("vision.py içinde process_rgba bulunamadı")

    /**
     * Ham RGBA_8888 karesini Python'a gönderir ve sonucu döndürür.
     *
     * @param rgba CameraX düzleminden kopyalanmış, yeniden kullanılan tampon
     * @param width kaynak kare genişliği
     * @param height kaynak kare yüksekliği
     * @param rowStride satır adımı (bayt)
     * @param rotation kareyi dik konuma getirmek için 0/90/180/270
     */
    fun process(
        rgba: ByteArray,
        width: Int,
        height: Int,
        rowStride: Int,
        rotation: Int
    ): DetectionResult {
        val result = processRgba.call(rgba, width, height, rowStride, rotation)
        return result.toDetectionResult()
    }

    /** Python sözlüğünü [DetectionResult] nesnesine çevirir. */
    private fun PyObject.toDetectionResult(): DetectionResult {
        val detected = callAttr("get", KEY_DETECTED)?.toBoolean() ?: false
        if (!detected) return DetectionResult.NONE

        return DetectionResult(
            detected = true,
            color = callAttr("get", KEY_COLOR)?.toString().orEmpty(),
            x = callAttr("get", KEY_X)?.toInt() ?: 0,
            y = callAttr("get", KEY_Y)?.toInt() ?: 0,
            width = callAttr("get", KEY_WIDTH)?.toInt() ?: 0,
            height = callAttr("get", KEY_HEIGHT)?.toInt() ?: 0
        )
    }

    companion object {
        private const val KEY_DETECTED = "detected"
        private const val KEY_COLOR = "color"
        private const val KEY_X = "x"
        private const val KEY_Y = "y"
        private const val KEY_WIDTH = "width"
        private const val KEY_HEIGHT = "height"

        @Volatile
        private var instance: PythonBridge? = null

        /** Çift kontrollü kilit: Python yalnızca ilk çağrıda başlatılır. */
        fun getInstance(context: Context): PythonBridge =
            instance ?: synchronized(this) {
                instance ?: create(context).also { instance = it }
            }

        private fun create(context: Context): PythonBridge {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(context.applicationContext))
            }
            return PythonBridge(Python.getInstance().getModule("vision"))
        }
    }
}
