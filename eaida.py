import sys
import math
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView
from pymavlink import mavutil

# --- 1. ARKA PLAN İŞLEMİ: MAVLink Bağlantısı ve Komutlar ---
class SITLBaglantisi(QThread):
    # YENİ: Sinyale 'arm_durumu' bilgisini de (8. parametre olarak) ekledik.
    veri_sinyali = pyqtSignal(float, float, float, float, float, float, str, str)

    def run(self):
        print("SITL Simülasyonuna bağlanılıyor (Port: 14551)...")
        self.master = mavutil.mavlink_connection('udpin:0.0.0.0:14551')
        self.master.wait_heartbeat()
        print("SITL Bağlantısı Başarılı! Veri akışı başlıyor...")

        enlem, boylam, hiz, roll, pitch, heading = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        mod = "BİLİNMİYOR"
        arm_durumu = "BİLİNMİYOR"

        while True:
            mesaj = self.master.recv_match(type=['GLOBAL_POSITION_INT', 'VFR_HUD', 'ATTITUDE', 'HEARTBEAT'], blocking=True)
            if not mesaj:
                continue

            tip = mesaj.get_type()

            if tip == 'GLOBAL_POSITION_INT':
                enlem = mesaj.lat / 1e7
                boylam = mesaj.lon / 1e7
            elif tip == 'VFR_HUD':
                hiz = round(mesaj.groundspeed, 1)
            elif tip == 'ATTITUDE':
                roll = round(math.degrees(mesaj.roll), 2)
                pitch = round(math.degrees(mesaj.pitch), 2)
                heading = round(math.degrees(mesaj.yaw), 2)
                if heading < 0: heading += 360
            elif tip == 'HEARTBEAT':
                # Uçuş Modunu Al (MANUAL, GUIDED vs.)
                mod = mavutil.mode_string_v10(mesaj)
                
                # YENİ: ARM Durumunu Al
                # base_mode bayraklarında SAFETY_ARMED biti aktif mi diye kontrol ediyoruz
                if mesaj.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
                    arm_durumu = "ARM (Motorlar Aktif)"
                else:
                    arm_durumu = "DISARM (Motorlar Kapalı)"

            self.veri_sinyali.emit(enlem, boylam, hiz, roll, pitch, heading, mod, arm_durumu)

    # --- ARM ve DISARM Fonksiyonları ---
    def arm_arac(self):
        if hasattr(self, 'master'):
            print("ARM komutu gönderiliyor (Force Arm)...")
            self.master.mav.command_long_send(
                self.master.target_system, self.master.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                1, 21196, 0, 0, 0, 0, 0) # NOT: Daha sonra düzeltilecek (Gerçek araçta 21196 silinip 0 yapılacak)

    def disarm_arac(self):
        if hasattr(self, 'master'):
            print("DISARM komutu gönderiliyor (Force Disarm)...")
            self.master.mav.command_long_send(
                self.master.target_system, self.master.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                0, 21196, 0, 0, 0, 0, 0) # NOT: Daha sonra düzeltilecek

    # --- Waypoint (Hedef) Gönderme Fonksiyonu ---
    def waypoint_gonder(self, hedeflenen_enlem, hedeflenen_boylam):
        if hasattr(self, 'master'):
            print(f"Hedef gönderiliyor: {hedeflenen_enlem}, {hedeflenen_boylam}")
            
            try:
                mod_id = self.master.mode_mapping()['GUIDED']
                self.master.mav.set_mode_send(
                    self.master.target_system,
                    mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                    mod_id)
            except Exception as e:
                print("Mod değiştirilemedi:", e)

            self.master.mav.set_position_target_global_int_send(
                0, 
                self.master.target_system, self.master.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                int(0b110111111000), 
                int(hedeflenen_enlem * 1e7), 
                int(hedeflenen_boylam * 1e7),
                0, 0, 0, 0, 0, 0, 0, 0, 0)

# --- 2. ANA ARAYÜZ ---
class YerKontrolIstasyonu(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KTÜ UZAY YGM - Çift Araçlı Yer Kontrol İstasyonu")
        self.setGeometry(100, 100, 1200, 700)

        merkez_widget = QWidget()
        self.setCentralWidget(merkez_widget)
        ana_duzen = QHBoxLayout()
        sol_panel_duzeni = QVBoxLayout()

        ida_grubu = QGroupBox("İDA Canlı Telemetri")
        ida_duzeni = QVBoxLayout()
        self.lbl_enlem = QLabel("Enlem: -")
        self.lbl_boylam = QLabel("Boylam: -")
        self.lbl_hiz = QLabel("Yer Hızı: - m/s")
        self.lbl_roll = QLabel("Roll: - °")
        self.lbl_pitch = QLabel("Pitch: - °")
        self.lbl_heading = QLabel("Heading (Yaw): - °")
        self.lbl_mod = QLabel("Mod: BEKLENİYOR")
        self.lbl_arm_durumu = QLabel("Motor Durumu: BEKLENİYOR") # YENİ: Arayüze ARM bilgisi eklendi

        # Yazıları biraz daha belirgin (kalın) yapalım
        self.lbl_arm_durumu.setStyleSheet("font-weight: bold; color: darkred;") 

        ida_duzeni.addWidget(self.lbl_enlem)
        ida_duzeni.addWidget(self.lbl_boylam)
        ida_duzeni.addWidget(self.lbl_hiz)
        ida_duzeni.addWidget(self.lbl_roll)
        ida_duzeni.addWidget(self.lbl_pitch)
        ida_duzeni.addWidget(self.lbl_heading)
        ida_duzeni.addWidget(self.lbl_mod)
        ida_duzeni.addWidget(self.lbl_arm_durumu)
        ida_grubu.setLayout(ida_duzeni)

        kontrol_grubu = QGroupBox("İDA Kontrolleri")
        kontrol_duzeni = QVBoxLayout()
        self.btn_arm = QPushButton("ARM (Motorları Aktif Et)")
        self.btn_disarm = QPushButton("DISARM (Motorları Kapat)")
        kontrol_duzeni.addWidget(self.btn_arm)
        kontrol_duzeni.addWidget(self.btn_disarm)
        kontrol_grubu.setLayout(kontrol_duzeni)

        sol_panel_duzeni.addWidget(ida_grubu)
        sol_panel_duzeni.addWidget(kontrol_grubu)
        sol_panel_duzeni.addStretch()

        self.harita_view = QWebEngineView()
        self.harita_html_yukle()
        
        self.harita_view.titleChanged.connect(self.haritadan_veri_al)

        ana_duzen.addLayout(sol_panel_duzeni, 1)
        ana_duzen.addWidget(self.harita_view, 3)
        merkez_widget.setLayout(ana_duzen)

        self.telemetri_thread = SITLBaglantisi()
        self.telemetri_thread.veri_sinyali.connect(self.ekrani_guncelle)
        self.telemetri_thread.start()

        self.btn_arm.clicked.connect(self.telemetri_thread.arm_arac)
        self.btn_disarm.clicked.connect(self.telemetri_thread.disarm_arac)

    def haritadan_veri_al(self, title):
        if title.startswith("WAYPOINT:"):
            koordinatlar = title.replace("WAYPOINT:", "").split(",")
            secilen_enlem = float(koordinatlar[0])
            secilen_boylam = float(koordinatlar[1])
            self.telemetri_thread.waypoint_gonder(secilen_enlem, secilen_boylam)

    def harita_html_yukle(self):
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
                var map = L.map('map').setView([0, 0], 2);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19}).addTo(map);
                
                var arac_marker = null;
                var hedef_marker = null;

                function konumuGuncelle(enlem, boylam) {
                    var yeniKonum = new L.LatLng(enlem, boylam);
                    if (arac_marker === null) {
                        arac_marker = L.marker(yeniKonum).addTo(map);
                        map.setView(yeniKonum, 18);
                    } else {
                        arac_marker.setLatLng(yeniKonum);
                    }
                }

                map.on('click', function(e) {
                    var lat = e.latlng.lat;
                    var lng = e.latlng.lng;
                    
                    if (hedef_marker === null) {
                        hedef_marker = L.circleMarker([lat, lng], {color: 'red', radius: 8}).addTo(map);
                    } else {
                        hedef_marker.setLatLng([lat, lng]);
                    }

                    document.title = "WAYPOINT:" + lat + "," + lng;
                });
            </script>
        </body>
        </html>
        """
        self.harita_view.setHtml(html_kodu)

    # YENİ: arm_durumu parametresi eklendi
    def ekrani_guncelle(self, enlem, boylam, hiz, roll, pitch, heading, mod, arm_durumu):
        if enlem != 0.0 and boylam != 0.0:
            self.lbl_enlem.setText(f"Enlem: {enlem:.6f}")
            self.lbl_boylam.setText(f"Boylam: {boylam:.6f}")
            js_kodu = f"konumuGuncelle({enlem}, {boylam});"
            self.harita_view.page().runJavaScript(js_kodu)

        self.lbl_hiz.setText(f"Yer Hızı: {hiz} m/s")
        self.lbl_roll.setText(f"Roll: {roll}°")
        self.lbl_pitch.setText(f"Pitch: {pitch}°")
        self.lbl_heading.setText(f"Heading (Yaw): {heading}°")
        self.lbl_mod.setText(f"Mod: {mod}")
        
        # YENİ: Ekrana ARM durumunu yansıt ve duruma göre renk değiştir
        self.lbl_arm_durumu.setText(f"Motor Durumu: {arm_durumu}")
        if "DISARM" in arm_durumu:
            self.lbl_arm_durumu.setStyleSheet("font-weight: bold; color: red;")
        else:
            self.lbl_arm_durumu.setStyleSheet("font-weight: bold; color: green;")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    pencere = YerKontrolIstasyonu()
    pencere.show()
    sys.exit(app.exec_())
