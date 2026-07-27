package com.emir.renkalgilama

import android.app.Application
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Ekran durumunu tutan ve Python köprüsünün ömrünü yöneten ViewModel.
 *
 * Activity yeniden oluşturulsa bile (ekran döndürme vb.) Python yorumlayıcısı
 * yeniden başlatılmaz; [PythonBridge] süreç ömrü boyunca tekildir.
 */
class VisionViewModel(application: Application) : AndroidViewModel(application) {

    private val _uiState = MutableStateFlow(VisionUiState())
    val uiState: StateFlow<VisionUiState> = _uiState.asStateFlow()

    private var bridge: PythonBridge? = null

    init {
        startPython()
    }

    /** Python açılışı ~1 saniye sürebilir; ana iş parçacığı bloklanmaz. */
    private fun startPython() {
        viewModelScope.launch {
            runCatching {
                withContext(Dispatchers.Default) {
                    PythonBridge.getInstance(getApplication())
                }
            }.onSuccess { ready ->
                bridge = ready
                _uiState.update { it.copy(pythonReady = true, errorMessage = null) }
            }.onFailure { error ->
                Log.e(TAG, "Python başlatılamadı", error)
                _uiState.update {
                    it.copy(
                        pythonReady = false,
                        errorMessage = "Python başlatılamadı: ${error.message}"
                    )
                }
            }
        }
    }

    /**
     * Kameraya bağlanacak analizciyi üretir.
     * Python henüz hazır değilse `null` döner.
     */
    fun createAnalyzer(): CameraAnalyzer? {
        val ready = bridge ?: return null
        return CameraAnalyzer(
            bridge = ready,
            targetFps = CameraAnalyzer.DEFAULT_TARGET_FPS,
            onResult = ::onFrameProcessed,
            onError = ::onFrameFailed
        )
    }

    /** Analiz iş parçacığından çağrılır; [MutableStateFlow] güvenlidir. */
    private fun onFrameProcessed(result: DetectionResult, stats: FrameStats) {
        _uiState.update { it.copy(detection = result, stats = stats, errorMessage = null) }
    }

    private fun onFrameFailed(error: Throwable) {
        Log.e(TAG, "Kare işlenemedi", error)
        _uiState.update {
            it.copy(
                detection = DetectionResult.NONE,
                errorMessage = "İşleme hatası: ${error.message}"
            )
        }
    }

    private companion object {
        const val TAG = "VisionViewModel"
    }
}
