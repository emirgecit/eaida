plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.emir.renkalgilama"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.emir.renkalgilama"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"

        // Chaquopy + OpenCV yalnızca bu ABI'ler için paketlenir.
        // armeabi-v7a (32-bit) eklenirse APK boyutu ciddi büyür; modern cihazlar arm64.
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
        // Chaquopy, .so dosyalarını çalışma anında dosya sistemine açar
        // (manifest'teki android:extractNativeLibs="true" ile eşleşir).
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

chaquopy {
    defaultConfig {
        // 3.10 zorunlu: Chaquopy deposunda OpenCV yalnızca cp38 ve cp310
        // için derlenmiş tekerlek (wheel) olarak bulunuyor.
        version = "3.10"

        // pip, geliştirme makinesindeki bir Python yorumlayıcısına ihtiyaç duyar.
        // Bulunamazsa aşağıdaki satırı kendi yolunuzla açın:
        // buildPython("/usr/local/bin/python3.11")

        pip {
            // Sürümler SABİTLENMELİ. Sabitlenmezse pip, Chaquopy deposunda
            // derlenmiş tekerleği (wheel) bulunmayan bir sürüm seçip kaynaktan
            // derlemeye çalışır ve build başarısız olur.
            // headless sürüm: highgui/GTK bağımlılığı yok, APK daha küçük.
            // `import cv2` kullanımı normal sürümle birebir aynıdır.
            install("numpy==1.26.2")
            install("opencv-python-headless==4.5.1.48")
        }
    }

    // vision.py -> app/src/main/python/vision.py (Chaquopy varsayılan dizini)
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")

    // MVVM
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.activity:activity-ktx:1.9.3")

    // CameraX
    val cameraxVersion = "1.4.1"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")
}
