package com.emir.renkalgilama

import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy

/**
 * CameraX karelerini Python'a taşıyan analiz katmanı.
 *
 * Performans notları:
 *  - Kare `OUTPUT_IMAGE_FORMAT_RGBA_8888` olarak alınır; YUV -> RGB dönüşümü ve
 *    ara `Bitmap` oluşturma tamamen atlanır.
 *  - Tampon ([rgbaBuffer]) bir kez ayrılır ve her karede yeniden kullanılır;
 *    böylece saniyede 15 adet ~1.2 MB'lık dizi ayırıp GC'yi tetiklemeyiz.
 *  - Hedef FPS aşılırsa kare işlenmeden atılır (CPU ve pil tasarrufu).
 *
 * Bu sınıf CameraX'in tek iş parçacıklı analiz executor'ında çalışır, bu yüzden
 * alanlarına ek senkronizasyon gerekmez.
 */
class CameraAnalyzer(
    private val bridge: PythonBridge,
    private val targetFps: Int = DEFAULT_TARGET_FPS,
    private val onResult: (DetectionResult, FrameStats) -> Unit,
    private val onError: (Throwable) -> Unit
) : ImageAnalysis.Analyzer {

    private val minFrameIntervalNs: Long = 1_000_000_000L / targetFps

    private var rgbaBuffer = ByteArray(0)
    private var lastAcceptedNs = 0L
    private var smoothedFps = 0.0

    override fun analyze(image: ImageProxy) {
        image.use { proxy ->
            val now = System.nanoTime()
            val elapsedNs = now - lastAcceptedNs
            if (lastAcceptedNs != 0L && elapsedNs < minFrameIntervalNs) {
                return@use // hedef FPS'in üstündeyiz, bu kareyi atla
            }

            try {
                updateFps(elapsedNs)
                lastAcceptedNs = now

                val plane = proxy.planes[0]
                val source = plane.buffer
                source.rewind()

                val size = source.remaining()
                if (rgbaBuffer.size != size) {
                    rgbaBuffer = ByteArray(size)
                }
                source.get(rgbaBuffer)

                val rotation = proxy.imageInfo.rotationDegrees
                val swapped = rotation == 90 || rotation == 270

                val startNs = System.nanoTime()
                val result = bridge.process(
                    rgba = rgbaBuffer,
                    width = proxy.width,
                    height = proxy.height,
                    rowStride = plane.rowStride,
                    rotation = rotation
                )
                val processMs = (System.nanoTime() - startNs) / NANOS_PER_MILLI

                val stats = FrameStats(
                    fps = smoothedFps,
                    processMs = processMs,
                    frameWidth = if (swapped) proxy.height else proxy.width,
                    frameHeight = if (swapped) proxy.width else proxy.height
                )
                onResult(result, stats)
            } catch (t: Throwable) {
                onError(t)
            }
        }
    }

    /** Üstel hareketli ortalama: panelde titremeyen bir FPS değeri gösterir. */
    private fun updateFps(elapsedNs: Long) {
        if (lastAcceptedNs == 0L || elapsedNs <= 0L) return
        val instantFps = 1_000_000_000.0 / elapsedNs
        smoothedFps =
            if (smoothedFps == 0.0) instantFps
            else smoothedFps * (1 - FPS_SMOOTHING) + instantFps * FPS_SMOOTHING
    }

    companion object {
        const val DEFAULT_TARGET_FPS = 15
        private const val FPS_SMOOTHING = 0.15
        private const val NANOS_PER_MILLI = 1_000_000.0
    }
}
