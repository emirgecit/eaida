# Chaquopy, Python <-> Java köprüsünü yansıma (reflection) ile kurar.
-keep class com.chaquo.python.** { *; }
-keep class com.chaquo.python.android.** { *; }
-dontwarn com.chaquo.python.**

# CameraX
-dontwarn androidx.camera.**
