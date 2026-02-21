import os
import serial.tools.list_ports
from collections import deque
import sys
from math import degrees, radians, sin, cos  # sin ve cos fonksiyonları buraya eklendi
from PyQt5.QtCore import Qt, QCoreApplication, QUrl, QTimer, QObject, pyqtSlot, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QFileDialog,
    QFormLayout,
    QComboBox,
    QDialog,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from pymavlink import mavutil
import pyqtgraph as pg
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QFont

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"

# Harita widget'ı için HTML içeriği
MAP_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Harita</title>
    <style> html, body, #map { height: 100%; margin: 0; padding: 0; } </style>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
</head>
<body>
<div id="map"></div>
<script>
    let map = L.map('map').setView([39.0, 35.0], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18 }).addTo(map);

    let bridge = null;
    let ihaMarker = null, idaMarker = null;
    let ihaPathCoordinates = [], idaPathCoordinates = [];
    let ihaPathPolyline = null, idaPathPolyline = null;

    let ihaWaypointCoordinates = [], idaWaypointCoordinates = [];
    let ihaWaypointPolyline = null, idaWaypointPolyline = null;

    let ihaWaypointMarkers = [], idaWaypointMarkers = [];
    let selectedVehicle = "İHA";
    let ihaCount = 0, idaCount = 0;

    map.on('click', function(e) {
        let lat = e.latlng.lat;
        let lon = e.latlng.lng;

        if (selectedVehicle === "İHA") {
            ihaCount++;
            ihaWaypointCoordinates.push([lat, lon]);
            let marker = L.marker([lat, lon]).addTo(map).bindPopup("Rota " + ihaCount).openPopup();
            ihaWaypointMarkers.push(marker);
            if (ihaWaypointPolyline) map.removeLayer(ihaWaypointPolyline);
            ihaWaypointPolyline = L.polyline(ihaWaypointCoordinates, { color: 'orange', weight: 2, dashArray: '4,4' }).addTo(map);
        } else {
            idaCount++;
            idaWaypointCoordinates.push([lat, lon]);
            let marker = L.marker([lat, lon]).addTo(map).bindPopup("Rota " + idaCount).openPopup();
            idaWaypointMarkers.push(marker);
            if (idaWaypointPolyline) map.removeLayer(idaWaypointPolyline);
            idaWaypointPolyline = L.polyline(idaWaypointCoordinates, { color: 'purple', weight: 2, dashArray: '4,4' }).addTo(map);
        }
    });

    new QWebChannel(qt.webChannelTransport, function(channel) {
        bridge = channel.objects.bridge;

        bridge.setSelectedVehicle.connect(function(vehicle) {
            selectedVehicle = vehicle;
        });

        bridge.updateIhaPosition.connect(function(lat, lon) {
            if (!ihaMarker) {
                ihaMarker = L.marker([lat, lon], {
                    icon: L.icon({ iconUrl: "https://img.icons8.com/color/48/drone--v1.png", iconSize: [40, 40], iconAnchor: [20, 20] })
                }).addTo(map);
            } else {
                ihaMarker.setLatLng([lat, lon]);
            }

            map.setView([lat, lon], 17);
            ihaPathCoordinates.push([lat, lon]);
            if (ihaPathPolyline) map.removeLayer(ihaPathPolyline);
            ihaPathPolyline = L.polyline(ihaPathCoordinates, { color: 'red' }).addTo(map);
        });

        bridge.updateIdaPosition.connect(function(lat, lon) {
            if (!idaMarker) {
                idaMarker = L.marker([lat, lon], {
                    icon: L.icon({ iconUrl: "https://img.icons8.com/color/48/boat.png", iconSize: [40, 40], iconAnchor: [20, 20] })
                }).addTo(map);
            } else {
                idaMarker.setLatLng([lat, lon]);
            }

            map.setView([lat, lon], 17);
            idaPathCoordinates.push([lat, lon]);
            if (idaPathPolyline) map.removeLayer(idaPathPolyline);
            idaPathPolyline = L.polyline(idaPathCoordinates, { color: 'blue' }).addTo(map);
        });

        bridge.clearMapSignal.connect(function() {
            if (ihaMarker) { map.removeLayer(ihaMarker); ihaMarker = null; }
            if (idaMarker) { map.removeLayer(idaMarker); idaMarker = null; }

            if (ihaPathPolyline) { map.removeLayer(ihaPathPolyline); ihaPathPolyline = null; }
            if (idaPathPolyline) { map.removeLayer(idaPathPolyline); idaPathPolyline = null; }

            if (ihaWaypointPolyline) { map.removeLayer(ihaWaypointPolyline); ihaWaypointPolyline = null; }
            if (idaWaypointPolyline) { map.removeLayer(idaWaypointPolyline); idaWaypointPolyline = null; }

            ihaPathCoordinates = []; idaPathCoordinates = [];
            ihaWaypointCoordinates = []; idaWaypointCoordinates = [];

            ihaWaypointMarkers.forEach(m => map.removeLayer(m));
            idaWaypointMarkers.forEach(m => map.removeLayer(m));
            ihaWaypointMarkers = []; idaWaypointMarkers = [];

            ihaCount = 0; idaCount = 0;
        });
    });
</script>
</body>
</html>
'''

class CustomMessageBox(QDialog):
    """Mesaj kutusu."""
    def __init__(self, title, message, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        layout = QVBoxLayout()
        label = QLabel(message)
        layout.addWidget(label)
        ok_button = QPushButton("Tamam")
        ok_button.clicked.connect(self.accept)
        layout.addWidget(ok_button)
        self.setLayout(layout)

class Bridge(QObject):
    """Python (PyQt) ve JavaScript (Leaflet harita) arasında iletişim kurmak için Köprü sınıfı."""
    updateIhaPosition = pyqtSignal(float, float)
    updateIdaPosition = pyqtSignal(float, float)
    clearMapSignal = pyqtSignal()
    rollPitchUpdated = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ihaWaypoints = []
        self.idaWaypoints = []
        self.is_connected = False  # Bağlantı durumu için değişken
        self.selected_vehicle = "İHA"  

    @pyqtSlot(float, float)
    def addIhaWaypoint(self, lat, lon):
        """Bir İHA rota noktası ekler ve listeye kaydeder."""
        self.ihaWaypoints.append((lat, lon))
        print(f"İHA Rota Noktası Eklendi: {lat}, {lon}")

    @pyqtSlot(float, float)
    def addIdaWaypoint(self, lat, lon):
        """Bir İDA rota noktası ekler ve listeye kaydeder."""
        self.idaWaypoints.append((lat, lon))
        print(f"İDA Rota Noktası Eklendi: {lat}, {lon}")

    @pyqtSlot(str)
    def setSelectedVehicle(self, vehicle):
        self.selected_vehicle = vehicle

    @pyqtSlot(result=str)
    def getSelectedVehicle(self):
        return self.selected_vehicle

    @pyqtSlot(result=bool)
    def isConnected(self):
        """Bağlantı durumunu döndürür."""
        return self.is_connected  # Bu değişken, bağlantı durumu için kullan

class HorizonIndicator(QWidget):
    """Roll ve pitch'e göre ufuk göstergesini görüntüleme yeri."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.roll = 0.0
        self.pitch = 0.0
        self.setStyleSheet("background-color: lightgray;")
        self.setMinimumHeight(100)
        self.setMinimumWidth(200)

    def update_attitude(self, roll, pitch):
        """Roll ve pitch değerleri."""
        self.roll = roll
        self.pitch = pitch
        self.update()

    def paintEvent(self, event):
        """Ufuk göstergesini çizimi."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        center_x = width / 2
        center_y = height / 2
        
        horizon_y = center_y + self.pitch * 2  # Pitch'in ters yönlendirilmesi

        sky_color = QColor(80, 160, 240)
        ground_color = QColor(100, 180, 80)

        painter.fillRect(0, 0, width, int(horizon_y), sky_color)
        painter.fillRect(0, int(horizon_y), width, height - int(horizon_y), ground_color)

        pen = QPen(Qt.black, 2)
        painter.setPen(pen)
        horizon_line_y = int(center_y + self.pitch * 2)  # Pitch'in ters yönlendirilmesi
        painter.drawLine(0, horizon_line_y, width, horizon_line_y)

        center_point_radius = 8
        painter.setBrush(Qt.black)
        painter.drawEllipse(int(center_x - center_point_radius), int(horizon_line_y - center_point_radius),
                            2 * center_point_radius, 2 * center_point_radius)
        painter.setBrush(Qt.NoBrush)

        roll_angle_rad = radians(self.roll)
        roll_line_length = 40
        roll_line_end_x = center_x + roll_line_length * sin(roll_angle_rad)
        roll_line_end_y = horizon_line_y - roll_line_length * cos(roll_angle_rad)  # Y ekseninin ters çevrilmesi
        painter.drawLine(int(center_x), horizon_line_y, int(roll_line_end_x), int(roll_line_end_y))

        pitch_mark_length = 20
        pitch_spacing = 10

        for p_deg in range(10, 91, 10):
            y_offset = p_deg * pitch_spacing
            rotated_y = horizon_line_y - y_offset
            x1_rotated = center_x + ((-pitch_mark_length / 2) * cos(roll_angle_rad) - (rotated_y - horizon_line_y) * sin(roll_angle_rad))
            y1_rotated = horizon_line_y + ((-pitch_mark_length / 2) * sin(roll_angle_rad) + (rotated_y - horizon_line_y) * cos(roll_angle_rad))
            x2_rotated = center_x + ((pitch_mark_length / 2) * cos(roll_angle_rad) - (rotated_y - horizon_line_y) * sin(roll_angle_rad))
            y2_rotated = horizon_line_y + ((pitch_mark_length / 2) * sin(roll_angle_rad) + (rotated_y - horizon_line_y) * cos(roll_angle_rad))
            painter.drawLine(int(x1_rotated), int(y1_rotated), int(x2_rotated), int(y2_rotated))
            painter.drawText(int(x2_rotated + 5), int(y2_rotated + 5), str(p_deg))

        for p_deg in range(10, 91, 10):
            y_offset = p_deg * pitch_spacing
            rotated_y = horizon_line_y + y_offset
            x1_rotated = center_x + ((-pitch_mark_length / 2) * cos(roll_angle_rad) - (rotated_y - horizon_line_y) * sin(roll_angle_rad))
            y1_rotated = horizon_line_y + ((-pitch_mark_length / 2) * sin(roll_angle_rad) + (rotated_y - horizon_line_y) * cos(roll_angle_rad))
            x2_rotated = center_x + ((pitch_mark_length / 2) * cos(roll_angle_rad) - (rotated_y - horizon_line_y) * sin(roll_angle_rad))
            y2_rotated = horizon_line_y + ((pitch_mark_length / 2) * sin(roll_angle_rad) + (rotated_y - horizon_line_y) * cos(roll_angle_rad))
            painter.drawLine(int(x1_rotated), int(y1_rotated), int(x2_rotated), int(y2_rotated))
            painter.drawText(int(x2_rotated + 5), int(y2_rotated + 5), str(-p_deg))

        painter.drawLine(int(center_x), horizon_line_y - 10, int(center_x), horizon_line_y + 10)

        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(Qt.black)
        painter.drawText(10, 20, f"Pitch: {self.pitch:.1f}°")
        painter.drawText(10, 35, f"Roll: {self.roll:.1f}°")

class YerIstasyonu(QWidget):
    """Ana Yer İstasyonu uygulama penceresi."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("İnsansız Deniz Aracı Yer İstasyonu")
        self.setGeometry(100, 100, 1200, 1400)

        self.bridge = Bridge()
        self.mavlink_connection = None
        self.is_connected = False

        # LogText başlangıcı.
        self.logText = QTextEdit()
        self.logText.setReadOnly(True)

        # Çizim için veri kuyrukları
        self.ida_speed_data = deque(maxlen=100)
        self.ida_depth_data = deque(maxlen=100)
        self.iha_speed_data = deque(maxlen=100)
        self.iha_altitude_data = deque(maxlen=100)

        self.initUI()

        # Telemetre verileri için zamanlayıcı
        self.timer = QTimer()
        self.timer.timeout.connect(self.updateTelemetry)
        self.timer.start(1000)  # Her 1 saniyede bir güncellenir

        # Grafik güncellemeleri için zamanlayıcı
        self.graph_timer = QTimer()
        self.graph_timer.timeout.connect(self.updateGraphs)
        self.graph_timer.start(500)  # Her 0.5 saniyede bir güncellenir.

        # Köprü sinyalini ufuk göstergesi güncelleme slotuna bağlar.
        self.bridge.rollPitchUpdated.connect(self.horizon_indicator.update_attitude)

    def initUI(self):
        """Ana kullanıcı arayüzü öğelerini başlatır."""
        self.tabs = QTabWidget()
        self.mainTab = QWidget()
        self.graphTab = QWidget()
        self.logTab = QWidget()
        self.settingsTab = QWidget()

        self.tabs.addTab(self.mainTab, "Yer İstasyonu")
        self.tabs.addTab(self.graphTab, "Grafikler")
        self.tabs.addTab(self.logTab, "Log")
        self.tabs.addTab(self.settingsTab, "Ayarlar")

        self.initMainTabUI()
        self.initGraphTabUI()
        self.initLogTabUI()
        self.initSettingsTabUI()

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.setLayout(layout)

    def clearMap(self):
        """Harita işaretçilerini ve yollarını temizler."""
        self.bridge.clearMapSignal.emit()
        self.logText.append("Harita temizlendi.")

    def initMainTabUI(self):
        """Ana sekme UI öğelerini başlatır."""
        layout = QVBoxLayout()
        topLayout = QHBoxLayout()
        leftLayout = QVBoxLayout()
        rightLayout = QVBoxLayout()

        self.statusIndicator = QLabel()
        self.statusIndicator.setAlignment(Qt.AlignCenter)
        self.statusIndicator.setStyleSheet(
            """
            QLabel {
                border-radius: 10px;
                padding: 5px;
                font-weight: bold;
                font-size: 14px;
            }
            .connected { background: #4CAF50; color: white; }
            .disconnected { background: #F44336; color: white; }
        """
        )
        self.updateConnectionStatus()
        leftLayout.addWidget(self.statusIndicator)

        telemetryLayout = QHBoxLayout()

        # Araç Seçimi
        vehicleSelectLayout = QVBoxLayout()
        self.vehicleSelectLabel = QLabel("Araç Seçin:")
        self.vehicleSelectCombo = QComboBox()
        self.vehicleSelectCombo.addItems(["İHA", "İDA"])  
        vehicleSelectLayout.addWidget(self.vehicleSelectLabel)
        vehicleSelectLayout.addWidget(self.vehicleSelectCombo)
        leftLayout.addLayout(vehicleSelectLayout)

        # Harita tıklama işlemi
        self.mapWidget = QWebEngineView()
        self.mapWidget.setHtml(MAP_HTML)
        self.mapChannel = QWebChannel()
        self.mapChannel.registerObject("bridge", self.bridge)
        self.mapWidget.page().setWebChannel(self.mapChannel)
        
        # Araç seçimi değiştiğinde köprüye güncelleme yap
        self.vehicleSelectCombo.currentTextChanged.connect(self.updateSelectedVehicle)

        # Telemetri ve buton düzeni
        telemetryLayout = QHBoxLayout()

        # İDA Telemetri
        idaTelemetryLayout = QVBoxLayout()
        ida_title = QLabel("<b>İDA Telemetri</b>")
        ida_title.setAlignment(Qt.AlignCenter)
        idaTelemetryLayout.addWidget(ida_title)
        
        ida_telemetry_labels = [
            "İDA Enlem", "İDA Boylam", "İDA Hız (km/s)", "İDA Heading (°)", "İDA Roll (°)", 
            "İDA Pitch (°)", "İDA Yaw (°)", "İDA Derinlik (m)", "Motor Left PWM", "Motor Right PWM", "İDA Uçuş Modu"
        ]

        self.ida_telemetry_values = {}
        for label in ida_telemetry_labels:
            hbox = QHBoxLayout()
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet("font-weight: bold;")
            value_label = QLabel("-")
            value_label.setStyleSheet("min-width: 100px;")
            hbox.addWidget(lbl)
            hbox.addWidget(value_label)
            idaTelemetryLayout.addLayout(hbox)
            self.ida_telemetry_values[label] = value_label
        telemetryLayout.addLayout(idaTelemetryLayout)

        # İHA Telemetri
        ihaTelemetryLayout = QVBoxLayout()
        iha_title = QLabel("<br><b>İHA Telemetri</b>")
        iha_title.setAlignment(Qt.AlignCenter)
        ihaTelemetryLayout.addWidget(iha_title)
        
        iha_telemetry_labels = [
            "İHA Enlem", "İHA Boylam", "İHA Hız (km/s)", "İHA İrtifa (m)", "İHA Heading (°)", 
            "İHA Roll (°)", "İHA Pitch (°)", "İHA Yaw (°)", "Motor Left PWM", "Motor Right PWM", "İHA Uçuş Modu"
        ]

        self.iha_telemetry_values = {}
        for label in iha_telemetry_labels:
            hbox = QHBoxLayout()
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet("font-weight: bold;")
            value_label = QLabel("-")
            value_label.setStyleSheet("min-width: 100px;")
            hbox.addWidget(lbl)
            hbox.addWidget(value_label)
            ihaTelemetryLayout.addLayout(hbox)
            self.iha_telemetry_values[label] = value_label
        telemetryLayout.addLayout(ihaTelemetryLayout)

        leftLayout.addLayout(telemetryLayout)

        arm_disarm_layout = QHBoxLayout()
        self.armButton = QPushButton("Arm Et")
        self.armButton.setStyleSheet("background-color: green; color: white; padding: 8px; border-radius: 5px;")
        self.armButton.clicked.connect(self.armVehicle)
        arm_disarm_layout.addWidget(self.armButton)

        self.disarmButton = QPushButton("Disarm Et")
        self.disarmButton.setStyleSheet("background-color: red; color: white; padding: 8px; border-radius: 5px;")
        self.disarmButton.clicked.connect(self.disarmVehicle)
        arm_disarm_layout.addWidget(self.disarmButton)
        leftLayout.addLayout(arm_disarm_layout)

        self.sendMissionBtn = QPushButton("Görevi Gönder")
        self.sendMissionBtn.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px; border-radius: 5px;")
        self.sendMissionBtn.clicked.connect(self.sendMission)  # Burada sendMission metoduna bağlanıyor
        leftLayout.addWidget(self.sendMissionBtn)

        self.startMissionBtn = QPushButton("Görevi Başlat")
        self.startMissionBtn.setStyleSheet("background-color: #008CBA; color: white; padding: 8px; border-radius: 5px;")
        self.startMissionBtn.clicked.connect(self.startMission)
        leftLayout.addWidget(self.startMissionBtn)

        self.clearMapBtn = QPushButton("Haritayı Temizle")
        self.clearMapBtn.setStyleSheet("background-color: #f44336; color: white; padding: 8px; border-radius: 5px;")
        self.clearMapBtn.clicked.connect(self.clearMap)
        leftLayout.addWidget(self.clearMapBtn)

        # Bağlantı ayarları
        portSettingsLayout = QFormLayout()

        # Port Ayarları
        self.mainPortLabel = QLabel("Portu Seçin:")
        self.mainPortCombo = QComboBox()
        ports = self.list_serial_ports() or []
        self.mainPortCombo.addItems(sorted(list(set(["COM4", "COM6"] + ports))))
        if "COM4" in self.mainPortCombo.currentText():
            self.mainPortCombo.setCurrentText("COM4")
        portSettingsLayout.addRow(self.mainPortLabel, self.mainPortCombo)

        self.mainBaudLabel = QLabel("Baud Hızını Seçin:")
        self.mainBaudCombo = QComboBox()
        self.mainBaudCombo.addItems(["9600", "19200", "38400", "57600", "115200", "921600"])
        self.mainBaudCombo.setCurrentText("57600")
        portSettingsLayout.addRow(self.mainBaudLabel, self.mainBaudCombo)

        self.connectButtonMainTab = QPushButton("Bağlan")
        self.connectButtonMainTab.clicked.connect(self.connectToPixhawk)
        portSettingsLayout.addWidget(self.connectButtonMainTab)

        leftLayout.addLayout(portSettingsLayout)

        # Bağlantıyı kes butonu
        self.disconnectBtn = QPushButton("Bağlantıyı Kes")
        self.disconnectBtn.clicked.connect(self.disconnectFromPixhawk)
        leftLayout.addWidget(self.disconnectBtn)

        leftLayout.addStretch()

        topLayout.addLayout(leftLayout, stretch=1)

        mapLayout = QVBoxLayout()
        mapLayout.addWidget(self.mapWidget, stretch=2)
        topLayout.addLayout(mapLayout, stretch=2)

        self.horizon_indicator = HorizonIndicator()
        self.horizon_indicator.setMinimumHeight(300)
        self.horizon_indicator.setMinimumWidth(300)
        rightLayout.addWidget(self.horizon_indicator)

        # Araç simgeleri
        self.vehicleIconLabel = QLabel()
        self.vehicleIconLabel.setAlignment(Qt.AlignCenter)
        rightLayout.addWidget(self.vehicleIconLabel)

        # TAKIM LOGOSU
        self.logoLabel = QLabel()
        try: 
            pixmap = QPixmap("C:/Users/Nailcan/Desktop/assets/Logo.jpg")
            if not pixmap.isNull():
                self.logoLabel.setPixmap(pixmap.scaledToHeight(420, Qt.SmoothTransformation))
            else:
                self.logoLabel.setText("Logo Bulunamadı")
        except Exception as e:
            self.logoLabel.setText("Logo Bulunamadı")
        self.logoLabel.setAlignment(Qt.AlignRight)
        rightLayout.addWidget(self.logoLabel)

        rightLayout.addStretch()
        topLayout.addLayout(rightLayout, stretch=1)

        layout.addLayout(topLayout)
        self.mainTab.setLayout(layout)

    def updateSelectedVehicle(self, vehicle):
        """Seçilen aracı günceller."""
        self.bridge.setSelectedVehicle(vehicle)
        if vehicle == "İHA":
            self.logText.append("Seçilen Araç: İHA")
        elif vehicle == "İDA":
            self.logText.append("Seçilen Araç: İDA")

    def initGraphTabUI(self):
        """Grafik UI öğelerini başlatır."""
        layout = QVBoxLayout()

        self.graphSpeed = pg.PlotWidget(title="Hız (km/s)")
        self.speed_plot = self.graphSpeed.plot(pen="r")
        self.graphSpeed.setBackground("w")
        self.graphSpeed.showGrid(x=True, y=True)

        self.graphDepth = pg.PlotWidget(title="Derinlik (m) / İrtifa (m)")
        self.depth_plot = self.graphDepth.plot(pen="b", name="İDA Derinlik")
        self.altitude_plot = self.graphDepth.plot(pen="m", name="İHA İrtifa")
        self.graphDepth.setBackground("w")
        self.graphDepth.showGrid(x=True, y=True)
        self.graphDepth.addLegend()

        layout.addWidget(self.graphSpeed)
        layout.addWidget(self.graphDepth)
        self.graphTab.setLayout(layout)

    def initLogTabUI(self):
        """Log UI öğelerini başlatır."""
        layout = QVBoxLayout()
        self.logText.setStyleSheet("font-family: monospace;")

        self.saveLogBtn = QPushButton("Log Kaydet")
        self.saveLogBtn.setStyleSheet("padding: 5px; border-radius: 5px;")
        self.saveLogBtn.clicked.connect(self.saveLogFile)

        layout.addWidget(self.logText)
        layout.addWidget(self.saveLogBtn)
        self.logTab.setLayout(layout)

    def initSettingsTabUI(self):
        """Ayarlar UI öğelerini başlatır."""
        layout = QVBoxLayout()

        port_layout = QHBoxLayout()

        # Port Ayarları
        port_settings_layout = QFormLayout()
        self.portLabel = QLabel("Port:")
        self.portCombo = QComboBox()
        ports = self.list_serial_ports() or []
        self.portCombo.addItems(sorted(list(set(["COM4", "COM6"] + ports))))
        if "COM4" in self.portCombo.currentText():
            self.portCombo.setCurrentText("COM4")
        port_settings_layout.addRow(self.portLabel, self.portCombo)

        self.baudLabel = QLabel("Baud Hızı:")
        self.baudCombo = QComboBox()
        self.baudCombo.addItems(["9600", "19200", "38400", "57600", "115200", "921600"])
        self.baudCombo.setCurrentText("57600")
        port_settings_layout.addRow(self.baudLabel, self.baudCombo)

        layout.addLayout(port_settings_layout)

        self.connectButton = QPushButton("Bağlan")
        self.connectButton.clicked.connect(self.connectToPixhawk)
        layout.addWidget(self.connectButton)

        layout.addStretch()

        self.settingsTab.setLayout(layout)

    def list_serial_ports(self):
        """Seri portları listeler."""
        ports = serial.tools.list_ports.comports()
        return [port.device for port in ports]

    def connectToPixhawk(self):
        """Pixhawk'a MAVLink bağlantısı kurar."""
        port_name = self.mainPortCombo.currentText()
        baud_rate = int(self.mainBaudCombo.currentText())

        try:
            self.mavlink_connection = mavutil.mavlink_connection(port_name, baud=baud_rate, autoreconnect=True)
            self.mavlink_connection.wait_heartbeat(timeout=5)
            self.is_connected = True
            self.bridge.is_connected = True  # Bağlantı başarılıysa.
            self.logText.append(f"Pixhawk bağlantısı {port_name} ({baud_rate}) üzerinden başarılı.")
            self.updateConnectionStatus()
        except Exception as e:
            self.bridge.is_connected = False  # Bağlantı başarısızsa.
            # Hata mesajını güncelle
            vehicle_type = self.vehicleSelectCombo.currentText()  # Seçilen aracı al
            self.logText.append(f"{vehicle_type} bağlantı hatası: {str(e)}")
            self.is_connected = False
            self.updateConnectionStatus()
            CustomMessageBox("Bağlantı Hatası", f"{vehicle_type} bağlantısı kurulamadı: {str(e)}").exec_()

    def updateConnectionStatus(self):
        """Bağlantı durumu gösterge etiketini günceller."""
        if self.is_connected:
            self.statusIndicator.setText("Bağlantı Durumu: Bağlı")
            self.statusIndicator.setStyleSheet("background-color: #4CAF50; color: white; border-radius: 10px; padding: 5px; font-weight: bold; font-size: 14px;")
        else:
            self.statusIndicator.setText("Bağlantı Durumu: Bağlantısız")
            self.statusIndicator.setStyleSheet("background-color: #F44336; color: white; border-radius: 10px; padding: 5px; font-weight: bold; font-size: 14px;")

    def updateTelemetry(self):
        if not self.is_connected or not self.mavlink_connection:
            return

        max_thrust_kg = 9.0
        pwm_min = 1000
        pwm_max = 2000

        while True:
            msg = self.mavlink_connection.recv_match(blocking=False)
            if msg is None:
                return

            msg_type = msg.get_type()

            if msg_type == "SERVO_OUTPUT_RAW":
                pwm1 = msg.servo1_raw
                pwm2 = msg.servo3_raw
                
                center_pwm = 1500

                thrust1 = max_thrust_kg * (pwm1 - center_pwm) / (pwm_max - pwm_min) * 2
                thrust2 = max_thrust_kg * (pwm2 - center_pwm) / (pwm_max - pwm_min) * 2

                selected_vehicle = self.bridge.getSelectedVehicle()
                if selected_vehicle == "İHA":
                    self.iha_telemetry_values["Motor Left PWM"].setText(f"{pwm1} → {thrust1:.2f} kg")
                    self.iha_telemetry_values["Motor Right PWM"].setText(f"{pwm2} → {thrust2:.2f} kg")
                else:
                    self.ida_telemetry_values["Motor Left PWM"].setText(f"{pwm1} → {thrust1:.2f} kg")
                    self.ida_telemetry_values["Motor Right PWM"].setText(f"{pwm2} → {thrust2:.2f} kg")

            elif msg_type == "GLOBAL_POSITION_INT":
                lat = msg.lat / 1e7
                lon = msg.lon / 1e7
                relative_alt = msg.relative_alt / 1000.0
                vx = msg.vx / 100.0
                vy = msg.vy / 100.0
                heading = msg.hdg / 100.0 if hasattr(msg, "hdg") else 0.0
                speed = (vx**2 + vy**2)**0.5 * 3.6

                selected_vehicle = self.bridge.getSelectedVehicle()
                if selected_vehicle == "İHA":
                    self.iha_telemetry_values["İHA Enlem"].setText(f"{lat:.6f}")
                    self.iha_telemetry_values["İHA Boylam"].setText(f"{lon:.6f}")
                    self.iha_telemetry_values["İHA Hız (km/s)"].setText(f"{speed:.2f}")
                    self.iha_telemetry_values["İHA İrtifa (m)"].setText(f"{relative_alt:.2f}")
                    self.iha_telemetry_values["İHA Heading (°)"].setText(f"{heading:.2f}")
                    self.bridge.updateIhaPosition.emit(lat, lon)
                    self.iha_speed_data.append(speed)
                else:
                    self.ida_telemetry_values["İDA Enlem"].setText(f"{lat:.6f}")
                    self.ida_telemetry_values["İDA Boylam"].setText(f"{lon:.6f}")
                    self.ida_telemetry_values["İDA Hız (km/s)"].setText(f"{speed:.2f}")
                    self.ida_telemetry_values["İDA Derinlik (m)"].setText(f"{msg.alt / 1000.0:.2f}")
                    self.ida_telemetry_values["İDA Heading (°)"].setText(f"{heading:.2f}")
                    self.bridge.updateIdaPosition.emit(lat, lon)
                    self.ida_speed_data.append(speed)

            elif msg_type == "HEARTBEAT":
                mode_mapping = self.mavlink_connection.mode_mapping()
                mode_str = "Bilinmiyor"
                if mode_mapping:
                    for name, code in mode_mapping.items():
                        if code == msg.custom_mode:
                            mode_str = name
                            break
                selected_vehicle = self.bridge.getSelectedVehicle()
                if selected_vehicle == "İHA":
                    self.iha_telemetry_values["İHA Uçuş Modu"].setText(mode_str)
                else:
                    self.ida_telemetry_values["İDA Uçuş Modu"].setText(mode_str)

            elif msg_type == "ATTITUDE":
                roll = degrees(msg.roll)
                pitch = degrees(msg.pitch)
                yaw = degrees(msg.yaw)

                selected_vehicle = self.bridge.getSelectedVehicle()
                if selected_vehicle == "İHA":
                    self.iha_telemetry_values["İHA Roll (°)"].setText(f"{roll:.2f}")
                    self.iha_telemetry_values["İHA Pitch (°)"].setText(f"{pitch:.2f}")
                    self.iha_telemetry_values["İHA Yaw (°)"].setText(f"{yaw:.2f}")
                    self.bridge.rollPitchUpdated.emit(roll, pitch)
                else:
                    self.ida_telemetry_values["İDA Roll (°)"].setText(f"{roll:.2f}")
                    self.ida_telemetry_values["İDA Pitch (°)"].setText(f"{pitch:.2f}")
                    self.ida_telemetry_values["İDA Yaw (°)"].setText(f"{yaw:.2f}")
                    self.bridge.rollPitchUpdated.emit(roll, pitch)

    def updateGraphs(self):
        """Grafikler için çizim verilerini günceller."""
        if self.ida_speed_data:
            self.graphSpeed.setTitle(f"Hız (km/s) - Güncel: {self.ida_speed_data[-1]:.2f}")
            self.speed_plot.setData(list(range(len(self.ida_speed_data))), list(self.ida_speed_data))

        if self.ida_depth_data:
            self.depth_plot.setData(list(range(len(self.ida_depth_data))), list(self.ida_depth_data))
        if self.iha_altitude_data:
            self.altitude_plot.setData(list(range(len(self.iha_altitude_data))), list(self.iha_altitude_data))

    def saveLogFile(self):
        """Log dosyasını kaydeder."""
        file_name, _ = QFileDialog.getSaveFileName(self, "Log Kaydet", "", "Metin Dosyaları (*.txt)")
        if file_name:
            try:
                with open(file_name, 'w', encoding='utf-8') as file:
                    file.write(self.logText.toPlainText())
                    self.logText.append(f"Log dosyası kaydedildi: {file_name}")
            except Exception as e:
                self.logText.append(f"Log dosyası kaydetme hatası: {str(e)}")

    def armVehicle(self):
        """Araca ARM komutu gönderir (İHA için sistem kimliği 1)."""
        if self.mavlink_connection:
            target_system = 1  # İHA
            target_component = 1  # Otopilot için 1
            try:
                self.mavlink_connection.mav.command_long_send(
                    self.mavlink_connection.target_system,
                    self.mavlink_connection.target_component,
                    mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                    0,
                    1, 0, 0, 0, 0, 0, 0  # Param1 = ARM için 1
                )
                self.logText.append("ARM komutu gönderildi.")
            except Exception as e:
                self.logText.append(f"ARM komutu gönderme hatası: {str(e)}")
                CustomMessageBox("Hata", f"ARM komutu gönderilirken hata oluştu: {str(e)}").exec_()
        else:
            self.logText.append("Pixhawk'a bağlı değil.")
            CustomMessageBox("Bağlantı Hatası", "Lütfen önce Pixhawk'a bağlanın.").exec_()

    def disarmVehicle(self):
        """Araca DISARM komutu gönderir (İHA için sistem kimliği 1)."""
        if self.mavlink_connection:
            target_system = 1  # İHA
            target_component = 1  # Otopilot için 1
            try:
                self.mavlink_connection.mav.command_long_send(
                    self.mavlink_connection.target_system,
                    self.mavlink_connection.target_component,
                    mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                    0,
                    0, 0, 0, 0, 0, 0, 0  # Param1 = DISARM için 0
                )
                self.logText.append("DISARM komutu gönderildi.")
            except Exception as e:
                self.logText.append(f"DISARM komutu gönderme hatası: {str(e)}")
                CustomMessageBox("Hata", f"DISARM komutu gönderilirken hata oluştu: {str(e)}").exec_()
        else:
            self.logText.append("Pixhawk'a bağlı değil.")
            CustomMessageBox("Bağlantı Hatası", "Lütfen önce Pixhawk'a bağlanın.").exec_()

    def sendMission(self):
        """Görev gönderme işlevi (örnek olarak kullanılabilir)."""
        if self.mavlink_connection:
            # Buraya görev gönderme mantığını ekleyebilirim.
            self.logText.append("Görev gönderildi.")
        else:
            self.logText.append("Pixhawk'a bağlı değil.")
            CustomMessageBox("Bağlantı Hatası", "Lütfen önce Pixhawk'a bağlanın.").exec_()

    def startMission(self):
        """Görevi başlatma işlevi (örnek olarak kullanılabilir)."""
        if self.mavlink_connection:
            # Buraya görevi başlatma mantığını ekleyebilirim.
            self.logText.append("Görev başlatıldı.")
        else:
            self.logText.append("Pixhawk'a bağlı değil.")
            CustomMessageBox("Bağlantı Hatası", "Lütfen önce Pixhawk'a bağlanın.").exec_()

    def disconnectFromPixhawk(self):
        """Pixhawk bağlantısını keser."""
        if self.mavlink_connection:
            self.mavlink_connection.close()
            self.is_connected = False
            self.bridge.is_connected = False
            self.logText.append("Pixhawk bağlantısı kesildi.")
            self.updateConnectionStatus()

        for label in self.iha_telemetry_values.values():
            label.setText("-")
        for label in self.ida_telemetry_values.values():
            label.setText("-")
            
        self.bridge.rollPitchUpdated.emit(0.0, 0.0)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = YerIstasyonu()
    window.show()
    sys.exit(app.exec_())