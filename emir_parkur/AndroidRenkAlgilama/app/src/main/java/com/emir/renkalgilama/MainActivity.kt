package com.emir.renkalgilama

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import android.util.Size
import android.view.WindowManager
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.emir.renkalgilama.databinding.ActivityMainBinding
import kotlinx.coroutines.launch
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Tek ekranlı görünüm katmanı.
 *
 * Sorumlulukları yalnızca: izin isteme, CameraX bağlama ve [VisionViewModel]
 * durumunu ekrana yansıtma. Görüntü işlemeye dair hiçbir mantık burada yok.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val viewModel: VisionViewModel by viewModels()

    /** Python çağrıları tek bir arka plan iş parçacığında sıralı çalışır. */
    private lateinit var analysisExecutor: ExecutorService

    private var cameraProvider: ProcessCameraProvider? = null
    private var cameraBound = false
    private var permissionGranted = false

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        permissionGranted = granted
        if (granted) {
            bindCameraIfReady()
        } else {
            binding.statusText.text = getString(R.string.camera_permission_denied)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        binding.previewView.scaleType = PreviewView.ScaleType.FILL_CENTER
        binding.previewView.implementationMode = PreviewView.ImplementationMode.PERFORMANCE

        analysisExecutor = Executors.newSingleThreadExecutor()

        observeState()
        requestCameraPermission()
    }

    private fun requestCameraPermission() {
        val alreadyGranted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.CAMERA
        ) == PackageManager.PERMISSION_GRANTED

        if (alreadyGranted) {
            permissionGranted = true
            bindCameraIfReady()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun observeState() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.uiState.collect { state -> render(state) }
            }
        }
    }

    private fun render(state: VisionUiState) {
        if (state.pythonReady) bindCameraIfReady()

        binding.overlayView.update(
            state.detection,
            state.stats.frameWidth,
            state.stats.frameHeight
        )

        binding.fpsText.text = getString(R.string.panel_fps, state.stats.fps)
        binding.latencyText.text = getString(R.string.panel_latency, state.stats.processMs)

        binding.colorText.text = if (state.detection.detected) {
            state.detection.color
        } else {
            getString(R.string.searching)
        }
        binding.colorText.setTextColor(
            if (state.detection.detected) state.detection.drawColor
            else ContextCompat.getColor(this, R.color.text_secondary)
        )

        binding.boxText.text = if (state.detection.detected) {
            String.format(
                Locale.US,
                "x=%d  y=%d  w=%d  h=%d",
                state.detection.x,
                state.detection.y,
                state.detection.width,
                state.detection.height
            )
        } else {
            getString(R.string.no_box)
        }

        binding.statusText.text = when {
            state.errorMessage != null -> state.errorMessage
            !state.pythonReady -> getString(R.string.python_starting)
            !permissionGranted -> getString(R.string.camera_permission_denied)
            else -> getString(
                R.string.frame_size,
                state.stats.frameWidth,
                state.stats.frameHeight
            )
        }
    }

    /** Hem izin hem Python hazır olduğunda, yalnızca bir kez bağlanır. */
    private fun bindCameraIfReady() {
        if (cameraBound || !permissionGranted) return
        val analyzer = viewModel.createAnalyzer() ?: return
        cameraBound = true

        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            try {
                val provider = providerFuture.get()
                cameraProvider = provider
                bindUseCases(provider, analyzer)
            } catch (t: Throwable) {
                cameraBound = false
                Log.e(TAG, "Kamera bağlanamadı", t)
                binding.statusText.text = getString(R.string.camera_error, t.message ?: "")
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun bindUseCases(provider: ProcessCameraProvider, analyzer: CameraAnalyzer) {
        // 640x480'e en yakın çözünürlük, 4:3 en-boy oranı
        val resolutionSelector = ResolutionSelector.Builder()
            .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
            .setResolutionStrategy(
                ResolutionStrategy(
                    Size(TARGET_WIDTH, TARGET_HEIGHT),
                    ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                )
            )
            .build()

        val preview = Preview.Builder()
            .setResolutionSelector(resolutionSelector)
            .build()
            .also { it.setSurfaceProvider(binding.previewView.surfaceProvider) }

        val imageAnalysis = ImageAnalysis.Builder()
            .setResolutionSelector(resolutionSelector)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            // Tek düzlemli RGBA: YUV dönüşümü ve Bitmap ara adımı yok
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
            .build()
            .also { it.setAnalyzer(analysisExecutor, analyzer) }

        provider.unbindAll()
        provider.bindToLifecycle(
            this,
            CameraSelector.DEFAULT_BACK_CAMERA,
            preview,
            imageAnalysis
        )
    }

    override fun onDestroy() {
        super.onDestroy()
        cameraProvider?.unbindAll()
        analysisExecutor.shutdown()
    }

    private companion object {
        const val TAG = "MainActivity"
        const val TARGET_WIDTH = 640
        const val TARGET_HEIGHT = 480
    }
}
