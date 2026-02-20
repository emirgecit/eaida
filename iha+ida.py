import sys
import math
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView
from pymavlink import mavutil

# --- 1. ARKA PLAN İŞLEMİ: İDA (Su Üstü Aracı) Bağlantısı (868 MHz / Port: 14551) ---
class IDABaglantisi(QThread):
    # Sinyal: Enlem, Boylam, Hız, Roll, Pitch, Heading, Mod, Arm Durumu, Yön Set Pointi, Hız Set Pointi
    veri_sinyali = pyqtSignal(float, float, float, float, float, float, str, str, float, float)

    def run(self):
        print("İDA Bağlantısı Bekleniyor (Port: 14551)...")
        self.master = mavutil.mavlink_connection('udpin:0.0.0.0:14551')
        self.master.wait_heartbeat()
        print("İDA Bağlantısı Başarılı!")

        enlem, boylam, hiz, roll, pitch, heading = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        yon_set_point = 0.0
        hiz_set_point = 0.0
        mod = "BİLİNMİYOR"
        arm_durumu = "BİLİNMİYOR"

        while True:
            # NAV_CONTROLLER_OUTPUT eklendi (Set point verileri için)
            mesaj = self.master.recv_match(type=['GLOBAL_POSITION_INT', 'VFR_HUD', 'ATTITUDE', 'HEARTBEAT', 'NAV_CONTROLLER_OUTPUT'], blocking=True)
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
            elif tip == 'NAV_CONTROLLER_OUTPUT':
                # Otonom sürüşteki hedeflenen yön (Target Bearing)
                yon_set_point = mesaj.target_bearing
            elif tip == 'HEARTBEAT':
                mod = mavutil.mode_string_v10(mesaj)
                if mesaj.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
                    arm_durumu = "ARM (Motorlar Aktif)"
                else:
                    arm_durumu = "DISARM (Motorlar Kapalı)"

            self.veri_sinyali.emit(enlem, boylam, hiz, roll, pitch, heading, mod, arm_durumu, yon_set_point, hiz_set_point)

    def arm_arac(self):
        if hasattr(self, 'master'):
            self.master.mav.command_long_send(self.master.target_system, self.master.target_component, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 21196, 0, 0, 0, 0, 0)

    def disarm_arac(self):
        if hasattr(self, 'master'):
            self.master.mav.command_long_send(self.master.target_system, self.master.target_component, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 0, 21196, 0, 0, 0, 0, 0)

    def waypoint_gonder(self, hedeflenen_enlem, hedeflenen_boylam):
        if hasattr(self, 'master'):
            try:
                mod_id = self.master.mode_mapping()['GUIDED']
                self.master.mav.set_mode_send(self.master.target_system, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mod_id)
            except Exception as e:
                pass

            self.master.mav.set_position_target_global_int_send(
                0, self.master.target_system, self.master.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                int(0b110111111000), 
                int(hedeflenen_enlem * 1e7), int(hedeflenen_boylam * 1e7),
                0, 0, 0, 0, 0, 0, 0, 0, 0)


# --- 2. ARKA PLAN İŞLEMİ: İHA Bağlantısı (433 MHz / Port: 14552) ---
class IHABaglantisi(QThread):
    # Sinyal: Renk Verisi, İHA Modu
    veri_sinyali = pyqtSignal(str, str)

    def run(self):
        # Gerçekte SiK Radio'nun bağlı olduğu port dinlenecek. Simülasyon için 14552 varsayalım.
        print("İHA Bağlantısı Bekleniyor (Port: 14552)...")
        try:
            self.master = mavutil.mavlink_connection('udpin:0.0.0.0:14552')
            self.master.wait_heartbeat(timeout=5)
            iha_bagli = True
            print("İHA Bağlantısı Başarılı!")
        except:
            iha_bagli = False
            print("İHA Bulunamadı. Sadece İDA ile devam ediliyor.")

        hedef_renk = "BEKLENİYOR"
        mod = "BİLİNMİYOR"

        while iha_bagli:
            # STATUSTEXT üzerinden görev bilgisayarının gönderdiği renk tespitini yakalayacağız
            mesaj = self.master.recv_match(type=['HEARTBEAT', 'STATUSTEXT'], blocking=True)
            if not mesaj: continue

            if mesaj.get_type() == 'HEARTBEAT':
                mod = mavutil.mode_string_v10(mesaj)
            elif mesaj.get_type() == 'STATUSTEXT':
                metin = mesaj.text
                # Görev bilgisayarı "COLOR:RED" gibi bir mesaj yollarsa bunu arayüze al
                if "COLOR:" in metin:
                    hedef_renk = metin.split(":")[1]

            self.veri_sinyali.emit(hedef_renk, mod)

    # İHA için RC Override (Manuel uçuş komutları)
    def rc_override_gonder(self, ch1=1500, ch2=1500, ch3=1500, ch4=1500):
        if hasattr(self, 'master'):
            # ch1: Roll, ch2: Pitch, ch3: Throttle, ch4: Yaw (1000-2000 arası PWM)
            self.master.mav.rc_channels_override_send(
                self.master.target_system, self.master.target_component,
                ch1, ch2, ch3, ch4, 0, 0, 0, 0)


# --- 3. ANA ARAYÜZ ---
class YerKontrolIstasyonu(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KTÜ UZAY YGM - Çift Araçlı Sistem Mimarisi YKİ")
        self.setGeometry(100, 100, 1300, 750)

        merkez_widget = QWidget()
        self.setCentralWidget(merkez_widget)
        ana_duzen = QHBoxLayout()
        sol_panel_duzeni = QVBoxLayout()

        # --- İDA PANELİ ---
        ida_grubu = QGroupBox("İDA Telemetri (RFD868x - 868 MHz)")
        ida_duzeni = QVBoxLayout()
        self.lbl_enlem = QLabel("Enlem: -")
        self.lbl_boylam = QLabel("Boylam: -")
        self.lbl_hiz = QLabel("Yer Hızı: - m/s")
        self.lbl_roll = QLabel("Roll: - °")
        self.lbl_pitch = QLabel("Pitch: - °")
        self.lbl_heading = QLabel("Heading (Yaw): - °")
        self.lbl_yon_sp = QLabel("Yön Set Pointi: - °") # METİNE GÖRE EKLENDİ
        self.lbl_hiz_sp = QLabel("Hız Set Pointi: - m/s") # METİNE GÖRE EKLENDİ
        self.lbl_mod = QLabel("Mod: BEKLENİYOR")
        self.lbl_arm_durumu = QLabel("Motor Durumu: BEKLENİYOR")
        self.lbl_arm_durumu.setStyleSheet("font-weight: bold; color: darkred;")

        ida_duzeni.addWidget(self.lbl_enlem)
        ida_duzeni.addWidget(self.lbl_boylam)
        ida_duzeni.addWidget(self.lbl_hiz)
        ida_duzeni.addWidget(self.lbl_roll)
        ida_duzeni.addWidget(self.lbl_pitch)
        ida_duzeni.addWidget(self.lbl_heading)
        ida_duzeni.addWidget(self.lbl_yon_sp)
        ida_duzeni.addWidget(self.lbl_hiz_sp)
        ida_duzeni.addWidget(self.lbl_mod)
        ida_duzeni.addWidget(self.lbl_arm_durumu)
        ida_grubu.setLayout(ida_duzeni)

        # --- İHA PANELİ ---
        iha_grubu = QGroupBox("İHA Telemetri (SiK Radio - 433 MHz)")
        iha_duzeni = QVBoxLayout()
        self.lbl_iha_mod = QLabel("İHA Modu: BAĞLANTI YOK")
        self.lbl_iha_renk = QLabel("Tespit Edilen Hedef Renk: YOK") # METİNE GÖRE EKLENDİ
        self.lbl_iha_renk.setStyleSheet("font-weight: bold; color: blue;")
        
        # RC Override Simülasyon Butonları (Metne Göre Eklendi)
        self.btn_iha_yuksel = QPushButton("İHA RC Override: Yüksel (Gaz Ver)")
        
        iha_duzeni.addWidget(self.lbl_iha_mod)
        iha_duzeni.addWidget(self.lbl_iha_renk)
        iha_duzeni.addWidget(self.btn_iha_yuksel)
        iha_grubu.setLayout(iha_duzeni)

        # --- İDA KONTROLLERİ ---
        kontrol_grubu = QGroupBox("İDA Kontrolleri")
        kontrol_duzeni = QHBoxLayout()
        self.btn_arm = QPushButton("İDA ARM")
        self.btn_disarm = QPushButton("İDA DISARM")
        kontrol_duzeni.addWidget(self.btn_arm)
        kontrol_duzeni.addWidget(self.btn_disarm)
        kontrol_grubu.setLayout(kontrol_duzeni)

        sol_panel_duzeni.addWidget(ida_grubu)
        sol_panel_duzeni.addWidget(iha_grubu)
        sol_panel_duzeni.addWidget(kontrol_grubu)
        sol_panel_duzeni.addStretch()

        # --- HARİTA ---
        self.harita_view = QWebEngineView()
        self.harita_html_yukle()
        self.harita_view.titleChanged.connect(self.haritadan_veri_al)

        ana_duzen.addLayout(sol_panel_duzeni, 1)
        ana_duzen.addWidget(self.harita_view, 3)
        merkez_widget.setLayout(ana_duzen)

        # THREADLERİ BAŞLAT
        # 1. İDA Thread
        self.ida_thread = IDABaglantisi()
        self.ida_thread.veri_sinyali.connect(self.ida_ekrani_guncelle)
        self.ida_thread.start()

        # 2. İHA Thread
        self.iha_thread = IHABaglantisi()
        self.iha_thread.veri_sinyali.connect(self.iha_ekrani_guncelle)
        self.iha_thread.start()

        # İDA BUTONLARI
        self.btn_arm.clicked.connect(self.ida_thread.arm_arac)
        self.btn_disarm.clicked.connect(self.ida_thread.disarm_arac)
        
        # İHA BUTONLARI
        # Tıklayınca 3. kanala (Throttle) 1800 PWM gönderir
        self.btn_iha_yuksel.pressed.connect(lambda: self.iha_thread.rc_override_gonder(ch3=1800))
        # Bırakınca tekrar 1500'e (orta nokta) çeker
        self.btn_iha_yuksel.released.connect(lambda: self.iha_thread.rc_override_gonder(ch3=1500))

    def haritadan_veri_al(self, title):
        if title.startswith("WAYPOINT:"):
            koordinatlar = title.replace("WAYPOINT:", "").split(",")
            self.ida_thread.waypoint_gonder(float(koordinatlar[0]), float(koordinatlar[1]))

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

    def ida_ekrani_guncelle(self, enlem, boylam, hiz, roll, pitch, heading, mod, arm_durumu, yon_sp, hiz_sp):
        if enlem != 0.0 and boylam != 0.0:
            self.lbl_enlem.setText(f"Enlem: {enlem:.6f}")
            self.lbl_boylam.setText(f"Boylam: {boylam:.6f}")
            self.harita_view.page().runJavaScript(f"konumuGuncelle({enlem}, {boylam});")

        self.lbl_hiz.setText(f"Yer Hızı: {hiz} m/s")
        self.lbl_roll.setText(f"Roll: {roll}°")
        self.lbl_pitch.setText(f"Pitch: {pitch}°")
        self.lbl_heading.setText(f"Heading (Yaw): {heading}°")
        self.lbl_yon_sp.setText(f"Yön Set Pointi: {yon_sp}°")
        
        # Hız set pointi GUIDED modda genelde aracın WP_SPEED parametresiyle sabittir
        self.lbl_hiz_sp.setText(f"Hız Set Pointi: {hiz_sp} m/s (Sistem Varsayılanı)")
        
        self.lbl_mod.setText(f"Mod: {mod}")
        self.lbl_arm_durumu.setText(f"Motor Durumu: {arm_durumu}")
        if "DISARM" in arm_durumu:
            self.lbl_arm_durumu.setStyleSheet("font-weight: bold; color: red;")
        else:
            self.lbl_arm_durumu.setStyleSheet("font-weight: bold; color: green;")

    def iha_ekrani_guncelle(self, renk, mod):
        self.lbl_iha_mod.setText(f"İHA Modu: {mod}")
        self.lbl_iha_renk.setText(f"Tespit Edilen Hedef Renk: {renk}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    pencere = YerKontrolIstasyonu()
    pencere.show()
    sys.exit(app.exec_())
