package com.emir.renkalgilama

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.max

/**
 * Kamera önizlemesinin üzerine tespit kutusunu ve renk adını çizer.
 *
 * Koordinatlar Python'dan **kare uzayında** (örn. 480x640) gelir; bu görünüm
 * onları ekran uzayına `PreviewView`'in FILL_CENTER ölçekleme kuralıyla
 * birebir aynı şekilde taşır, böylece kutu görüntüyle hizalı kalır.
 *
 * `onDraw` içinde nesne ayrılmaz: tüm `Paint` ve `RectF` örnekleri alandır.
 */
class OverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val density = resources.displayMetrics.density

    private val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3f * density
        strokeCap = Paint.Cap.ROUND
    }

    private val labelBackgroundPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    private val labelTextPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.BLACK
        textSize = 14f * density
        isFakeBoldText = true
    }

    private val crosshairPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.5f * density
        color = Color.argb(160, 255, 255, 255)
    }

    private val boxRect = RectF()
    private val labelRect = RectF()

    private var detection: DetectionResult = DetectionResult.NONE
    private var frameWidth = 0
    private var frameHeight = 0

    /**
     * Yeni sonucu uygular ve gerekirse yeniden çizim ister.
     * Ana iş parçacığından çağrılmalıdır.
     */
    fun update(result: DetectionResult, frameWidth: Int, frameHeight: Int) {
        val unchanged = result == detection &&
            frameWidth == this.frameWidth &&
            frameHeight == this.frameHeight
        if (unchanged) return

        this.detection = result
        this.frameWidth = frameWidth
        this.frameHeight = frameHeight
        invalidate()
    }

    /** Tespiti temizler (örneğin kamera durdurulduğunda). */
    fun clear() = update(DetectionResult.NONE, frameWidth, frameHeight)

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        drawCrosshair(canvas)

        val result = detection
        if (!result.detected || frameWidth <= 0 || frameHeight <= 0) return

        // PreviewView FILL_CENTER ile aynı dönüşüm: en büyük ölçek + ortalama
        val scale = max(width.toFloat() / frameWidth, height.toFloat() / frameHeight)
        val offsetX = (width - frameWidth * scale) / 2f
        val offsetY = (height - frameHeight * scale) / 2f

        boxRect.set(
            result.x * scale + offsetX,
            result.y * scale + offsetY,
            (result.x + result.width) * scale + offsetX,
            (result.y + result.height) * scale + offsetY
        )

        val accent = result.drawColor
        boxPaint.color = accent
        canvas.drawRoundRect(boxRect, CORNER_RADIUS * density, CORNER_RADIUS * density, boxPaint)

        drawLabel(canvas, result.color, accent)
    }

    private fun drawLabel(canvas: Canvas, text: String, accent: Int) {
        if (text.isEmpty()) return

        val padH = 8f * density
        val padV = 5f * density
        val textWidth = labelTextPaint.measureText(text)
        val metrics = labelTextPaint.fontMetrics
        val textHeight = metrics.descent - metrics.ascent

        val labelHeight = textHeight + padV * 2
        // Kutu ekranın tepesindeyse etiketi kutunun içine al
        val top = if (boxRect.top - labelHeight >= 0f) boxRect.top - labelHeight else boxRect.top

        labelRect.set(boxRect.left, top, boxRect.left + textWidth + padH * 2, top + labelHeight)
        labelBackgroundPaint.color = accent
        canvas.drawRoundRect(
            labelRect,
            LABEL_RADIUS * density,
            LABEL_RADIUS * density,
            labelBackgroundPaint
        )

        canvas.drawText(
            text,
            labelRect.left + padH,
            labelRect.top + padV - metrics.ascent,
            labelTextPaint
        )
    }

    private fun drawCrosshair(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val arm = 10f * density
        canvas.drawLine(cx - arm, cy, cx + arm, cy, crosshairPaint)
        canvas.drawLine(cx, cy - arm, cx, cy + arm, crosshairPaint)
    }

    private companion object {
        const val CORNER_RADIUS = 6f
        const val LABEL_RADIUS = 4f
    }
}
