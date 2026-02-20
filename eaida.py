import sys
import time
import random
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView

# 1. ARKA PLAN İŞLEMİ (Simülasyon Verisi)
class TelemetriSimulasyonu(QThread):
    veri_sinyali = pyqtSignal(float, float, float, str)

    def run(self):
        # Başlangıç koordinatları (Örn: Trabzon/Batumi rotasına uygun bir nokta)
        enlem = 40.990000
        boylam = 39.770000 
        
        while True:
            time.sleep(1) 
            enlem += random.uniform(-0.0002, 0.0002)
            boylam += random.uniform(-0.0002, 0.0002)
            hiz = round(random.uniform(2.0, 5.5), 1)
            mod = "GÖREVDE (OTONOM)"
            self.veri_sinyali.emit(enlem, boylam, hiz, mod)

# 2. ANA ARAYÜZ
class YerKontrolIstasyonu(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KTÜ UZAY YGM - İDA Yer Kontrol İstasyonu")
        self.setGeometry(100, 100, 1000, 600) # Harita sığsın diye pencereyi büyüttük

        merkez_widget = QWidget()
        self.setCentralWidget(merkez_widget)
        
        # Ana düzeni yatay yaptık: Sol taraf paneller, Sağ taraf harita
        ana_duzen = QHBoxLayout() 
        sol_panel_duzeni = QVBoxLayout()

        # --- Telemetri Bölümü ---
        telemetri_grubu = QGroupBox("Canlı Telemetri Verileri")
        telemetri_duzeni = QVBoxLayout()
        self.lbl_enlem = QLabel("Enlem: -")
        self.lbl_boylam = QLabel("Boylam: -")
        self.lbl_hiz = QLabel("Yer Hızı: -")
        self.lbl_mod = QLabel("Uçuş Modu: BEKLENİYOR")
        telemetri_duzeni.addWidget(self.lbl_enlem)
        telemetri_duzeni.addWidget(self.lbl_boylam)
        telemetri_duzeni.addWidget(self.lbl_hiz)
        telemetri_duzeni.addWidget(self.lbl_mod)
        telemetri_grubu.setLayout(telemetri_duzeni)

        # --- Kontrol Bölümü ---
        kontrol_grubu = QGroupBox("Araç Kontrolleri")
        kontrol_duzeni = QVBoxLayout()
        self.btn_arm = QPushButton("ARM (Motorları Başlat)")
        self.btn_disarm = QPushButton("DISARM (Motorları Durdur)")
        self.btn_arm.clicked.connect(self.arm_komutu_gonder)
        self.btn_disarm.clicked.connect(self.disarm_komutu_gonder)
        kontrol_duzeni.addWidget(self.btn_arm)
        kontrol_duzeni.addWidget(self.btn_disarm)
        kontrol_grubu.setLayout(kontrol_duzeni)

        sol_panel_duzeni.addWidget(telemetri_grubu)
        sol_panel_duzeni.addWidget(kontrol_grubu)
        sol_panel_duzeni.addStretch() # Boşlukları aşağı iter, panelleri yukarı yaslar

        # --- Harita Bölümü (Leaflet) ---
        self.harita_view = QWebEngineView()
        self.harita_html_yukle() # Haritayı başlatan fonksiyonu çağırıyoruz

        # Düzenleri birleştirme
        ana_duzen.addLayout(sol_panel_duzeni, 1) # Sol panel 1 birim yer kaplasın
        ana_duzen.addWidget(self.harita_view, 3) # Harita 3 birim (daha geniş) yer kaplasın
        merkez_widget.setLayout(ana_duzen)

        # 3. THREAD'İ BAŞLATMA
        self.simulasyon_baslat()

    def harita_html_yukle(self):
        # Leaflet kütüphanesini kullanarak interaktif bir HTML haritası oluşturuyoruz
        html_kodu = """
        <!DOCTYPE html>
        <html>
        <head>
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <style> body { margin: 0; padding: 0; } #map { height: 100vh; width: 100vw; } </style>
        </head> 
        <body>
            <div id="map"></div>
            <script>
                // Haritayı başlat
                var map = L.map('map').setView([40.990000, 39.770000], 16);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    maxZoom: 19
                }).addTo(map);
                
                // Araç işaretçisi (Marker)
                var arac_marker = L.marker([40.990000, 39.770000]).addTo(map);

                // Python'dan çağrılacak JavaScript fonksiyonu
                function konumuGuncelle(enlem, boylam) {
                    var yeniKonum = new L.LatLng(enlem, boylam);
                    arac_marker.setLatLng(yeniKonum);
                    map.panTo(yeniKonum); // Haritayı araca ortala
                }
            </script>
        </body>
        </html>
        """
        self.harita_view.setHtml(html_kodu)

    def simulasyon_baslat(self):
        self.telemetri_thread = TelemetriSimulasyonu()
        self.telemetri_thread.veri_sinyali.connect(self.ekrani_guncelle)
        self.telemetri_thread.start()

    def ekrani_guncelle(self, enlem, boylam, hiz, mod):
        # Sol paneldeki yazıları güncelle
        self.lbl_enlem.setText(f"Enlem: {enlem:.6f}")
        self.lbl_boylam.setText(f"Boylam: {boylam:.6f}")
        self.lbl_hiz.setText(f"Yer Hızı: {hiz} m/s")
        if "ARM" not in self.lbl_mod.text() and "DISARM" not in self.lbl_mod.text():
            self.lbl_mod.setText(f"Uçuş Modu: {mod}")
        
        # Haritadaki konumu güncelle (JavaScript fonksiyonunu tetikler)
        js_kodu = f"konumuGuncelle({enlem}, {boylam});"
        self.harita_view.page().runJavaScript(js_kodu)

    def arm_komutu_gonder(self):
        self.lbl_mod.setText("Uçuş Modu: ARM EDİLDİ (MANUEL)")

    def disarm_komutu_gonder(self):
        self.lbl_mod.setText("Uçuş Modu: DISARM EDİLDİ (MANUEL)")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    pencere = YerKontrolIstasyonu()
    pencere.show()
    sys.exit(app.exec_())   