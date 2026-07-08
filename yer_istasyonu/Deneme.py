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
from typing import Optional
from enum import Enum

import serial.tools.list_ports

from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QObject, QTimer, QUrl
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QTextEdit, QFormLayout,
    QComboBox, QGroupBox, QLineEdit, QMessageBox, QFrame, QGridLayout, QScrollArea,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy, QSplitter
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
# KARANLIK TEMA
# ==========================================
DARK_THEME = """
QWidget { background-color: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI', Arial; font-size: 13px; }
QPushButton { border: 1px solid #45475a; border-radius: 4px; padding: 8px; font-weight: bold; font-size: 13px; }
QPushButton:hover { border: 1px solid #00e5ff; }
QComboBox, QLineEdit { background-color: #313244; border: 1px solid #45475a; padding: 6px; border-radius: 3px; color: white; font-size: 13px; }
QTextEdit { background-color: #181825; border: 1px solid #313244; color: #a6e3a1; font-family: monospace; font-size: 13px; }
QTableWidget { background-color: #181825; alternate-background-color: #1e1e2e; color: #cdd6f4; gridline-color: #313244; font-size: 12px; }
QHeaderView::section { background-color: #313244; color: white; border: 1px solid #45475a; padding: 4px; font-weight: bold; }
QGroupBox { border: 1px solid #45475a; border-radius: 4px; margin-top: 15px; font-weight: bold; color: #89b4fa; padding-top: 15px; font-size: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 5px; background-color: #1e1e2e; }
QTabWidget::pane { border: 1px solid #45475a; background-color: #1e1e2e; }
QTabBar::tab { background: #181825; border: 1px solid #45475a; padding: 10px 20px; min-width: 280px; margin-right: 2px; color: #888888; border-top-left-radius: 4px; border-top-right-radius: 4px; font-size: 14px; font-weight: bold; }
QTabBar::tab:selected { background: #1e1e2e; border-bottom-color: #1e1e2e; color: #a6e3a1; font-weight: bold; }
QLabel { background-color: transparent; }
QScrollArea { border: none; background-color: transparent; }
QSplitter::handle { background-color: #45475a; width: 4px; }
"""

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

        self.p_speed = pg.PlotWidget(title="Hız (m/s) ve Hız İsteği")
        self.p_speed.addLegend()
        self.curve_speed_real = self.p_speed.plot(pen=pg.mkPen("#89b4fa", width=2), name="Gerçek Hız")
        self.curve_speed_set = self.p_speed.plot(pen=pg.mkPen("#f9e2af", width=2, style=Qt.DashLine), name="Hız İsteği (Set)")

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
            self.curve_speed_set.setData(list(self.main_app.hist_speed_sp))
            self.curve_yaw_real.setData(list(self.main_app.hist_yaw))
            self.curve_yaw_set.setData(list(self.main_app.hist_yaw_sp))
            self.curve_pwm_left.setData(list(self.main_app.hist_m1))
            self.curve_pwm_right.setData(list(self.main_app.hist_m3))

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
    let waypointMarkers = [];
    let waypointPolyline = null;
    let missionLayer = L.layerGroup().addTo(map);

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
                html: '<div style="background-color:#fab387;color:#11111b;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;font-weight:bold;border:2px solid #11111b; font-size:14px;">' + (idx + 1) + '</div>',
                iconSize: [32, 32], iconAnchor: [16, 16]
            }})
        }}).bindPopup('<div class="info-panel"><b>WP ' + (idx + 1) + '</b><br>Lat: ' + lat.toFixed(6) + '<br>Lon: ' + lon.toFixed(6) + '</div>');

        marker.addTo(missionLayer);
        waypointMarkers.push({{marker: marker, lat: lat, lon: lon}});
        if (bridge) bridge.mapClicked(lat, lon, "İDA");
        window.updateMissionLine();
    }};

    window.removeWaypoint = function(index) {{
        if (index >= 0 && index < waypointMarkers.length) {{
            missionLayer.removeLayer(waypointMarkers[index].marker);
            waypointMarkers.splice(index, 1);
            
            waypointMarkers.forEach((w, idx) => {{
                let html = '<div style="background-color:#fab387;color:#11111b;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;font-weight:bold;border:2px solid #11111b; font-size:14px;">' + (idx + 1) + '</div>';
                w.marker.setIcon(L.divIcon({{html: html, iconSize: [32, 32], iconAnchor: [16, 16]}}));
                w.marker.getPopup().setContent('<div class="info-panel"><b>WP ' + (idx + 1) + '</b><br>Lat: ' + w.lat.toFixed(6) + '<br>Lon: ' + w.lon.toFixed(6) + '</div>');
            }});
            window.updateMissionLine();
        }}
    }};

    window.updateMissionLine = function() {{
        if (waypointPolyline) missionLayer.removeLayer(waypointPolyline);
        let coords = waypointMarkers.map(w => [w.lat, w.lon]);
        waypointPolyline = L.polyline(coords, {{color:'#fab387', weight:3, dashArray:'6, 6'}});
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
        let html = '<div id="' + rotId + '" style="width: 130px; height: 130px; transform: rotate(' + heading + 'deg); transform-origin: center center; transition: transform 0.15s linear;">' +
                   '<svg width="130" height="130" viewBox="0 0 130 130">' +
                   '  <path d="M 65 65 L 35.5 13.4 A 60 60 0 0 1 94.5 13.4 Z" fill="' + color + '" fill-opacity="0.4" />' +
                   '  <circle cx="65" cy="65" r="9" fill="' + color + '" stroke="#11111b" stroke-width="2.5" />' +
                   '</svg></div>';
        return L.divIcon({{ className: 'custom-cone-marker', html: html, iconSize: [130, 130], iconAnchor: [65, 65] }});
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
            self.log_signal.emit(f"[{self.vehicle_type}] Porta bağlanılıyor: {connection_string}...")
            
            # Windows'ta ve RFD900'de kopmalara karşı autoreconnect=True
            if connection_string.startswith(("udp:", "tcp:", "udpin:")):
                self.master = mavutil.mavlink_connection(connection_string, autoreconnect=True)
            else:
                self.master = mavutil.mavlink_connection(connection_string, baud=baudrate, autoreconnect=True)

            self.log_signal.emit(f"[{self.vehicle_type}] Telemetri senkronizasyonu bekleniyor...")
            
            # RFD900 bağlanırken ilk saniyelerde veri atlayabilir, timeout'u 15 saniyeye çıkardık
            msg = self.master.wait_heartbeat(timeout=15)
            
            if not msg:
                self.log_signal.emit(f"[{self.vehicle_type}] ⚠️ UYARI: Heartbeat gecikti! Yine de veri akışı zorlanıyor...")
                target_sys = 1
                target_comp = 1
            else:
                self.log_signal.emit(f"[{self.vehicle_type}] ✅ Heartbeat alındı! (Sistem ID: {self.master.target_system})")
                target_sys = self.master.target_system
                target_comp = self.master.target_component

            self.is_running = True

            self.master.mav.request_data_stream_send(
                target_sys, target_comp,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1
            )
            
            self.connected_signal.emit(True)
            self.start()
            
        except PermissionError:
            self.is_running = False
            self.connected_signal.emit(False)
            self.log_signal.emit(f"[{self.vehicle_type}] ❌ HATA: Port meşgul! Mission Planner veya diğer test kodu arkada açık kalmış olabilir.")
            QMessageBox.critical(None, "Port Meşgul", "Seçtiğiniz COM portu şu an başka bir program tarafından kullanılıyor. Lütfen Mission Planner'ı veya diğer pencereleri kapatın.")
        except Exception as e:
            self.is_running = False
            self.connected_signal.emit(False)
            self.log_signal.emit(f"[{self.vehicle_type}] ❌ Bağlantı Hatası: {e}")

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
                    if lat != 0.0 and lon != 0.0:
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
                self.log_signal.emit(f"[{self.vehicle_type}] KAMIKAZE EMRİ GÖNDERİLDİ: CH{KILL_SERVO_CHANNEL} -> {pwm_value}")
            except Exception as e:
                self.log_signal.emit(f"[{self.vehicle_type}] Kamikaze hatası: {e}")

    def kill_power(self):
        if self.master:
            try:
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
                        continue 
                    
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
        self.setMinimumSize(180, 100)
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

        painter.fillRect(int(-w * 1.5), int(-h * 2), int(w * 3), int(h * 2), QColor(137, 180, 250)) 
        painter.fillRect(int(-w * 1.5), 0, int(w * 3), int(h * 2), QColor(166, 227, 161)) 
        
        painter.setPen(QPen(Qt.white, 2))
        painter.drawLine(int(-w), 0, int(w), 0)
        painter.restore()

        painter.setPen(QPen(QColor(243, 139, 168), 3)) 
        painter.drawLine(int(w / 2 - 30), int(h / 2), int(w / 2 - 10), int(h / 2))
        painter.drawLine(int(w / 2 + 10), int(h / 2), int(w / 2 + 30), int(h / 2))
        painter.drawLine(int(w / 2), int(h / 2 - 5), int(w / 2), int(h / 2 + 5))

        painter.setPen(Qt.white)
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(5, 20, f"R:{self.roll:.1f}°")
        painter.drawText(5, 40, f"P:{self.pitch:.1f}°")

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

    def get_serial_ports(self):
        filtered_ports = []
        for port in serial.tools.list_ports.comports():
            dev = port.device
            if "/dev/ttyS" in dev:
                if dev in ["/dev/ttyS0", "/dev/ttyS1", "/dev/ttyS2"]:
                    filtered_ports.append(dev)
            else:
                filtered_ports.append(dev)
        return filtered_ports

    def buton_stili_olustur(self, bg_color, hover_color, text_color="#11111b"):
        return f"""
            QPushButton {{
                background-color: {bg_color};
                color: {text_color};
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {hover_color};
            }}
            QPushButton:pressed {{
                background-color: {bg_color};
            }}
        """

    def initUI(self):
        self.tabs = QTabWidget()
        self.mainTab = QWidget()
        self.graphTab = QWidget()

        self.tabs.addTab(self.mainTab, "1. Operasyon Merkezi")
        self.tabs.addTab(self.graphTab, "2. Mühendislik Analizi ve Loglar")

        self.build_main_tab()
        self.build_graph_tab()

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.tabs)
        self.setLayout(layout)

    def update_link_status(self, who, ok):
        if who == "İDA": self._ida_ok = ok
        else: self._iha_ok = ok
        color = "#a6e3a1" if (self._ida_ok or self._iha_ok) else "#f38ba8"
        self.lbl_link_status.setStyleSheet(f"font-size: 14px; font-weight: bold; margin-top: 5px; color: {color};")
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
        main_h_layout.setContentsMargins(4, 4, 4, 4)
        main_h_layout.setSpacing(6)

        sidebar_widget = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(2, 2, 2, 2)
        sidebar_layout.setSpacing(4)

        telemetry_layout = QHBoxLayout()
        telemetry_layout.setSpacing(4)
        
        ida_frame = QFrame()
        ida_frame.setStyleSheet("background-color: #181825; border: 1px solid #45475a; border-radius: 4px; padding: 4px;")
        ida_l = QVBoxLayout(ida_frame)
        ida_l.setSpacing(2)
        self.lbl_ida_nav = QLabel("<span style='color:#a6e3a1; font-weight:bold; font-size:16px;'>[İDA] NAVİGASYON</span><br><br><span style='color:#888888; font-size:13px;'>Bekleniyor...</span>")
        self.lbl_ida_health = QLabel("<span style='color:#888888; font-size:13px;'>GPS: --</span>")
        line_ida = QFrame(); line_ida.setFrameShape(QFrame.HLine); line_ida.setStyleSheet("color: #45475a;")
        ida_l.addWidget(self.lbl_ida_nav); ida_l.addWidget(line_ida); ida_l.addWidget(self.lbl_ida_health)

        iha_frame = QFrame()
        iha_frame.setStyleSheet("background-color: #181825; border: 1px solid #45475a; border-radius: 4px; padding: 4px;")
        iha_l = QVBoxLayout(iha_frame)
        iha_l.setSpacing(2)
        self.lbl_iha_nav = QLabel("<span style='color:#89b4fa; font-weight:bold; font-size:16px;'>[İHA] NAVİGASYON</span><br><br><span style='color:#888888; font-size:13px;'>Bekleniyor...</span>")
        self.lbl_iha_health = QLabel("<span style='color:#888888; font-size:13px;'>GPS: --</span>")
        line_iha = QFrame(); line_iha.setFrameShape(QFrame.HLine); line_iha.setStyleSheet("color: #45475a;")
        iha_l.addWidget(self.lbl_iha_nav); iha_l.addWidget(line_iha); iha_l.addWidget(self.lbl_iha_health)

        telemetry_layout.addWidget(ida_frame); telemetry_layout.addWidget(iha_frame)
        sidebar_layout.addLayout(telemetry_layout)

        pfd_layout = QHBoxLayout()
        self.pfd = HorizonIndicator()
        pfd_layout.addWidget(self.pfd)
        sidebar_layout.addLayout(pfd_layout)

        planlama_g = QGroupBox("1. Rota Planlama")
        planlama_l = QVBoxLayout()
        planlama_l.setSpacing(4)
        
        imlec_layout = QHBoxLayout()
        imlec_layout.addWidget(QLabel("Harita İmleci:"))
        self.harita_arac_secim = QComboBox()
        self.harita_arac_secim.addItems(["İDA", "İHA"])
        self.harita_arac_secim.currentTextChanged.connect(lambda txt: self.map_view.page().runJavaScript(f"if(typeof setVehicle !== 'undefined') setVehicle('{txt}')"))
        imlec_layout.addWidget(self.harita_arac_secim)
        planlama_l.addLayout(imlec_layout)

        manuel_layout = QHBoxLayout()
        self.input_lat = QLineEdit(); self.input_lat.setPlaceholderText("Enlem")
        self.input_lon = QLineEdit(); self.input_lon.setPlaceholderText("Boylam")
        self.btn_manuel_ekle = QPushButton("Ekle")
        self.btn_manuel_ekle.setStyleSheet("background-color: #313244; color: white;")
        self.btn_manuel_ekle.clicked.connect(self.manuel_nokta_ekle)
        manuel_layout.addWidget(self.input_lat); manuel_layout.addWidget(self.input_lon); manuel_layout.addWidget(self.btn_manuel_ekle)
        planlama_l.addLayout(manuel_layout)

        self.waypointTable = QTableWidget(0, 3)
        self.waypointTable.setHorizontalHeaderLabels(["Sıra", "Enlem", "Boylam"])
        self.waypointTable.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.waypointTable.setFixedHeight(160)
        self.waypointTable.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        planlama_l.addWidget(self.waypointTable)
        
        durum_kutu = QHBoxLayout()
        self.lbl_gorev_durum = QLabel(f"Nokta: <b style='color:#a6e3a1; font-size:14px;'>0/{MAX_WAYPOINTS}</b>")
        self.btn_temizle = QPushButton("Tümünü Sil")
        self.btn_temizle.setStyleSheet("background-color: #313244; color: white;")
        self.btn_temizle.clicked.connect(self.haritayi_temizle)
        self.btn_secili_sil = QPushButton("Seçili Sil")
        self.btn_secili_sil.setStyleSheet("background-color: #fab387; color: #11111b;")
        self.btn_secili_sil.clicked.connect(self.secili_noktayi_sil)
        
        durum_kutu.addWidget(self.lbl_gorev_durum)
        durum_kutu.addWidget(self.btn_temizle)
        durum_kutu.addWidget(self.btn_secili_sil)
        planlama_l.addLayout(durum_kutu)

        planlama_g.setLayout(planlama_l)
        sidebar_layout.addWidget(planlama_g)

        kontrol_g = QGroupBox("2. Otonomi ve Komutlar")
        kontrol_l = QVBoxLayout()
        kontrol_l.setSpacing(4)
        
        arm_layout = QGridLayout()
        arm_layout.setSpacing(4)
        btn_ida_arm = QPushButton("İDA ARM")
        btn_ida_arm.setStyleSheet(self.buton_stili_olustur("#a6e3a1", "#89d689"))
        btn_ida_arm.clicked.connect(lambda: self.ida_thread.send_command(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 1, 21196))
        
        btn_ida_disarm = QPushButton("İDA DISARM")
        btn_ida_disarm.setStyleSheet(self.buton_stili_olustur("#f38ba8", "#ea769b"))
        btn_ida_disarm.clicked.connect(lambda: self.ida_thread.send_command(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 21196))
        
        btn_ida_manual = QPushButton("İDA DUR (MANUAL)")
        btn_ida_manual.setStyleSheet(self.buton_stili_olustur("#fab387", "#f5925d"))
        btn_ida_manual.clicked.connect(lambda: self.ida_thread.set_mode(VehicleMode.MANUAL))
        
        arm_layout.addWidget(btn_ida_arm, 0, 0)
        arm_layout.addWidget(btn_ida_disarm, 0, 1)
        arm_layout.addWidget(btn_ida_manual, 1, 0, 1, 2)
        kontrol_l.addLayout(arm_layout)

        self.cb_auto_rtl = QCheckBox("Görev Sonu EVE DÖN (Otomatik RTL)")
        self.cb_auto_rtl.setChecked(True)
        kontrol_l.addWidget(self.cb_auto_rtl)

        self.btn_gorev_gonder = QPushButton("GÖREVİ YÜKLE")
        self.btn_gorev_gonder.setStyleSheet(self.buton_stili_olustur("#89b4fa", "#749ff5"))
        self.btn_gorev_gonder.clicked.connect(self.gorevi_pixhawka_yukle)
        kontrol_l.addWidget(self.btn_gorev_gonder)

        self.btn_auto = QPushButton("GÖREVİ BAŞLAT (AUTO)")
        self.btn_auto.setStyleSheet(self.buton_stili_olustur("#cba6f7", "#b38cf5"))
        self.btn_auto.clicked.connect(self.ida_thread.restart_mission_and_auto)
        kontrol_l.addWidget(self.btn_auto)

        kontrol_g.setLayout(kontrol_l)
        sidebar_layout.addWidget(kontrol_g)

        kami_g = QGroupBox("Kamikaze Paneli (Manuel/Otomatik)")
        kami_g.setStyleSheet("QGroupBox { border: 1px solid #f38ba8; } QGroupBox::title { color: #f38ba8; }")
        kami_l = QVBoxLayout()
        kami_l.setSpacing(4)
        self.lbl_kami_durum = QLabel("<span style='color:#888888;'>Durum: İHA Hedefi Bekleniyor...</span>")
        kami_l.addWidget(self.lbl_kami_durum)

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
        self.btn_acil.setStyleSheet(self.buton_stili_olustur("#eba0ac", "#d68794"))
        self.btn_acil.clicked.connect(self.ida_thread.kill_power)
        kami_l.addWidget(self.btn_acil)
        kami_g.setLayout(kami_l)
        sidebar_layout.addWidget(kami_g)

        sidebar_layout.addStretch()
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(sidebar_widget)
        scroll_area.setMinimumWidth(440) 

        self.map_view = QWebEngineView()
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

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(scroll_area)
        splitter.addWidget(self.map_view)
        splitter.setStretchFactor(0, 35)
        splitter.setStretchFactor(1, 65)

        main_h_layout.addWidget(splitter)
        self.mainTab.setLayout(main_h_layout)

    def build_graph_tab(self):
        main_layout = QHBoxLayout()

        left_layout = QVBoxLayout()
        
        available_ports = self.get_serial_ports()

        ida_group = QGroupBox("İDA Telemetri Bağlantısı - 868 MHz")
        ida_layout = QFormLayout()
        self.ida_port = QComboBox(); self.ida_port.setEditable(True)
        self.ida_port.addItems(["udp:127.0.0.1:14550", "tcp:127.0.0.1:5760"] + available_ports)
        self.ida_port.setCurrentText("udp:127.0.0.1:14550")
        self.ida_baud = QComboBox(); self.ida_baud.addItems(["57600", "115200", "921600"])
        self.ida_conn_btn = QPushButton("İDA'ya Bağlan")
        self.ida_conn_btn.setStyleSheet(self.buton_stili_olustur("#89b4fa", "#749ff5"))
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
        self.iha_conn_btn.setStyleSheet(self.buton_stili_olustur("#89b4fa", "#749ff5"))
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
        self.lbl_link_status.setStyleSheet("font-size: 15px; font-weight: bold; margin-top: 10px; color: #f38ba8;")

        left_layout.addWidget(ida_group)
        left_layout.addWidget(iha_group)
        left_layout.addWidget(param_group)
        left_layout.addWidget(self.lbl_link_status)
        left_layout.addStretch()

        right_layout = QVBoxLayout()
        
        btn_grafik = QPushButton("🎥 Şartname Grafiklerini Aç (Ekran 2)")
        btn_grafik.setStyleSheet(self.buton_stili_olustur("#cba6f7", "#b38cf5"))
        btn_grafik.clicked.connect(self.grafik_penceresi_ac)
        
        log_video_layout = QHBoxLayout()
        self.btn_log_toggle = QPushButton("CSV Log Başlat")
        self.btn_log_toggle.setStyleSheet(self.buton_stili_olustur("#89b4fa", "#749ff5"))
        self.btn_log_toggle.clicked.connect(self.toggle_logging)

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
                self.lbl_kami_durum.setText(f"<span style='color:{renk_hex}; font-weight: bold; font-size:14px;'>{renk} TESPİT EDİLDİ!</span>")
                self.log_yazdir(f"[OTOMATİK RÖLE] İHA'dan '{renk}' rengi alındı. İDA'ya fırlatılıyor...")
                self.kamikaze_aktif = True
                pwm = 1000 if "KIRMIZI" in renk else 1500 if "MAVİ" in renk else 2000
                self.ida_thread.trigger_kamikaze(pwm)

        elif vehicle == "İDA":
            if "CONTACT" in text or "TEMAS" in text:
                self.lbl_kami_durum.setText("<span style='color:#a6e3a1; font-weight: bold; font-size:14px;'>TEMAS SAĞLANDI!</span>")
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
            self.btn_log_toggle.setStyleSheet(self.buton_stili_olustur("#f38ba8", "#ea769b"))
            self.log_yazdir(f"[LOG] Arka planda log başlatıldı.\nKaydedilen Dosya: {full_path}")
        else:
            self.is_logging = False
            self.btn_log_toggle.setText("CSV Log Başlat")
            self.btn_log_toggle.setStyleSheet(self.buton_stili_olustur("#89b4fa", "#749ff5"))
            self.log_yazdir("[LOG] Arka planda log kaydı durduruldu.")

    @pyqtSlot(float, float, str)
    def haritadan_waypoint_al(self, lat, lon, vehicle):
        if vehicle == "İDA":
            if len(self.ida_mission_waypoints) < MAX_WAYPOINTS:
                row = self.waypointTable.rowCount()
                self.waypointTable.insertRow(row)
                self.waypointTable.setItem(row, 0, QTableWidgetItem(str(row + 1)))
                self.waypointTable.setItem(row, 1, QTableWidgetItem(f"{lat:.6f}"))
                self.waypointTable.setItem(row, 2, QTableWidgetItem(f"{lon:.6f}"))
                
                self.ida_mission_waypoints.append((lat, lon))
                self.lbl_gorev_durum.setText(f"Nokta: <b style='color:#a6e3a1; font-size:14px;'>{len(self.ida_mission_waypoints)}/{MAX_WAYPOINTS}</b>")
                self.log_yazdir(f"Nokta Eklendi: {lat:.6f}, {lon:.6f}")

    def manuel_nokta_ekle(self):
        try:
            lat = float(self.input_lat.text().replace(",", "."))
            lon = float(self.input_lon.text().replace(",", "."))
            if len(self.ida_mission_waypoints) < MAX_WAYPOINTS:
                self.map_view.page().runJavaScript(f"if(typeof addWaypointFromGCS !== 'undefined') addWaypointFromGCS({lat}, {lon});")
                self.input_lat.clear()
                self.input_lon.clear()
            else:
                QMessageBox.warning(self, "Uyarı", f"Zaten {MAX_WAYPOINTS} nokta seçildi!")
        except ValueError:
            pass

    def secili_noktayi_sil(self):
        selected_items = self.waypointTable.selectedItems()
        if not selected_items:
            return
            
        rows = set([item.row() for item in selected_items])
        
        for row in sorted(rows, reverse=True):
            self.waypointTable.removeRow(row)
            if row < len(self.ida_mission_waypoints):
                self.ida_mission_waypoints.pop(row)
            self.map_view.page().runJavaScript(f"try {{ removeWaypoint({row}); }} catch(e) {{}}")
            
        for i in range(self.waypointTable.rowCount()):
            self.waypointTable.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            
        self.lbl_gorev_durum.setText(f"Nokta: <b style='color:#a6e3a1; font-size:14px;'>{len(self.ida_mission_waypoints)}/{MAX_WAYPOINTS}</b>")

    def haritayi_temizle(self):
        self.ida_mission_waypoints.clear()
        self.lbl_gorev_durum.setText(f"Nokta: <b style='color:#a6e3a1; font-size:14px;'>0/{MAX_WAYPOINTS}</b>")
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
        text = f"<span style='font-size:13px; color:#cdd6f4; line-height:1.4;'>"
        text += f"GPS: <b style='color:#cdd6f4;'>{uydu} Uydu ({fix_durumu})</b></span>"
        self.lbl_ida_health.setText(text)

    def iha_saglik_guncelle(self, voltaj, batarya_yuzde, gps_fix, uydu):
        fix_durumu = "3D Fix" if gps_fix >= 3 else "No Fix"
        text = f"<span style='font-size:13px; color:#cdd6f4; line-height:1.4;'>"
        text += f"GPS: <b style='color:#cdd6f4;'>{uydu} Uydu ({fix_durumu})</b></span>"
        self.lbl_iha_health.setText(text)

    def ida_guncelle(self, lat, lon, speed, alt, roll, pitch, yaw, mode, arm, yon_sp, wp_dist):
        arm_color = "#a6e3a1" if arm == "ARM" else "#f38ba8"
        text = f"<span style='color:#a6e3a1; font-weight:bold; font-size:15px;'>[İDA] NAVİGASYON</span><br>"
        text += f"<span style='font-size:13px; color:#cdd6f4; line-height:1.4;'>"
        text += f"Mod: <b style='color:#cdd6f4;'>{mode}</b> | Dur: <b style='color:{arm_color}'>{arm}</b><br>"
        text += f"Hız: <b style='color:#89b4fa;'>{speed:.1f} m/s</b><br>"
        text += f"Yön: <b style='color:#fab387;'>{yaw:.1f}°</b> (Set: {yon_sp:.1f}°)<br>"
        text += f"<span style='font-size:11px; color:#888888;'>Lat:{lat:.5f} Lon:{lon:.5f}</span></span>"
        self.lbl_ida_nav.setText(text)
        
        self.hist_speed.append(speed)
        self.hist_speed_sp.append(0.0) 
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
        text = f"<span style='color:#89b4fa; font-weight:bold; font-size:15px;'>[İHA] NAVİGASYON</span><br>"
        text += f"<span style='font-size:13px; color:#cdd6f4; line-height:1.4;'>"
        text += f"Mod: <b style='color:#cdd6f4;'>{mode}</b> | Dur: <b style='color:{arm_color}'>{arm}</b><br>"
        text += f"İrtifa: <b style='color:#89b4fa;'>{alt:.1f} m</b><br>"
        text += f"Hız: <b style='color:#fab387;'>{speed:.1f} m/s</b> Yön: <b style='color:#fab387;'>{yaw:.1f}°</b><br>"
        text += f"<span style='font-size:11px; color:#888888;'>Lat:{lat:.5f} Lon:{lon:.5f}</span></span>"
        self.lbl_iha_nav.setText(text)
        
        if self.harita_arac_secim.currentText() == "İHA":
            self.pfd.update_attitude(roll, pitch)

        now = time.time()
        if now - self.last_map_js_iha >= 0.2:
            self.map_view.page().runJavaScript(f"try {{ if(typeof updateIhaPosition !== 'undefined') updateIhaPosition({lat}, {lon}, {yaw}, {alt}, '{mode}'); }} catch(e) {{}}")
            self.last_map_js_iha = now

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

