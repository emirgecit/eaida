#!/usr/bin/env python3
import sys
import os
import math
import csv
import time
import sqlite3
import threading
from datetime import datetime
from collections import deque
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import List, Optional
from enum import Enum

import serial.tools.list_ports

# --- UBUNTU BEYAZ/SİYAH EKRAN ÇÖZÜMÜ ---
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
os.environ["QT_XCB_FORCE_SOFTWARE_OPENGL"] = "1"

from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QObject, QTimer, QUrl
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QTextEdit, QFormLayout,
    QComboBox, QGroupBox, QLineEdit, QMessageBox, QFrame, QGridLayout, QScrollArea,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtGui import QPainter, QColor, QPen
import pyqtgraph as pg
from pymavlink import mavutil

TILE_SERVER_PORT = 8765
MAX_WAYPOINTS = 4
KILL_SERVO_CHANNEL = 9
KILL_PWM = 2000  # TYR Raporuna göre acil durum servo PWM değeri

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MBTILES_FILE = os.path.join(BASE_DIR, "uydu_harita.mbtiles")

# ==========================================
# KARANLIK TEMA (CATPPUCCIN ESİNTİLİ)
# ==========================================
DARK_THEME = """
QWidget { background-color: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI', Arial; font-size: 12px; }
QPushButton { background-color: #313244; border: 1px solid #45475a; border-radius: 6px; padding: 8px; font-weight: bold; }
QPushButton:hover { background-color: #45475a; }
QPushButton:pressed { background-color: #585b70; }
QComboBox, QLineEdit { background-color: #313244; border: 1px solid #45475a; padding: 6px; border-radius: 4px; color: white; }
QTextEdit { background-color: #181825; border: 1px solid #313244; color: #a6e3a1; font-family: monospace; }
QTableWidget { background-color: #181825; alternate-background-color: #1e1e2e; color: #cdd6f4; gridline-color: #313244; }
QHeaderView::section { background-color: #313244; color: white; border: 1px solid #45475a; padding: 4px; }
QGroupBox { border: 1px solid #45475a; border-radius: 5px; margin-top: 15px; font-weight: bold; color: #89b4fa; padding-top: 15px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; background-color: #1e1e2e; }
QTabWidget::pane { border: 1px solid #45475a; background-color: #1e1e2e; }
QTabBar::tab { background: #181825; border: 1px solid #45475a; padding: 8px 20px; margin-right: 2px; color: #888888; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #1e1e2e; border-bottom-color: #1e1e2e; color: #a6e3a1; font-weight: bold; }
QLabel { background-color: transparent; }
QScrollArea { border: none; background-color: transparent; }
"""

# ==========================================
# EKRAN 2: TEKNOFEST GRAFİK PENCERESİ
# ==========================================
class VideoGraphWindow(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main_app = main_app
        self.setWindowTitle("TEKNOFEST EKRAN 2: Otonomi Grafikleri")
        self.setGeometry(100, 500, 600, 500)
        self.setStyleSheet(DARK_THEME)

        layout = QVBoxLayout()
        pg.setConfigOption("background", "#181825")
        pg.setConfigOption("foreground", "#cdd6f4")

        self.p_speed = pg.PlotWidget(title="Hız (m/s)")
        self.curve_speed_real = self.p_speed.plot(pen=pg.mkPen("#89b4fa", width=2), name="Gerçek Hız")

        self.p_yaw = pg.PlotWidget(title="Gerçek Heading (Yaw) vs Açı İsteği (Setpoint)")
        self.p_yaw.addLegend()
        self.curve_yaw_real = self.p_yaw.plot(pen=pg.mkPen("#a6e3a1", width=2), name="Gerçek Yaw (°)")
        self.curve_yaw_set = self.p_yaw.plot(pen=pg.mkPen("#fab387", width=2, style=Qt.DashLine), name="Yaw İsteği (Set)")

        self.p_pwm = pg.PlotWidget(title="Thrusterlardan Kuvvet İsteği (PWM Sinyali)")
        self.p_pwm.addLegend()
        self.curve_pwm_left = self.p_pwm.plot(pen=pg.mkPen("#f38ba8", width=2), name="Sol Thruster (CH1)")
        self.curve_pwm_right = self.p_pwm.plot(pen=pg.mkPen("#89b4fa", width=2), name="Sağ Thruster (CH3)")

        layout.addWidget(self.p_speed)
        layout.addWidget(self.p_yaw)
        layout.addWidget(self.p_pwm)
        self.setLayout(layout)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(200)

    def update_plots(self):
        if len(self.main_app.hist_speed) > 0:
            self.curve_speed_real.setData(list(self.main_app.hist_speed))
            self.curve_yaw_real.setData(list(self.main_app.hist_yaw))
            self.curve_yaw_set.setData(list(self.main_app.hist_yaw_sp))
            self.curve_pwm_left.setData(list(self.main_app.hist_m1))
            self.curve_pwm_right.setData(list(self.main_app.hist_m3))

# ==========================================
# MBTILES YEREL SUNUCU SİSTEMİ
# ==========================================
class MBTilesManager:
    def __init__(self, mbtiles_path: str = MBTILES_FILE):
        self.mbtiles_path = Path(mbtiles_path)
        self.conn = None
        self.has_offline_map = False
        if self.mbtiles_path.exists():
            try:
                self.conn = sqlite3.connect(str(self.mbtiles_path), check_same_thread=False)
                self.has_offline_map = True
            except Exception:
                self.has_offline_map = False

    def get_tile(self, zoom: int, x: int, y: int) -> Optional[bytes]:
        if not self.conn: return None
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?", (zoom, x, y))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        except Exception:
            return None

    def close(self):
        if self.conn: self.conn.close()

class OfflineTileHandler(BaseHTTPRequestHandler):
    mbtiles_manager = None
    def do_GET(self):
        if self.path.startswith("/tile/"):
            try:
                parts = self.path.split("/")
                z, x, y = int(parts[2]), int(parts[3]), int(parts[4])
                tile_data = self.mbtiles_manager.get_tile(z, x, y)
                if tile_data:
                    self.send_response(200)
                    self.send_header("Content-type", "image/png")
                    self.send_header("Content-Length", str(len(tile_data)))
                    self.send_header("Cache-Control", "max-age=2592000")
                    self.end_headers()
                    self.wfile.write(tile_data)
                else:
                    self.send_response(404)
                    self.end_headers()
            except Exception:
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *args):
        pass

class OfflineTileServer(threading.Thread):
    def __init__(self, mbtiles_manager, port: int = TILE_SERVER_PORT):
        super().__init__(daemon=True)
        OfflineTileHandler.mbtiles_manager = mbtiles_manager
        self.port = port
    def run(self):
        server = HTTPServer(("127.0.0.1", self.port), OfflineTileHandler)
        server.serve_forever()

# ==========================================
# ÇEVRİMDIŞI İKONLU HTML ŞABLONU
# ==========================================
MAP_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    html, body, #map {{ height: 100%; width: 100%; margin: 0; padding: 0; background-color: #1e1e2e; }}
    .info-panel {{ background: rgba(30, 30, 30, 0.95); color: #cdd6f4; padding: 10px; border-radius: 5px; border: 2px solid #89b4fa; font-size: 11px; }}
    .badge {{ position:fixed; left:10px; bottom:10px; background:#a6e3a1; color:#11111b; padding:6px 10px; border-radius:4px; font-size:11px; z-index:9999; font-weight:bold; }}
    
    .leaflet-tile-container img {{ outline: 1px solid transparent; -webkit-backface-visibility: hidden; }}
    .custom-cone-marker {{ background: transparent !important; border: none !important; }}
  </style>
  <link rel="stylesheet" href="file://{leaflet_css}" />
  <script src="file://{leaflet_js}"></script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
</head>
<body>
<div id="map"></div>
<div class="badge">MBTILES OFFLINE</div>

<script>
    const TILE_URL = "{tile_url}";
    const ENABLE_TILES = {enable_tiles};
    const centerLat = 40.9985368;
    const centerLon = 39.768233; 
    
    const bounds = L.latLngBounds([centerLat - 0.050, centerLon - 0.065], [centerLat + 0.050, centerLon + 0.065]);

    let map = L.map('map', {{
        center: [centerLat, centerLon],
        zoom: 17, minZoom: 14, maxZoom: 23, maxBounds: bounds, maxBoundsViscosity: 1.0,
        zoomSnap: 1, zoomDelta: 1, wheelPxPerZoomLevel: 60
    }});

    if (ENABLE_TILES) {{
      L.tileLayer(TILE_URL, {{ 
          minZoom: 14, maxZoom: 23, maxNativeZoom: 19, tms: true, bounds: bounds,
          updateWhenIdle: false, updateWhenZooming: true
      }}).addTo(map);
    }} else {{
      const err = L.control({{position:'topright'}});
      err.onAdd = function() {{
        const div = L.DomUtil.create('div', 'info-panel');
        div.innerHTML = "<b>MBTiles yok</b><br>Harita kapalı, telemetri çalışır.";
        return div;
      }}
      err.addTo(map);
    }}

    let bridge = null;
    let idaMarker = null, idaPath = [], idaPolyline = L.polyline([], {{color:'#89b4fa', weight:3}}).addTo(map);
    let ihaMarker = null, ihaPath = [], ihaPolyline = L.polyline([], {{color:'#f38ba8', weight:2}}).addTo(map);
    let ihaTarget = null;
    let waypointMarkers = [], waypointPolyline = null, missionLayer = L.layerGroup().addTo(map);

    let selectedVehicle = "İDA";
    window.followMode = true;

    new QWebChannel(qt.webChannelTransport, function(channel) {{ bridge = channel.objects.bridge; }});

    map.on('click', function(e) {{
        let lat = e.latlng.lat; let lon = e.latlng.lng;
        if (selectedVehicle === "İDA") {{
            if (waypointMarkers.length < {max_wps}) {{ window.addMissionWaypoint(lat, lon); }} 
            else {{ alert("Maksimum {max_wps} nokta seçilebilir!"); }}
        }} else {{
            if (ihaTarget) {{ ihaTarget.setLatLng([lat, lon]); }} 
            else {{ ihaTarget = L.circleMarker([lat, lon], {{color:'#f38ba8', radius:7, fillOpacity:0.8}}).addTo(map); }}
            ihaTarget.bindPopup("İHA Hedefi").openPopup();
            if (bridge) bridge.mapClicked(lat, lon, selectedVehicle);
        }}
    }});

    window.setFollowMode = function(on) {{ window.followMode = !!on; }};

    window.centerMap = function() {{
        if (idaMarker) map.setView(idaMarker.getLatLng(), 18);
        else map.setView([centerLat, centerLon], 18);
    }};

    window.setVehicle = function(vehicle) {{ selectedVehicle = vehicle; }};

    window.addWaypointFromGCS = function(lat, lon) {{ window.addMissionWaypoint(lat, lon); }};

    window.addMissionWaypoint = function(lat, lon) {{
        let idx = waypointMarkers.length;
        let marker = L.marker([lat, lon], {{
            icon: L.divIcon({{
                html: '<div style="background-color:#fab387;color:#11111b;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:bold;border:2px solid #11111b;">' + (idx + 1) + '</div>',
                iconSize: [28, 28], iconAnchor: [14, 14]
            }})
        }}).bindPopup('<div class="info-panel"><b>WP ' + (idx + 1) + '</b><br>Lat: ' + lat.toFixed(6) + '<br>Lon: ' + lon.toFixed(6) + '</div>');

        marker.addTo(missionLayer);
        waypointMarkers.push({{marker: marker, lat: lat, lon: lon}});
        if (bridge) bridge.mapClicked(lat, lon, "İDA");
        window.updateMissionLine();
    }};

    window.updateMissionLine = function() {{
        if (waypointPolyline) missionLayer.removeLayer(waypointPolyline);
        let coords = waypointMarkers.map(w => [w.lat, w.lon]);
        waypointPolyline = L.polyline(coords, {{color:'#fab387', weight:2, dashArray:'5, 5'}});
        waypointPolyline.addTo(missionLayer);
    }};

    window.clearMission = function() {{
        waypointMarkers.forEach(w => missionLayer.removeLayer(w.marker));
        waypointMarkers = [];
        if (waypointPolyline) missionLayer.removeLayer(waypointPolyline);
        if (ihaTarget) {{ map.removeLayer(ihaTarget); ihaTarget = null; }}
    }};

    function getVehicleIcon(vehicle, heading) {{
        let color = vehicle === 'İDA' ? '#89b4fa' : '#f38ba8';
        let rotId = vehicle === 'İDA' ? 'ida-rot' : 'iha-rot';
        let html = '<div id="' + rotId + '" style="width: 120px; height: 120px; transform: rotate(' + heading + 'deg); transform-origin: center center; transition: transform 0.15s linear;">' +
                   '<svg width="120" height="120" viewBox="0 0 120 120">' +
                   '  <path d="M 60 60 L 32.5 12.4 A 55 55 0 0 1 87.5 12.4 Z" fill="' + color + '" fill-opacity="0.35" />' +
                   '  <circle cx="60" cy="60" r="8" fill="' + color + '" stroke="#11111b" stroke-width="2" />' +
                   '</svg></div>';
        return L.divIcon({{ className: 'custom-cone-marker', html: html, iconSize: [120, 120], iconAnchor: [60, 60] }});
    }}

    window.updateIdaPosition = function(lat, lon, heading, speed, mode) {{
        if (!idaMarker) {{
            idaMarker = L.marker([lat, lon], {{icon: getVehicleIcon('İDA', heading), zIndexOffset: 1000}}).addTo(map);
        }} else {{
            idaMarker.setLatLng([lat, lon]);
            let rotDiv = document.getElementById('ida-rot');
            if (rotDiv) rotDiv.style.transform = 'rotate(' + heading + 'deg)';
            else idaMarker.setIcon(getVehicleIcon('İDA', heading));
        }}
        idaPath.push([lat, lon]);
        if (idaPath.length > 500) idaPath.shift();
        idaPolyline.setLatLngs(idaPath);
        if (window.followMode) map.setView([lat, lon], map.getZoom());
    }};

    window.updateIhaPosition = function(lat, lon, heading, altitude, mode) {{
        if (!ihaMarker) {{
            ihaMarker = L.marker([lat, lon], {{icon: getVehicleIcon('İHA', heading), zIndexOffset: 900}}).addTo(map);
        }} else {{
            ihaMarker.setLatLng([lat, lon]);
            let rotDiv = document.getElementById('iha-rot');
            if (rotDiv) rotDiv.style.transform = 'rotate(' + heading + 'deg)';
            else ihaMarker.setIcon(getVehicleIcon('İHA', heading));
        }}
    }};
</script>
</body>
</html>
"""

class MapBridge(QObject):
    waypoint_signal = pyqtSignal(float, float, str)
    @pyqtSlot(float, float, str)
    def mapClicked(self, lat, lon, vehicle):
        self.waypoint_signal.emit(lat, lon, vehicle)

class VehicleMode(Enum):
    MANUAL = 0
    GUIDED = 4
    AUTO = 10
    RTL = 11
    HOLD = 16

class VehicleThread(QThread):
    connected_signal = pyqtSignal(bool)
    data_signal = pyqtSignal(float, float, float, float, float, float, float, str, str, float, float)
    health_signal = pyqtSignal(float, int, int, int)
    log_signal = pyqtSignal(str)
    statustext_signal = pyqtSignal(str, str)
    servo_signal = pyqtSignal(int, int)

    def __init__(self, vehicle_type: str):
        super().__init__()
        self.vehicle_type = vehicle_type
        self.master = None
        self.is_running = False
        self.pause_reading = False

    def connect_vehicle(self, connection_string, baudrate):
        try:
            if connection_string.startswith(("udp:", "tcp:", "udpin:")):
                self.master = mavutil.mavlink_connection(connection_string)
            else:
                self.master = mavutil.mavlink_connection(connection_string, baud=baudrate, timeout=1.0)
            self.master.wait_heartbeat(timeout=5)
            self.is_running = True
            self.master.mav.request_data_stream_send(
                self.master.target_system, self.master.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1
            )
            self.connected_signal.emit(True)
            self.log_signal.emit(f"[{self.vehicle_type}] Bağlantı Başarılı: {connection_string}")
            self.start()
        except Exception as e:
            self.is_running = False
            self.connected_signal.emit(False)
            self.log_signal.emit(f"[{self.vehicle_type}] Bağlantı Hatası: {e}")

    def run(self):
        lat = lon = speed = alt = roll = pitch = yaw = 0.0
        yon_sp = wp_dist = 0.0
        mode = "BİLİNMİYOR"
        arm_status = "DISARM"
        voltaj = batarya_yuzde = gps_fix = uydu = 0
        last_emit_time = 0.0

        while self.is_running and self.master:
            try:
                if self.pause_reading:
                    time.sleep(0.05)
                    continue

                msg = self.master.recv_match(
                    type=["GLOBAL_POSITION_INT", "VFR_HUD", "ATTITUDE", "HEARTBEAT",
                          "NAV_CONTROLLER_OUTPUT", "SYS_STATUS", "GPS_RAW_INT",
                          "STATUSTEXT", "SERVO_OUTPUT_RAW"],
                    blocking=False, timeout=0.1
                )
                if not msg:
                    time.sleep(0.01)
                    continue

                mt = msg.get_type()
                if mt == "GLOBAL_POSITION_INT":
                    lat, lon = msg.lat / 1e7, msg.lon / 1e7
                    alt = msg.relative_alt / 1000.0
                elif mt == "VFR_HUD":
                    speed = msg.groundspeed
                elif mt == "ATTITUDE":
                    roll, pitch, yaw = math.degrees(msg.roll), math.degrees(msg.pitch), math.degrees(msg.yaw)
                    if yaw < 0: yaw += 360
                elif mt == "NAV_CONTROLLER_OUTPUT":
                    yon_sp = msg.target_bearing
                    wp_dist = msg.wp_dist
                elif mt == "HEARTBEAT":
                    mode = mavutil.mode_string_v10(msg)
                    arm_status = "ARM" if (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) else "DISARM"
                elif mt == "GPS_RAW_INT":
                    gps_fix, uydu = msg.fix_type, msg.satellites_visible
                elif mt == "SYS_STATUS":
                    voltaj = msg.voltage_battery / 1000.0
                    batarya_yuzde = msg.battery_remaining
                    self.health_signal.emit(voltaj, batarya_yuzde, gps_fix, uydu)
                elif mt == "SERVO_OUTPUT_RAW":
                    self.servo_signal.emit(msg.servo1_raw, msg.servo3_raw)
                elif mt == "STATUSTEXT":
                    try: text = msg.text.decode("utf-8", errors="ignore").upper()
                    except: text = str(getattr(msg, "text", "")).upper()
                    if "COLOR" in text or "RENK" in text or "DIST" in text or "TEMAS" in text:
                        self.statustext_signal.emit(self.vehicle_type, text)

                now = time.time()
                if now - last_emit_time >= 0.1:
                    if lat != 0.0 and lon != 0.0: # Null Island filtresi
                        self.data_signal.emit(lat, lon, speed, alt, roll, pitch, yaw, mode, arm_status, yon_sp, wp_dist)
                    last_emit_time = now

            except Exception:
                pass

    def send_command(self, cmd_id, param1=0, param2=0, param3=0, param4=0, param5=0, param6=0, param7=0):
        if self.master:
            self.master.mav.command_long_send(
                self.master.target_system, self.master.target_component,
                cmd_id, 0, param1, param2, param3, param4, param5, param6, param7
            )
            self.log_signal.emit(f"[{self.vehicle_type}] Komut gönderildi: {cmd_id}")

    def set_mode(self, mode: VehicleMode):
        if self.master:
            self.master.mav.set_mode_send(self.master.target_system, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode.value)

    def restart_mission_and_auto(self):
        if self.master:
            try:
                self.master.mav.mission_set_current_send(self.master.target_system, self.master.target_component, 1)
                self.set_mode(VehicleMode.AUTO)
                self.log_signal.emit(f"[{self.vehicle_type}] Görev 1. noktadan başlatıldı (AUTO)!")
            except Exception as e:
                self.log_signal.emit(f"[{self.vehicle_type}] Görev başlatma hatası: {e}")

    def trigger_kamikaze(self, pwm_value: int):
        if self.master:
            try:
                self.master.mav.command_long_send(
                    self.master.target_system, self.master.target_component,
                    mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0, KILL_SERVO_CHANNEL, pwm_value, 0, 0, 0, 0, 0
                )
                self.log_signal.emit(f"[{self.vehicle_type}] KAMIKAZE: CH{KILL_SERVO_CHANNEL} -> {pwm_value}")
            except Exception as e:
                self.log_signal.emit(f"[{self.vehicle_type}] Kamikaze hatası: {e}")

    def kill_power(self):
        if self.master:
            try:
                # TYR Raporu uyarınca güç kesici servo motoru (2000 PWM) ile tetiklenir
                self.master.mav.command_long_send(
                    self.master.target_system, self.master.target_component,
                    mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0, KILL_SERVO_CHANNEL, KILL_PWM, 0, 0, 0, 0, 0
                )
                self.log_signal.emit(f"[{self.vehicle_type}] 🛑 ACİL DURUM TETİKLENDİ - GÜÇ KESİLİYOR")
            except Exception as e:
                self.log_signal.emit(f"[{self.vehicle_type}] KILL hatası: {e}")

    def upload_mission_thread_safe(self, waypoints, auto_rtl=True):
        def task():
            if not self.master: return
            self.pause_reading = True
            time.sleep(0.2)
            try:
                try:
                    self.master.mav.mission_clear_all_send(self.master.target_system, self.master.target_component)
                    self.master.recv_match(type="MISSION_ACK", blocking=True, timeout=1.0)
                except: pass

                # ArduPilot için sıralama mantığı: 0. Sıra Dummy (Home), 1...N gerçek WP'ler
                upload_list = [waypoints[0]] + waypoints
                if auto_rtl:
                    upload_list.append("RTL")

                mission_count = len(upload_list)
                self.master.mav.mission_count_send(self.master.target_system, self.master.target_component, mission_count)
                
                sent = set()
                start_t = time.time()

                while len(sent) < mission_count and (time.time() - start_t) < 15.0:
                    req = self.master.recv_match(type=["MISSION_REQUEST_INT", "MISSION_REQUEST"], blocking=True, timeout=2.0)
                    if not req:
                        continue # Ufak kopmalarda break yerine beklemeye devam et
                    
                    seq = int(req.seq)
                    if seq in sent or seq >= mission_count:
                        continue

                    item = upload_list[seq]

                    if item == "RTL":
                        self.master.mav.mission_item_int_send(
                            self.master.target_system, self.master.target_component, seq,
                            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT, mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
                            0, 1, 0, 0, 0, 0, 0, 0, 0
                        )
                    else:
                        lat, lon = item
                        self.master.mav.mission_item_int_send(
                            self.master.target_system, self.master.target_component, seq,
                            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT, mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                            0, 1, 0, 1.5, 0, 0, int(lat * 1e7), int(lon * 1e7), 10
                        )
                    sent.add(seq)

                if len(sent) == mission_count:
                    ack = self.master.recv_match(type="MISSION_ACK", blocking=True, timeout=3.0)
                    if ack and int(ack.type) == int(mavutil.mavlink.MAV_MISSION_ACCEPTED):
                        rtl_msg = " + RTL" if auto_rtl else ""
                        self.log_signal.emit(f"[{self.vehicle_type}] ✅ Görev Yüklendi ({len(waypoints)} WP{rtl_msg})")
                    else:
                        self.log_signal.emit(f"[{self.vehicle_type}] ⚠️ Görev iletildi ancak Onay (ACK) alınamadı!")
                else:
                    self.log_signal.emit(f"[{self.vehicle_type}] ❌ Görev yükleme başarısız! (Bağlantı zayıf)")

            except Exception as e:
                self.log_signal.emit(f"[{self.vehicle_type}] Görev yükleme hatası: {e}")
            finally:
                self.pause_reading = False

        threading.Thread(target=task, daemon=True).start()

    def disconnect(self):
        self.is_running = False
        if self.master:
            try: self.master.close()
            except: pass
        self.connected_signal.emit(False)


class HorizonIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.roll = 0.0
        self.pitch = 0.0
        self.setMinimumSize(180, 140)
        self.setStyleSheet("background-color: #181825; border: 1px solid #45475a; border-radius: 4px;")

    def update_attitude(self, roll, pitch):
        self.roll, self.pitch = roll, pitch
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.save()
        painter.translate(w / 2, h / 2)
        painter.rotate(-self.roll)
        pitch_scale = 4.0
        painter.translate(0, self.pitch * pitch_scale)

        painter.fillRect(int(-w * 1.5), int(-h * 2), int(w * 3), int(h * 2), QColor(137, 180, 250)) # Mavi Gökyüzü
        painter.fillRect(int(-w * 1.5), 0, int(w * 3), int(h * 2), QColor(166, 227, 161)) # Yeşil Yer
        
        painter.setPen(QPen(Qt.white, 2))
        painter.drawLine(int(-w), 0, int(w), 0)
        painter.restore()

        painter.setPen(QPen(QColor(243, 139, 168), 2)) # Kırmızı Merkez
        painter.drawLine(int(w / 2 - 25), int(h / 2), int(w / 2 - 8), int(h / 2))
        painter.drawLine(int(w / 2 + 8), int(h / 2), int(w / 2 + 25), int(h / 2))
        painter.drawLine(int(w / 2), int(h / 2 - 4), int(w / 2), int(h / 2 + 4))

        painter.setPen(Qt.white)
        painter.drawText(5, 15, f"R:{self.roll:.1f}°")
        painter.drawText(5, 30, f"P:{self.pitch:.1f}°")


class YerKontrolIstasyonu(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KTÜ UZAY YGM - 2026 TEKNOFEST YKİ (ŞAMPİYON SÜRÜM)")
        self.setGeometry(30, 30, 1800, 1000)
        self.setStyleSheet(DARK_THEME)

        self.hist_speed = deque(maxlen=200); self.hist_speed_sp = deque(maxlen=200)
        self.hist_yaw = deque(maxlen=200); self.hist_yaw_sp = deque(maxlen=200)
        self.hist_m1 = deque(maxlen=200); self.hist_m3 = deque(maxlen=200)
        self.hist_roll = deque(maxlen=200); self.hist_pitch = deque(maxlen=200)

        # Çevrimdışı harita sunucusu başlat
        self.mbtiles_manager = MBTilesManager(MBTILES_FILE)
        self.tile_server = None
        if self.mbtiles_manager.has_offline_map:
            self.tile_server = OfflineTileServer(self.mbtiles_manager, port=TILE_SERVER_PORT)
            self.tile_server.start()

        self.ida_thread = VehicleThread("İDA")
        self.iha_thread = VehicleThread("İHA")

        self.ida_thread.log_signal.connect(self.log_yazdir)
        self.iha_thread.log_signal.connect(self.log_yazdir)
        self.ida_thread.statustext_signal.connect(self.statustext_isle)
        self.iha_thread.statustext_signal.connect(self.statustext_isle)
        self.ida_thread.servo_signal.connect(self.ida_servo_guncelle)
        
        self.ida_thread.data_signal.connect(self.ida_guncelle)
        self.ida_thread.health_signal.connect(self.ida_saglik_guncelle)
        self.iha_thread.data_signal.connect(self.iha_guncelle)
        self.iha_thread.health_signal.connect(self.iha_saglik_guncelle)

        self.ida_thread.connected_signal.connect(lambda ok: self.update_link_status("İDA", ok))
        self.iha_thread.connected_signal.connect(lambda ok: self.update_link_status("İHA", ok))
        self._ida_ok = False; self._iha_ok = False

        self.kamikaze_aktif = False
        self.ida_mission_waypoints = []

        self.is_logging = False
        self.csv_filename = ""
        self.graph_window = None
        self.follow_mode = True

        self.last_map_js_ida = 0.0; self.last_map_js_iha = 0.0

        self.initUI()
        self.graph_timer = QTimer()
        self.graph_timer.timeout.connect(self.update_graphs)
        self.graph_timer.start(500)

    def get_serial_ports(self):
        return [port.device for port in serial.tools.list_ports.comports()]

    def initUI(self):
        self.tabs = QTabWidget()
        self.connTab = QWidget(); self.mainTab = QWidget()
        self.graphTab = QWidget()

        self.tabs.addTab(self.connTab, "1. Bağlantı Ayarları")
        self.tabs.addTab(self.mainTab, "2. Operasyon Merkezi")
        self.tabs.addTab(self.graphTab, "3. Mühendislik Analizi ve Loglar")

        self.build_conn_tab()
        self.build_main_tab()
        self.build_graph_tab()

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.tabs)
        self.setLayout(layout)

    def build_conn_tab(self):
        layout = QVBoxLayout()
        available_ports = self.get_serial_ports()

        ida_group = QGroupBox("İDA Telemetri Bağlantısı - 868 MHz")
        ida_layout = QFormLayout()
        self.ida_port = QComboBox(); self.ida_port.setEditable(True)
        self.ida_port.addItems(["udp:127.0.0.1:14550", "tcp:127.0.0.1:5760"] + available_ports)
        self.ida_port.setCurrentText("udp:127.0.0.1:14550")
        self.ida_baud = QComboBox(); self.ida_baud.addItems(["57600", "115200", "921600"])
        self.ida_conn_btn = QPushButton("İDA'ya Bağlan")
        self.ida_conn_btn.setStyleSheet("background-color: #89b4fa; color: #11111b;")
        self.ida_conn_btn.clicked.connect(lambda: self.ida_thread.connect_vehicle(self.ida_port.currentText(), int(self.ida_baud.currentText())))
        ida_layout.addRow("Port/Bağlantı:", self.ida_port); ida_layout.addRow("Baud Rate:", self.ida_baud); ida_layout.addRow("", self.ida_conn_btn)
        ida_group.setLayout(ida_layout)

        iha_group = QGroupBox("İHA Telemetri Bağlantısı - 433 MHz")
        iha_layout = QFormLayout()
        self.iha_port = QComboBox(); self.iha_port.setEditable(True)
        self.iha_port.addItems(["udp:127.0.0.1:14552", "tcp:127.0.0.1:5762"] + available_ports)
        self.iha_port.setCurrentText("udp:127.0.0.1:14552")
        self.iha_baud = QComboBox(); self.iha_baud.addItems(["57600", "115200", "921600"])
        self.iha_conn_btn = QPushButton("İHA'ya Bağlan")
        self.iha_conn_btn.setStyleSheet("background-color: #89b4fa; color: #11111b;")
        self.iha_conn_btn.clicked.connect(lambda: self.iha_thread.connect_vehicle(self.iha_port.currentText(), int(self.iha_baud.currentText())))
        iha_layout.addRow("Port/Bağlantı:", self.iha_port); iha_layout.addRow("Baud Rate:", self.iha_baud); iha_layout.addRow("", self.iha_conn_btn)
        iha_group.setLayout(iha_layout)

        param_group = QGroupBox("Ağ ve Şifreleme Ayarları (NetID)")
        param_layout = QHBoxLayout()
        self.param_spin = QLineEdit("25")
        self.param_btn = QPushButton("Kanalı (NetID) İDA'ya Yükle")
        self.param_btn.clicked.connect(self.update_telemetry_channel)
        param_layout.addWidget(QLabel("Ağ Kimliği (NetID):")); param_layout.addWidget(self.param_spin); param_layout.addWidget(self.param_btn)
        param_group.setLayout(param_layout)

        self.lbl_link_status = QLabel("Link Durumu: İDA ❌ | İHA ❌")
        self.lbl_link_status.setStyleSheet("font-size: 14px; font-weight: bold; margin-top: 10px; color: #f38ba8;")

        layout.addWidget(ida_group); layout.addWidget(iha_group); layout.addWidget(param_group); layout.addWidget(self.lbl_link_status); layout.addStretch()
        self.connTab.setLayout(layout)

    def update_link_status(self, who, ok):
        if who == "İDA": self._ida_ok = ok
        else: self._iha_ok = ok
        color = "#a6e3a1" if (self._ida_ok or self._iha_ok) else "#f38ba8"
        self.lbl_link_status.setStyleSheet(f"font-size: 14px; font-weight: bold; margin-top: 10px; color: {color};")
        self.lbl_link_status.setText(f"Link Durumu: İDA {'✅' if self._ida_ok else '❌'} | İHA {'✅' if self._iha_ok else '❌'}")

    def update_telemetry_channel(self):
        try:
            netid = float(self.param_spin.text())
            if self.ida_thread.master:
                self.ida_thread.master.mav.param_set_send(
                    self.ida_thread.master.target_system, self.ida_thread.master.target_component,
                    b"BRD_RADIO_NETID", netid, mavutil.mavlink.MAV_PARAM_TYPE_REAL32
                )
                self.log_yazdir(f"[GÜVENLİK] Kanal (NetID) = {int(netid)} İsteği Gönderildi.")
            else:
                self.log_yazdir("Hata: Önce İDA'ya bağlanmalısınız.")
        except Exception as e:
            self.log_yazdir(f"Parametre Hatası: {e}")

    def build_main_tab(self):
        main_h_layout = QHBoxLayout()
        main_h_layout.setContentsMargins(5, 5, 5, 5)

        sidebar_widget = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(10)

        telemetry_layout = QHBoxLayout()
        ida_frame = QFrame()
        ida_frame.setStyleSheet("background-color: #181825; border: 1px solid #45475a; border-radius: 6px; padding: 6px;")
        ida_l = QVBoxLayout(ida_frame)
        self.lbl_ida_nav = QLabel("<span style='color:#a6e3a1; font-weight:bold; font-size:14px;'>[İDA] NAVİGASYON</span><br><br><span style='color:#888888;'>Bekleniyor...</span>")
        self.lbl_ida_health = QLabel("<span style='color:#888888;'>Batarya: --%<br>GPS: --</span>")
        line_ida = QFrame(); line_ida.setFrameShape(QFrame.HLine); line_ida.setStyleSheet("color: #45475a;")
        ida_l.addWidget(self.lbl_ida_nav); ida_l.addWidget(line_ida); ida_l.addWidget(self.lbl_ida_health)

        iha_frame = QFrame()
        iha_frame.setStyleSheet("background-color: #181825; border: 1px solid #45475a; border-radius: 6px; padding: 6px;")
        iha_l = QVBoxLayout(iha_frame)
        self.lbl_iha_nav = QLabel("<span style='color:#89b4fa; font-weight:bold; font-size:14px;'>[İHA] NAVİGASYON</span><br><br><span style='color:#888888;'>Bekleniyor...</span>")
        self.lbl_iha_health = QLabel("<span style='color:#888888;'>Batarya: --%<br>GPS: --</span>")
        line_iha = QFrame(); line_iha.setFrameShape(QFrame.HLine); line_iha.setStyleSheet("color: #45475a;")
        iha_l.addWidget(self.lbl_iha_nav); iha_l.addWidget(line_iha); iha_l.addWidget(self.lbl_iha_health)

        telemetry_layout.addWidget(ida_frame); telemetry_layout.addWidget(iha_frame)
        sidebar_layout.addLayout(telemetry_layout)

        pfd_layout = QHBoxLayout()
        self.pfd = HorizonIndicator()
        pfd_layout.addWidget(self.pfd)
        sidebar_layout.addLayout(pfd_layout)

        kontrol_g = QGroupBox("Otonomi ve Komutlar")
        kontrol_l = QVBoxLayout()
        
        ara_kutu = QHBoxLayout()
        ara_kutu.addWidget(QLabel("Harita İmleci:"))
        self.harita_arac_secim = QComboBox()
        self.harita_arac_secim.addItems(["İDA", "İHA"])
        self.harita_arac_secim.currentTextChanged.connect(lambda txt: self.map_view.page().runJavaScript(f"if(typeof setVehicle !== 'undefined') setVehicle('{txt}')"))
        ara_kutu.addWidget(self.harita_arac_secim)
        self.lbl_gorev_durum = QLabel(f"Nokta: <b style='color:#a6e3a1; font-size:14px;'>0/{MAX_WAYPOINTS}</b>")
        ara_kutu.addWidget(self.lbl_gorev_durum)
        self.btn_temizle = QPushButton("Haritayı Temizle")
        self.btn_temizle.clicked.connect(self.haritayi_temizle)
        ara_kutu.addWidget(self.btn_temizle)
        kontrol_l.addLayout(ara_kutu)

        arm_layout = QGridLayout()
        btn_ida_arm = QPushButton("İDA ARM")
        btn_ida_arm.setStyleSheet("background-color: #a6e3a1; color: #11111b;")
        btn_ida_arm.clicked.connect(lambda: self.ida_thread.send_command(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 1, 21196))
        btn_ida_disarm = QPushButton("İDA DISARM")
        btn_ida_disarm.setStyleSheet("background-color: #f38ba8; color: #11111b;")
        btn_ida_disarm.clicked.connect(lambda: self.ida_thread.send_command(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 21196))
        btn_ida_manual = QPushButton("İDA DUR (MANUAL)")
        btn_ida_manual.setStyleSheet("background-color: #fab387; color: #11111b;")
        btn_ida_manual.clicked.connect(lambda: self.ida_thread.set_mode(VehicleMode.MANUAL))
        btn_ida_rtl = QPushButton("İDA EVE DÖN (RTL)")
        btn_ida_rtl.setStyleSheet("background-color: #f9e2af; color: #11111b;")
        btn_ida_rtl.clicked.connect(lambda: self.ida_thread.set_mode(VehicleMode.RTL))
        
        arm_layout.addWidget(btn_ida_arm, 0, 0); arm_layout.addWidget(btn_ida_disarm, 0, 1)
        arm_layout.addWidget(btn_ida_manual, 1, 0); arm_layout.addWidget(btn_ida_rtl, 1, 1)
        kontrol_l.addLayout(arm_layout)

        self.cb_auto_rtl = QCheckBox("Görev Sonu EVE DÖN (Otomatik RTL)")
        self.cb_auto_rtl.setChecked(True)
        kontrol_l.addWidget(self.cb_auto_rtl)

        self.btn_gorev_gonder = QPushButton("GÖREVİ YÜKLE")
        self.btn_gorev_gonder.setStyleSheet("background-color: #89b4fa; color: #11111b; font-size:13px;")
        self.btn_gorev_gonder.clicked.connect(self.gorevi_pixhawka_yukle)
        kontrol_l.addWidget(self.btn_gorev_gonder)

        self.btn_auto = QPushButton("GÖREVİ BAŞLAT (AUTO)")
        self.btn_auto.setStyleSheet("background-color: #cba6f7; color: #11111b; font-size:13px;")
        self.btn_auto.clicked.connect(self.ida_thread.restart_mission_and_auto)
        kontrol_l.addWidget(self.btn_auto)

        kontrol_g.setLayout(kontrol_l)
        sidebar_layout.addWidget(kontrol_g)

        # Tablo Alanı
        self.waypointTable = QTableWidget(0, 3)
        self.waypointTable.setHorizontalHeaderLabels(["Araç", "Enlem", "Boylam"])
        self.waypointTable.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.waypointTable.setMaximumHeight(150)
        sidebar_layout.addWidget(self.waypointTable)

        kami_g = QGroupBox("Kamikaze Paneli (Manuel/Otomatik)")
        kami_g.setStyleSheet("QGroupBox { border: 1px solid #f38ba8; } QGroupBox::title { color: #f38ba8; }")
        kami_l = QVBoxLayout()
        self.lbl_kami_durum = QLabel("<span style='color:#888888;'>Durum: İHA Hedefi Bekleniyor...</span>")
        self.lbl_kami_mesafe = QLabel("<span style='color:#888888;'>Mesafe: --</span>")
        kami_l.addWidget(self.lbl_kami_durum); kami_l.addWidget(self.lbl_kami_mesafe)

        yedek_layout = QHBoxLayout()
        self.btn_sim_kirmizi = QPushButton("Kırmızı"); self.btn_sim_kirmizi.setStyleSheet("background-color: #f38ba8; color: #11111b; border:none;")
        self.btn_sim_kirmizi.clicked.connect(lambda: self.ida_thread.trigger_kamikaze(1000))
        self.btn_sim_mavi = QPushButton("Mavi"); self.btn_sim_mavi.setStyleSheet("background-color: #89b4fa; color: #11111b; border:none;")
        self.btn_sim_mavi.clicked.connect(lambda: self.ida_thread.trigger_kamikaze(1500))
        self.btn_sim_yesil = QPushButton("Yeşil"); self.btn_sim_yesil.setStyleSheet("background-color: #a6e3a1; color: #11111b; border:none;")
        self.btn_sim_yesil.clicked.connect(lambda: self.ida_thread.trigger_kamikaze(2000))
        yedek_layout.addWidget(self.btn_sim_kirmizi); yedek_layout.addWidget(self.btn_sim_mavi); yedek_layout.addWidget(self.btn_sim_yesil)
        kami_l.addLayout(yedek_layout)
        
        self.btn_acil = QPushButton("GÜÇ KESME RÖLESİ (ACİL)")
        self.btn_acil.setStyleSheet("background-color: #eba0ac; color: #11111b; margin-top: 10px;")
        self.btn_acil.clicked.connect(self.ida_thread.kill_power)
        kami_l.addWidget(self.btn_acil)
        kami_g.setLayout(kami_l)
        sidebar_layout.addWidget(kami_g)

        sidebar_layout.addStretch()
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(sidebar_widget)

        self.map_view = QWebEngineView()
        self.map_view.settings().setAttribute(self.map_view.settings().LocalContentCanAccessFileUrls, True)
        self.map_view.settings().setAttribute(self.map_view.settings().LocalContentCanAccessRemoteUrls, True)
        try: base_path = os.path.dirname(os.path.abspath(__file__))
        except NameError: base_path = os.getcwd()

        leaflet_css_path = os.path.join(base_path, "leaflet", "leaflet.css").replace("\\", "/")
        leaflet_js_path = os.path.join(base_path, "leaflet", "leaflet.js").replace("\\", "/")

        html = MAP_HTML_TEMPLATE.format(
            leaflet_css=leaflet_css_path, leaflet_js=leaflet_js_path,
            tile_url=f"http://127.0.0.1:{TILE_SERVER_PORT}/tile/{{z}}/{{x}}/{{y}}",
            enable_tiles="true" if self.mbtiles_manager.has_offline_map else "false",
            max_wps=MAX_WAYPOINTS
        )
        self.map_view.setHtml(html, baseUrl=QUrl.fromLocalFile(base_path + os.path.sep))

        self.bridge = MapBridge()
        self.map_channel = QWebChannel()
        self.map_channel.registerObject("bridge", self.bridge)
        self.map_view.page().setWebChannel(self.map_channel)
        self.bridge.waypoint_signal.connect(self.haritadan_waypoint_al)

        main_h_layout.addWidget(scroll_area, 4)
        main_h_layout.addWidget(self.map_view, 6)
        self.mainTab.setLayout(main_h_layout)

    def build_graph_tab(self):
        main_layout = QHBoxLayout()

        left_layout = QVBoxLayout()
        self.cost_map_plot = pg.PlotWidget(title="Lokal Engel Haritası (Cost Map) - Radar Görünümü")
        self.cost_map_plot.setXRange(-20, 20)
        self.cost_map_plot.setYRange(-5, 30)
        self.cost_map_plot.showGrid(x=True, y=True, alpha=0.3)
        self.cost_map_plot.setAspectLocked(True)
        self.ida_marker = pg.ScatterPlotItem(x=[0], y=[0], size=15, pen=pg.mkPen(None), brush=pg.mkBrush("#00e5ff"), symbol="t1")
        self.cost_map_plot.addItem(self.ida_marker)
        self.obstacles_scatter = pg.ScatterPlotItem(size=10, pen=pg.mkPen(None), brush=pg.mkBrush("#ff1744"), symbol="o")
        self.cost_map_plot.addItem(self.obstacles_scatter)
        left_layout.addWidget(self.cost_map_plot)

        right_layout = QVBoxLayout()
        self.p_attitude = pg.PlotWidget(title="İDA Roll ve Pitch Salınım Analizi")
        self.p_attitude.addLegend()
        self.curve_roll = self.p_attitude.plot(pen=pg.mkPen("#a6e3a1", width=2), name="Roll (Yatma) °")
        self.curve_pitch = self.p_attitude.plot(pen=pg.mkPen("#fab387", width=2), name="Pitch (Yunuslama) °")
        
        btn_grafik = QPushButton("🎥 Şartname Grafiklerini Aç (Ekran 2)")
        btn_grafik.setStyleSheet("background-color: #cba6f7; color: #11111b; margin-top:10px;")
        btn_grafik.clicked.connect(self.grafik_penceresi_ac)
        
        log_video_layout = QHBoxLayout()
        self.btn_log_toggle = QPushButton("CSV Log Başlat")
        self.btn_log_toggle.setStyleSheet("background-color: #89b4fa; color: #11111b; padding: 8px;")
        self.btn_log_toggle.clicked.connect(self.toggle_logging)

        right_layout.addWidget(self.p_attitude, 1)
        right_layout.addWidget(btn_grafik)
        right_layout.addWidget(self.btn_log_toggle)
        right_layout.addWidget(QLabel("Sistem Logları:"), 0)
        
        self.logText = QTextEdit()
        self.logText.setReadOnly(True)
        right_layout.addWidget(self.logText, 1)

        main_layout.addLayout(left_layout, 5)
        main_layout.addLayout(right_layout, 5)
        self.graphTab.setLayout(main_layout)

    def grafik_penceresi_ac(self):
        if not self.graph_window:
            self.graph_window = VideoGraphWindow(self)
        self.graph_window.show()

    def toggle_follow(self):
        self.follow_mode = not self.follow_mode
        self.map_view.page().runJavaScript(f"window.setFollowMode({str(self.follow_mode).lower()});")
        btn_text = "🎯 TAKİP: AÇIK" if self.follow_mode else "🎯 TAKİP: KAPALI"
        self.sender().setText(btn_text)
        self.log_yazdir(f"FOLLOW: {'AÇIK' if self.follow_mode else 'KAPALI'}")

    @pyqtSlot(str, str)
    def statustext_isle(self, vehicle, text):
        if vehicle == "İHA" and "COLOR:" in text:
            renk = text.split(":")[1].strip().upper()
            if not self.kamikaze_aktif:
                renk_hex = "#f38ba8" if "KIRMIZI" in renk else "#89b4fa" if "MAVİ" in renk else "#a6e3a1"
                self.lbl_kami_durum.setText(f"<span style='color:{renk_hex}; font-weight: bold;'>{renk} TESPİT EDİLDİ!</span>")
                self.log_yazdir(f"[OTOMATİK RÖLE] İHA'dan '{renk}' rengi alındı. İDA'ya fırlatılıyor...")
                self.kamikaze_aktif = True
                pwm = 1000 if "KIRMIZI" in renk else 1500 if "MAVİ" in renk else 2000
                self.ida_thread.trigger_kamikaze(pwm)

        elif vehicle == "İDA":
            if "DIST:" in text:
                mesafe = text.split(":")[1].strip()
                self.lbl_kami_mesafe.setText(f"<span style='color:#89b4fa;'>Mesafe: <b>{mesafe} m</b></span>")
            elif "CONTACT" in text or "TEMAS" in text:
                self.lbl_kami_mesafe.setText("<span style='color:#a6e3a1; font-weight: bold;'>TEMAS SAĞLANDI!</span>")
                self.kamikaze_aktif = False

    def toggle_logging(self):
        if not self.is_logging:
            self.is_logging = True
            self.csv_filename = f"ida_telemetry_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            full_path = os.path.abspath(self.csv_filename)
            with open(self.csv_filename, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Zaman", "Enlem", "Boylam", "Hiz (m/s)", "Roll", "Pitch", "Heading (Yaw)", "Hedefe Mesafe (m)", "Set Yon"])
            self.btn_log_toggle.setText("CSV Kaydını Durdur")
            self.btn_log_toggle.setStyleSheet("background-color: #f38ba8; color: #11111b; padding: 8px;")
            self.log_yazdir(f"[LOG] Arka planda log başlatıldı.\nKaydedilen Dosya: {full_path}")
        else:
            self.is_logging = False
            self.btn_log_toggle.setText("CSV Log Başlat")
            self.btn_log_toggle.setStyleSheet("background-color: #89b4fa; color: #11111b; padding: 8px;")
            self.log_yazdir("[LOG] Arka planda log kaydı durduruldu.")

    @pyqtSlot(float, float, str)
    def haritadan_waypoint_al(self, lat, lon, vehicle):
        row = self.waypointTable.rowCount()
        self.waypointTable.insertRow(row)
        self.waypointTable.setItem(row, 0, QTableWidgetItem(vehicle))
        self.waypointTable.setItem(row, 1, QTableWidgetItem(f"{lat:.6f}"))
        self.waypointTable.setItem(row, 2, QTableWidgetItem(f"{lon:.6f}"))
        
        if vehicle == "İDA":
            if len(self.ida_mission_waypoints) < MAX_WAYPOINTS:
                self.ida_mission_waypoints.append((lat, lon))
                self.lbl_gorev_durum.setText(f"Nokta: <b style='color:#a6e3a1; font-size:14px;'>{len(self.ida_mission_waypoints)}/4</b>")
                self.log_yazdir(f"Nokta Eklendi: {lat:.6f}, {lon:.6f}")

    def manuel_nokta_ekle(self):
        try:
            lat = float(self.input_lat.text().replace(",", "."))
            lon = float(self.input_lon.text().replace(",", "."))
            if len(self.ida_mission_waypoints) < MAX_WAYPOINTS:
                # Sadece JavaScript'e komut gönder, listeye eklemeyi 'mapClicked' sinyali halledecek
                self.map_view.page().runJavaScript(f"if(typeof addWaypointFromGCS !== 'undefined') addWaypointFromGCS({lat}, {lon});")
                self.input_lat.clear()
                self.input_lon.clear()
            else:
                QMessageBox.warning(self, "Uyarı", f"Zaten {MAX_WAYPOINTS} nokta seçildi!")
        except ValueError:
            pass

    def haritayi_temizle(self):
        self.ida_mission_waypoints.clear()
        self.lbl_gorev_durum.setText("Nokta: <b style='color:#a6e3a1; font-size:14px;'>0/4</b>")
        self.waypointTable.setRowCount(0)
        self.map_view.page().runJavaScript("clearMission();")

    def gorevi_pixhawka_yukle(self):
        if not self.ida_thread.master:
            QMessageBox.warning(self, "Hata", "Lütfen önce İDA'ya bağlanın!")
            return

        if len(self.ida_mission_waypoints) == 0:
            QMessageBox.warning(self, "Eksik Nokta", "Lütfen haritadan nokta belirleyin!")
            return

        auto_rtl_enabled = self.cb_auto_rtl.isChecked()
        durum_metni = "AÇIK" if auto_rtl_enabled else "KAPALI"
        self.log_yazdir(f"[OTONOMİ] Görev araca gönderiliyor... (Otomatik RTL: {durum_metni})")
        self.ida_thread.upload_mission_thread_safe(self.ida_mission_waypoints, auto_rtl_enabled)

    @pyqtSlot(int, int)
    def ida_servo_guncelle(self, m1, m3):
        self.hist_m1.append(m1)
        self.hist_m3.append(m3)

    def ida_saglik_guncelle(self, voltaj, batarya_yuzde, gps_fix, uydu):
        fix_durumu = "3D Fix" if gps_fix >= 3 else "No Fix"
        renk = "#f38ba8" if batarya_yuzde < 20 else "#a6e3a1"
        text = f"<span style='font-size:12px; color:#cdd6f4; line-height:1.4;'>"
        text += f"Batarya: <b style='color:{renk}'>%{batarya_yuzde} ({voltaj:.1f}V)</b><br>"
        text += f"GPS: <b style='color:#cdd6f4;'>{uydu} Uydu ({fix_durumu})</b></span>"
        self.lbl_ida_health.setText(text)

    def iha_saglik_guncelle(self, voltaj, batarya_yuzde, gps_fix, uydu):
        fix_durumu = "3D Fix" if gps_fix >= 3 else "No Fix"
        renk = "#f38ba8" if batarya_yuzde < 20 else "#a6e3a1"
        text = f"<span style='font-size:12px; color:#cdd6f4; line-height:1.4;'>"
        text += f"Batarya: <b style='color:{renk}'>%{batarya_yuzde} ({voltaj:.1f}V)</b><br>"
        text += f"GPS: <b style='color:#cdd6f4;'>{uydu} Uydu ({fix_durumu})</b></span>"
        self.lbl_iha_health.setText(text)

    def ida_guncelle(self, lat, lon, speed, alt, roll, pitch, yaw, mode, arm, yon_sp, wp_dist):
        arm_color = "#a6e3a1" if arm == "ARM" else "#f38ba8"
        text = f"<span style='color:#a6e3a1; font-weight:bold; font-size:14px;'>[İDA] NAVİGASYON</span><br>"
        text += f"<span style='font-size:12px; color:#cdd6f4; line-height:1.4;'>"
        text += f"Mod: <b style='color:#cdd6f4;'>{mode}</b> | Dur: <b style='color:{arm_color}'>{arm}</b><br>"
        text += f"Hız: <b style='color:#89b4fa;'>{speed:.1f} m/s</b> | Hedefe Mesafe: <b style='color:#89b4fa;'>{wp_dist:.1f} m</b><br>"
        text += f"Yön: <b style='color:#fab387;'>{yaw:.1f}°</b> (Set: {yon_sp:.1f}°)<br>"
        text += f"<span style='font-size:10px; color:#888888;'>Lat:{lat:.5f} Lon:{lon:.5f}</span></span>"
        self.lbl_ida_nav.setText(text)
        
        self.hist_speed.append(speed)
        self.hist_speed_sp.append(wp_dist)
        self.hist_yaw.append(yaw)
        self.hist_yaw_sp.append(yon_sp)
        self.hist_roll.append(roll)
        self.hist_pitch.append(pitch)
        
        if self.is_logging and self.csv_filename:
            with open(self.csv_filename, mode="a", newline="") as file:
                writer = csv.writer(file)
                writer.writerow([datetime.now().strftime("%H:%M:%S"), lat, lon, speed, roll, pitch, yaw, wp_dist, yon_sp])
        
        if self.harita_arac_secim.currentText() == "İDA":
            self.pfd.update_attitude(roll, pitch)

        now = time.time()
        if now - self.last_map_js_ida >= 0.2:
            self.map_view.page().runJavaScript(f"try {{ if(typeof updateIdaPosition !== 'undefined') updateIdaPosition({lat}, {lon}, {yaw}, {speed}, '{mode}'); }} catch(e) {{}}")
            self.last_map_js_ida = now

    def iha_guncelle(self, lat, lon, speed, alt, roll, pitch, yaw, mode, arm, yon_sp, wp_dist):
        arm_color = "#a6e3a1" if arm == "ARM" else "#f38ba8"
        text = f"<span style='color:#89b4fa; font-weight:bold; font-size:14px;'>[İHA] NAVİGASYON</span><br>"
        text += f"<span style='font-size:12px; color:#cdd6f4; line-height:1.4;'>"
        text += f"Mod: <b style='color:#cdd6f4;'>{mode}</b> | Dur: <b style='color:{arm_color}'>{arm}</b><br>"
        text += f"İrtifa: <b style='color:#89b4fa;'>{alt:.1f} m</b><br>"
        text += f"Hız: <b style='color:#fab387;'>{speed:.1f} m/s</b> Yön: <b style='color:#fab387;'>{yaw:.1f}°</b><br>"
        text += f"<span style='font-size:10px; color:#888888;'>Lat:{lat:.5f} Lon:{lon:.5f}</span></span>"
        self.lbl_iha_nav.setText(text)
        
        if self.harita_arac_secim.currentText() == "İHA":
            self.pfd.update_attitude(roll, pitch)

        now = time.time()
        if now - self.last_map_js_iha >= 0.2:
            self.map_view.page().runJavaScript(f"try {{ if(typeof updateIhaPosition !== 'undefined') updateIhaPosition({lat}, {lon}, {yaw}, {alt}, '{mode}'); }} catch(e) {{}}")
            self.last_map_js_iha = now

    def update_graphs(self):
        if hasattr(self, 'hist_roll') and len(self.hist_roll) > 0:
            x_time = list(range(len(self.hist_roll)))
            self.curve_roll.setData(x_time, list(self.hist_roll))
            self.curve_pitch.setData(x_time, list(self.hist_pitch))

    def log_yazdir(self, msg):
        self.logText.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        self.logText.verticalScrollBar().setValue(self.logText.verticalScrollBar().maximum())

    def closeEvent(self, event):
        if self.tile_server:
            self.mbtiles_manager.close()
        self.ida_thread.disconnect()
        self.iha_thread.disconnect()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = YerKontrolIstasyonu()
    window.show()
    sys.exit(app.exec_())
