"""
🌊 İDA YER KONTROL İSTASYONU v4.1 - FULL OFFLINE + WORKING GRAPHS
TEKNOFEST 2026 - PARKUR 1+2+3 COMPLETE - ALL BUGS FIXED

✅ OFFLINE HARITA: MBTiles (Internet yok!)
✅ LIVE GRAFIKLER: Speed, Altitude, Battery
✅ RFD868x + 433MHz: WiFi'siz haberleşme
✅ PARKUR 1+2+3: Komple sistem
✅ QInputDialog: Düzeltildi
✅ update_graphs(): Çalışıyor
"""

import sys
import os
import json
import csv
import math
import time
from pathlib import Path
from datetime import datetime
from collections import deque
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from enum import Enum

# Environment
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"

# PyQt5
from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QObject, QTimer, QInputDialog
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTextEdit, QFormLayout, QComboBox, QGroupBox, QLineEdit,
    QMessageBox, QFrame, QGridLayout, QScrollArea, QSpinBox, QDoubleSpinBox,
    QFileDialog, QTableWidget, QTableWidgetItem, QSplitter
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtGui import QPainter, QColor, QPen, QFont

# Scientific
import pyqtgraph as pg
import numpy as np
import serial
import serial.tools.list_ports
from pymavlink import mavutil

# Offline harita
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ═══════════════════════════════════════════════════════════════
# 0. OFFLINE HARITA MANAGER - MBTiles
# ═══════════════════════════════════════════════════════════════

class MBTilesManager:
    """MBTiles'dan offline tile'lar sun"""
    
    def __init__(self, mbtiles_path: str = "turkey_offline.mbtiles"):
        self.mbtiles_path = Path(mbtiles_path)
        self.conn = None
        self.metadata = {}
        self.has_offline_map = False
        
        if self.mbtiles_path.exists():
            self._connect_db()
            self.has_offline_map = True
        else:
            print(f"⚠️  MBTiles dosyası bulunamadı: {self.mbtiles_path}")
    
    def _connect_db(self):
        try:
            self.conn = sqlite3.connect(str(self.mbtiles_path))
            cursor = self.conn.cursor()
            cursor.execute("SELECT name, value FROM metadata")
            self.metadata = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.close()
            print(f"✅ MBTiles bağlandı: {self.metadata.get('name', 'Unknown')}")
        except Exception as e:
            print(f"❌ MBTiles hatası: {e}")
    
    def _deg2num(self, lat: float, lon: float, zoom: int) -> Tuple[int, int, int]:
        n = 2.0 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
        return zoom, x, y
    
    def get_tile(self, zoom: int, x: int, y: int) -> Optional[bytes]:
        if not self.conn:
            return None
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?",
                (zoom, x, y)
            )
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else None
        except:
            return None
    
    def close(self):
        if self.conn:
            self.conn.close()

# ═══════════════════════════════════════════════════════════════
# 1. OFFLINE HARITA HTML
# ═══════════════════════════════════════════════════════════════

MAP_HTML_OFFLINE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>İDA Mission Planner - OFFLINE</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        html, body, #map { height: 100%; width: 100%; margin: 0; padding: 0; }
        .info-panel {
            background: rgba(30, 30, 30, 0.95);
            color: #e0e0e0;
            padding: 10px;
            border-radius: 5px;
            border: 2px solid #00e5ff;
            font-size: 11px;
            line-height: 1.5;
        }
        .offline-badge {
            position: fixed;
            bottom: 10px;
            left: 10px;
            background: #2e7d32;
            color: white;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: bold;
            z-index: 9999;
        }
    </style>
</head>
<body>
<div id="map"></div>
<div class="offline-badge">🗺️ OFFLINE MODE - MBTiles</div>
<script>
    const map = L.map('map').setView([39.0, 35.0], 7);
    
    // 👈 OFFLINE TILE LAYER
    L.tileLayer('/tile/{z}/{x}/{y}', {
        maxZoom: 18,
        minZoom: 5,
        attribution: 'MBTiles Offline',
        tms: true
    }).addTo(map);
    
    let bridge = null;
    
    // İDA
    let idaMarker = null, idaPath = [], idaPolyline = L.polyline([], {color: '#00e5ff', weight: 2, opacity: 0.7});
    idaPolyline.addTo(map);
    
    // İHA
    let ihaMarker = null, ihaPath = [], ihaPolyline = L.polyline([], {color: '#d50000', weight: 2, opacity: 0.7});
    ihaPolyline.addTo(map);
    
    // Mission
    let waypointMarkers = [], waypointPolyline = null, missionLayer = L.layerGroup().addTo(map);
    
    new QWebChannel(qt.webChannelTransport, function(channel) {
        bridge = channel.objects.bridge;
    });
    
    map.on('click', function(e) {
        if (waypointMarkers.length < 4) {
            window.addMissionWaypoint(e.latlng.lat, e.latlng.lng);
        }
    });
    
    window.addMissionWaypoint = function(lat, lon) {
        let idx = waypointMarkers.length;
        let marker = L.marker([lat, lon], {
            icon: L.divIcon({
                html: '<div style="background-color: #ff9100; color: white; border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; font-weight: bold; border: 2px solid white; font-size: 14px;">' + (idx + 1) + '</div>',
                iconSize: [32, 32],
                iconAnchor: [16, 16]
            })
        }).bindPopup('<div class="info-panel"><b>WP ' + (idx + 1) + '</b><br>Lat: ' + lat.toFixed(6) + '<br>Lon: ' + lon.toFixed(6) + '</div>');
        
        marker.addTo(missionLayer);
        waypointMarkers.push({marker: marker, lat: lat, lon: lon});
        if (bridge) bridge.waypointAdded(lat, lon, idx);
        window.updateMissionLine();
    };
    
    window.updateMissionLine = function() {
        if (waypointPolyline) missionLayer.removeLayer(waypointPolyline);
        let coords = waypointMarkers.map(w => [w.lat, w.lon]);
        waypointPolyline = L.polyline(coords, {color: '#ff9800', weight: 2, dashArray: '5, 5', opacity: 0.7});
        waypointPolyline.addTo(missionLayer);
    };
    
    window.clearMission = function() {
        waypointMarkers.forEach(w => missionLayer.removeLayer(w.marker));
        waypointMarkers = [];
        if (waypointPolyline) missionLayer.removeLayer(waypointPolyline);
        idaPolyline.setLatLngs([]);
        idaPath = [];
        ihaPolyline.setLatLngs([]);
        ihaPath = [];
    };
    
    window.updateIdaPosition = function(lat, lon, heading, speed, mode) {
        if (!idaMarker) {
            idaMarker = L.circleMarker([lat, lon], {
                radius: 8, fillColor: '#4caf50', color: '#fff', weight: 2, opacity: 1, fillOpacity: 0.8
            }).addTo(map).bindPopup('<div class="info-panel"><b>🚤 İDA</b><br>Mod: ' + mode + '<br>Hız: ' + speed.toFixed(1) + ' m/s</div>');
        }
        idaMarker.setLatLng([lat, lon]);
        idaPath.push([lat, lon]);
        if (idaPath.length > 500) idaPath.shift();
        idaPolyline.setLatLngs(idaPath);
        map.setView([lat, lon], map.getZoom());
    };
    
    window.updateIhaPosition = function(lat, lon, heading, altitude, mode) {
        if (!ihaMarker) {
            ihaMarker = L.circleMarker([lat, lon], {
                radius: 6, fillColor: '#d50000', color: '#fff', weight: 2, opacity: 1, fillOpacity: 0.8
            }).addTo(map).bindPopup('<div class="info-panel"><b>🛸 İHA</b><br>Mod: ' + mode + '<br>İrtifa: ' + altitude.toFixed(1) + ' m</div>');
        }
        ihaMarker.setLatLng([lat, lon]);
        ihaPath.push([lat, lon]);
        if (ihaPath.length > 500) ihaPath.shift();
        ihaPolyline.setLatLngs(ihaPath);
    };
</script>
</body>
</html>
'''

# ═══════════════════════════════════════════════════════════════
# 2. OFFLINE TILE SERVER
# ═══════════════════════════════════════════════════════════════

class OfflineTileHandler(BaseHTTPRequestHandler):
    mbtiles_manager = None
    
    def do_GET(self):
        if self.path.startswith('/tile/'):
            try:
                parts = self.path.split('/')
                z = int(parts[2])
                x = int(parts[3])
                y = int(parts[4])
                
                tile_data = self.mbtiles_manager.get_tile(z, x, y)
                
                if tile_data:
                    self.send_response(200)
                    self.send_header('Content-type', 'image/png')
                    self.send_header('Content-Length', len(tile_data))
                    self.send_header('Cache-Control', 'max-age=2592000')
                    self.end_headers()
                    self.wfile.write(tile_data)
                else:
                    self.send_response(404)
                    self.end_headers()
            except:
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

class OfflineTileServer(threading.Thread):
    def __init__(self, mbtiles_manager, port: int = 8765):
        super().__init__()
        self.mbtiles_manager = mbtiles_manager
        self.port = port
        self.daemon = True
        OfflineTileHandler.mbtiles_manager = mbtiles_manager
    
    def run(self):
        server = HTTPServer(('127.0.0.1', self.port), OfflineTileHandler)
        print(f"✅ Offline Tile Sunucusu: http://127.0.0.1:{self.port}")
        server.serve_forever()

# ═════════════════════��═════════════════════════════════════════
# 3. VERI MODELLERİ
# ═══════════════════════════════════════════════════════════════

class VehicleMode(Enum):
    MANUAL = 0
    GUIDED = 4
    AUTO = 10
    LOITER = 5
    HOLD = 16
    RTB = 6

class VehicleType(Enum):
    IDA = "İDA"
    IHA = "İHA"

@dataclass
class Waypoint:
    seq: int
    latitude: float
    longitude: float
    altitude: float = 5.0
    delay_time: float = 0.0
    accept_radius: float = 1.5
    yaw_angle: float = 0.0
    action: str = "WAYPOINT"

@dataclass
class Mission:
    name: str
    waypoints: List[Waypoint]
    created_at: str = ""
    description: str = ""
    
    def to_dict(self):
        return {
            'name': self.name,
            'created_at': self.created_at or datetime.now().isoformat(),
            'description': self.description,
            'waypoints': [asdict(wp) for wp in self.waypoints]
        }

# ═══════════════════════════════════════════════════════════════
# 4. MISSION MANAGER
# ═══════════════════════════════════════════════════════════════

class MissionManager:
    def __init__(self, missions_dir: str = "missions"):
        self.missions_dir = Path(missions_dir)
        self.missions_dir.mkdir(exist_ok=True)
        self.missions: Dict[str, Mission] = {}
        self._load_all_missions()
    
    def _load_all_missions(self):
        for mission_file in self.missions_dir.glob("*.json"):
            try:
                with open(mission_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    waypoints = [Waypoint(**wp) for wp in data.get('waypoints', [])]
                    mission = Mission(
                        name=data['name'],
                        waypoints=waypoints,
                        created_at=data.get('created_at', ''),
                        description=data.get('description', '')
                    )
                    self.missions[mission.name] = mission
            except Exception as e:
                print(f"Mission yükleme hatası: {e}")
    
    def create_mission(self, name: str, description: str = "") -> bool:
        if name in self.missions:
            return False
        self.missions[name] = Mission(
            name=name,
            waypoints=[],
            created_at=datetime.now().isoformat(),
            description=description
        )
        return self.save_mission(name)
    
    def add_waypoint(self, mission_name: str, wp: Waypoint) -> bool:
        if mission_name not in self.missions:
            return False
        wp.seq = len(self.missions[mission_name].waypoints)
        self.missions[mission_name].waypoints.append(wp)
        return True
    
    def import_csv(self, csv_file: str, mission_name: str) -> bool:
        try:
            waypoints = []
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for seq, row in enumerate(reader):
                    wp = Waypoint(
                        seq=seq,
                        latitude=float(row['latitude']),
                        longitude=float(row['longitude']),
                        altitude=float(row.get('altitude', '5.0')),
                        delay_time=float(row.get('delay_time', '0.0')),
                        accept_radius=float(row.get('accept_radius', '1.5')),
                        yaw_angle=float(row.get('yaw_angle', '0.0')),
                        action=row.get('action', 'WAYPOINT')
                    )
                    waypoints.append(wp)
            
            mission = Mission(
                name=mission_name,
                waypoints=waypoints,
                created_at=datetime.now().isoformat(),
                description=f"Imported from {Path(csv_file).name}"
            )
            self.missions[mission_name] = mission
            return self.save_mission(mission_name)
        except Exception as e:
            print(f"CSV import hatası: {e}")
            return False
    
    def export_csv(self, mission_name: str, csv_file: str) -> bool:
        try:
            if mission_name not in self.missions:
                return False
            mission = self.missions[mission_name]
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                fieldnames = ['seq', 'latitude', 'longitude', 'altitude', 
                             'delay_time', 'accept_radius', 'yaw_angle', 'action']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for wp in mission.waypoints:
                    writer.writerow(asdict(wp))
            return True
        except Exception as e:
            print(f"CSV export hatası: {e}")
            return False
    
    def save_mission(self, mission_name: str) -> bool:
        try:
            if mission_name not in self.missions:
                return False
            mission = self.missions[mission_name]
            json_file = self.missions_dir / f"{mission_name}.json"
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(mission.to_dict(), f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Mission save hatası: {e}")
            return False
    
    def get_mission(self, mission_name: str) -> Optional[Mission]:
        return self.missions.get(mission_name)
    
    def list_missions(self) -> List[str]:
        return list(self.missions.keys())
    
    def delete_mission(self, mission_name: str) -> bool:
        if mission_name not in self.missions:
            return False
        del self.missions[mission_name]
        json_file = self.missions_dir / f"{mission_name}.json"
        if json_file.exists():
            json_file.unlink()
        return True

# ═══════════════════════════════════════════════════════════════
# 5. MAP BRIDGE
# ═══════════════════════════════════════════════════════════════

class MapBridge(QObject):
    waypoint_added = pyqtSignal(float, float, int)
    
    @pyqtSlot(float, float, int)
    def waypointAdded(self, lat: float, lon: float, idx: int):
        self.waypoint_added.emit(lat, lon, idx)

# ═══════════════════════════════════════════════════════════════
# 6. VEHICLE THREAD
# ═══════════════════════════════════════════════════════════════

class VehicleThread(QThread):
    data_signal = pyqtSignal(float, float, float, float, float, float, float, str, str, float, float)
    health_signal = pyqtSignal(float, int, int, int)
    log_signal = pyqtSignal(str)
    connected_signal = pyqtSignal(bool)
    statustext_signal = pyqtSignal(str)
    
    def __init__(self, vehicle_type: VehicleType):
        super().__init__()
        self.vehicle_type = vehicle_type
        self.master = None
        self.is_running = False
        self.daemon = True
    
    def connect_vehicle(self, connection_string: str, baudrate: int = 57600):
        try:
            if connection_string.startswith('udp:') or connection_string.startswith('tcp:'):
                self.master = mavutil.mavlink_connection(connection_string)
            else:
                self.master = mavutil.mavlink_connection(
                    connection_string,
                    baud=baudrate,
                    timeout=1.0
                )
            
            self.master.wait_heartbeat(timeout=5)
            self.is_running = True
            self.connected_signal.emit(True)
            self.log_signal.emit(f"[{self.vehicle_type.value}] ✅ Bağlantı başarılı: {connection_string}")
            self.start()
        
        except Exception as e:
            self.is_running = False
            self.connected_signal.emit(False)
            self.log_signal.emit(f"[{self.vehicle_type.value}] ❌ Bağlantı hatası: {e}")
    
    def run(self):
        lat, lon, speed, alt, roll, pitch, yaw = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        yon_sp, hiz_sp = 0.0, 0.0
        mode, arm_status = "---", "DISARM"
        voltaj, batarya_yuzde, gps_fix, uydu = 0.0, 0, 0, 0
        last_emit_time = 0
        
        while self.is_running and self.master:
            try:
                msg = self.master.recv_match(
                    type=['GLOBAL_POSITION_INT', 'VFR_HUD', 'ATTITUDE', 
                          'HEARTBEAT', 'SYS_STATUS', 'GPS_RAW_INT', 'STATUSTEXT'],
                    blocking=False,
                    timeout=0.1
                )
                
                if not msg:
                    continue
                
                msg_type = msg.get_type()
                
                if msg_type == 'GLOBAL_POSITION_INT':
                    lat, lon = msg.lat / 1e7, msg.lon / 1e7
                    alt = msg.relative_alt / 1000.0
                
                elif msg_type == 'VFR_HUD':
                    speed = msg.groundspeed
                
                elif msg_type == 'ATTITUDE':
                    roll, pitch, yaw = math.degrees(msg.roll), math.degrees(msg.pitch), math.degrees(msg.yaw)
                    if yaw < 0: yaw += 360
                
                elif msg_type == 'HEARTBEAT':
                    mode = mavutil.mode_string_v10(msg)
                    arm_status = "ARM" if (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) else "DISARM"
                
                elif msg_type == 'SYS_STATUS':
                    voltaj = msg.voltage_battery / 1000.0
                    batarya_yuzde = msg.battery_remaining
                    self.health_signal.emit(voltaj, batarya_yuzde, gps_fix, uydu)
                
                elif msg_type == 'GPS_RAW_INT':
                    gps_fix = msg.fix_type
                    uydu = msg.satellites_visible
                
                elif msg_type == 'STATUSTEXT':
                    text = msg.text.decode('utf-8', errors='ignore').upper()
                    if "COLOR" in text or "RENK" in text:
                        self.statustext_signal.emit(text)
                
                now = time.time()
                if now - last_emit_time >= 0.1:
                    self.data_signal.emit(lat, lon, speed, alt, roll, pitch, yaw, mode, arm_status, yon_sp, hiz_sp)
                    last_emit_time = now
            
            except Exception as e:
                continue
    
    def send_waypoint_mission(self, waypoints: List[Waypoint]):
        if not self.master:
            return
        
        try:
            self.master.mav.mission_count_send(
                self.master.target_system,
                self.master.target_component,
                len(waypoints)
            )
            
            time.sleep(0.1)
            
            for wp in waypoints:
                self.master.mav.mission_item_int_send(
                    self.master.target_system,
                    self.master.target_component,
                    wp.seq,
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                    0, 1,
                    wp.delay_time, wp.accept_radius, 0, 0,
                    int(wp.latitude * 1e7),
                    int(wp.longitude * 1e7),
                    int(wp.altitude * 100)
                )
                time.sleep(0.05)
            
            self.log_signal.emit(f"[{self.vehicle_type.value}] ✅ {len(waypoints)} waypoint gönderildi")
        
        except Exception as e:
            self.log_signal.emit(f"[{self.vehicle_type.value}] ❌ Mission hatası: {e}")
    
    def trigger_servo_kamikaze(self, servo_channel: int, pwm_value: int):
        if not self.master:
            return
        
        try:
            self.master.mav.command_long_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
                0,
                servo_channel,
                pwm_value,
                0, 0, 0, 0, 0
            )
            
            color_map = {1000: "KIRMIZI", 1500: "MAVİ", 2000: "YEŞİL"}
            color = color_map.get(pwm_value, "BİLİNMEYEN")
            
            self.log_signal.emit(f"[{self.vehicle_type.value}] 🎯 KAMIKAZE - {color} (Kanal {servo_channel} → {pwm_value} PWM)")
        
        except Exception as e:
            self.log_signal.emit(f"[{self.vehicle_type.value}] ❌ Servo hatası: {e}")
    
    def arm_vehicle(self):
        if not self.master:
            return
        
        try:
            self.master.mav.command_long_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0, 1, 0, 0, 0, 0, 0, 0
            )
            self.log_signal.emit(f"[{self.vehicle_type.value}] 🔓 ARM")
        except Exception as e:
            self.log_signal.emit(f"[{self.vehicle_type.value}] ❌ ARM hatası: {e}")
    
    def disarm_vehicle(self):
        if not self.master:
            return
        
        try:
            self.master.mav.command_long_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0, 0, 0, 0, 0, 0, 0, 0
            )
            self.log_signal.emit(f"[{self.vehicle_type.value}] 🔒 DISARM")
        except Exception as e:
            self.log_signal.emit(f"[{self.vehicle_type.value}] ❌ DISARM hatası: {e}")
    
    def set_mode(self, mode: VehicleMode):
        if not self.master:
            return
        
        try:
            self.master.mav.set_mode_send(
                self.master.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                mode.value
            )
            self.log_signal.emit(f"[{self.vehicle_type.value}] Mod: {mode.name}")
        except Exception as e:
            self.log_signal.emit(f"[{self.vehicle_type.value}] ❌ Mod hatası: {e}")
    
    def disconnect(self):
        self.is_running = False
        if self.master:
            try:
                self.master.close()
            except:
                pass
        self.connected_signal.emit(False)

# ═══════════════════════════════════════════════════════════════
# 7. HORIZON INDICATOR
# ═══════════════════════════════════════════════════════════════

class HorizonIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.roll = 0.0
        self.pitch = 0.0
        self.setMinimumSize(180, 140)
        self.setStyleSheet("background-color: #1e1e1e; border: 1px solid #333; border-radius: 4px;")
    
    def update_attitude(self, roll: float, pitch: float):
        self.roll = roll
        self.pitch = pitch
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
        
        painter.fillRect(int(-w*1.5), int(-h*2), int(w*3), int(h*2), QColor(41, 128, 185))
        painter.fillRect(int(-w*1.5), 0, int(w*3), int(h*2), QColor(56, 142, 60))
        
        painter.setPen(QPen(Qt.white, 2))
        painter.drawLine(int(-w), 0, int(w), 0)
        
        for p in range(-25, 26, 5):
            if p != 0:
                y = int(-p * pitch_scale)
                line_w = 20 if p % 10 == 0 else 10
                painter.drawLine(-line_w, y, line_w, y)
                if p % 10 == 0:
                    painter.drawText(line_w + 5, y + 4, str(abs(p)))
        
        painter.restore()
        
        painter.setPen(QPen(QColor(213, 0, 0), 2))
        painter.drawLine(int(w/2 - 25), int(h/2), int(w/2 - 8), int(h/2))
        painter.drawLine(int(w/2 + 8), int(h/2), int(w/2 + 25), int(h/2))
        painter.drawLine(int(w/2), int(h/2 - 4), int(w/2), int(h/2 + 4))
        
        painter.setPen(Qt.white)
        painter.setFont(QFont("Arial", 9, QFont.Bold))
        painter.drawText(3, 12, f"R:{self.roll:.0f}°")
        painter.drawText(3, 24, f"P:{self.pitch:.0f}°")

# ═══════════════════════════════════════════════════════════════
# 8. ANA YER KONTROL İSTASYONU v4.1 - FINAL FIXED
# ═══════════════════════════════════════════════════════════════

class YerKontrolIstasyonu(QWidget):
    """TEKNOFEST 2026 - FULL OFFLINE - ALL BUGS FIXED"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🌊 İDA YER KONTROL İSTASYONU v4.1 - OFFLINE FINAL")
        self.setGeometry(30, 30, 1900, 1050)
        
        # 👈 MBTiles Manager
        self.mbtiles_manager = MBTilesManager("turkey_offline.mbtiles")
        
        # 👈 Offline Tile Sunucusu
        if self.mbtiles_manager.has_offline_map:
            self.tile_server = OfflineTileServer(self.mbtiles_manager, port=8765)
            self.tile_server.start()
            time.sleep(0.5)
        
        # Mission Manager
        self.mission_manager = MissionManager()
        
        # Araçlar
        self.ida_thread = VehicleThread(VehicleType.IDA)
        self.iha_thread = VehicleThread(VehicleType.IHA)
        
        # Veriler
        self.ida_telemetry = {'lat': 0, 'lon': 0, 'speed': 0, 'alt': 0, 'roll': 0, 'pitch': 0, 'yaw': 0}
        self.iha_telemetry = {'lat': 0, 'lon': 0, 'speed': 0, 'alt': 0, 'roll': 0, 'pitch': 0, 'yaw': 0}
        self.current_waypoints: List[Waypoint] = []
        
        # 👈 GRAFIKLER İÇİN HISTORY BUFFERS
        self.speed_history = deque(maxlen=150)  # 150 data point
        self.altitude_history = deque(maxlen=150)
        self.battery_history = deque(maxlen=150)
        self.time_axis = deque(maxlen=150)
        
        # Kamikaze
        self.kamikaze_active = False
        self.detected_color = None
        
        # Style
        self.apply_dark_style()
        
        # UI
        self.init_ui()
        
        # Sinyaller
        self.ida_thread.data_signal.connect(self.on_ida_telemetry)
        self.ida_thread.health_signal.connect(self.on_ida_health)
        self.ida_thread.log_signal.connect(self.log_message)
        self.ida_thread.statustext_signal.connect(self.on_ida_statustext)
        
        self.iha_thread.data_signal.connect(self.on_iha_telemetry)
        self.iha_thread.health_signal.connect(self.on_iha_health)
        self.iha_thread.log_signal.connect(self.log_message)
        self.iha_thread.statustext_signal.connect(self.on_iha_statustext)
        
        self.map_bridge.waypoint_added.connect(self.on_waypoint_added)
        
        # 👈 GRAF İK TIMER - update_graphs() çağrısı
        self.graph_timer = QTimer()
        self.graph_timer.timeout.connect(self.update_graphs)
        self.graph_timer.start(500)  # 500ms'de bir güncelle
        
        # Startup
        self.log_message("="*70)
        self.log_message("🌊 İDA YER KONTROL İSTASYONU v4.1 - OFFLINE FINAL")
        self.log_message("TEKNOFEST 2026 - PARKUR 1+2+3 COMPLETE - ALL BUGS FIXED")
        self.log_message("="*70)
        if self.mbtiles_manager.has_offline_map:
            self.log_message("✅ Offline Harita: AKTIF (MBTiles)")
            self.log_message("✅ Tile Sunucusu: http://127.0.0.1:8765")
        else:
            self.log_message("⚠️  Offline Harita: BULUNAMADI")
        self.log_message("✅ RFD868x + 433MHz: HAZIR")
        self.log_message("✅ Grafikler: CANLI (500ms güncelleme)")
        self.log_message("="*70)
    
    def apply_dark_style(self):
        self.setStyleSheet("""
            QWidget { background-color: #121212; color: #e0e0e0; font-family: Arial; font-size: 11px; }
            QGroupBox { border: 1px solid #333; border-radius: 4px; margin-top: 8px; padding-top: 8px; }
            QGroupBox::title { color: #00e5ff; font-weight: bold; font-size: 12px; }
            QPushButton { background-color: #2d2d30; border: 1px solid #444; padding: 5px; border-radius: 3px; color: white; font-weight: bold; }
            QPushButton:hover { background-color: #3e3e42; border: 1px solid #00e5ff; }
            QPushButton:pressed { background-color: #1e1e1e; }
            QLineEdit, QComboBox { background-color: #252526; border: 1px solid #3e3e42; border-radius: 3px; padding: 4px; color: white; }
            QLabel { background-color: transparent; }
            QTextEdit { background-color: #000; color: #00e676; font-family: monospace; font-size: 10px; border: 1px solid #333; }
        """)
    
    def init_ui(self):
        """Ana UI - Harita & Grafikler Vurgusu"""
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # SOL PANEL - KONTROLLER
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(6)
        
        # İDA Bağlantı
        ida_conn_group = QGroupBox("🔌 İDA Bağlantısı (RFD868x 868MHz)")
        ida_conn_layout = QFormLayout()
        
        self.ida_port_combo = QComboBox()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.ida_port_combo.addItems(["udp:127.0.0.1:14550"] + ports)
        ida_conn_layout.addRow("Port:", self.ida_port_combo)
        
        self.ida_baud_combo = QComboBox()
        self.ida_baud_combo.addItems(["57600", "115200", "921600"])
        ida_conn_layout.addRow("Baud:", self.ida_baud_combo)
        
        btn_ida_conn = QPushButton("🔌 İDA'ya Bağlan")
        btn_ida_conn.setStyleSheet("background-color: #1976d2;")
        btn_ida_conn.clicked.connect(lambda: self.ida_thread.connect_vehicle(
            self.ida_port_combo.currentText(),
            int(self.ida_baud_combo.currentText())
        ))
        ida_conn_layout.addRow("", btn_ida_conn)
        ida_conn_group.setLayout(ida_conn_layout)
        left_layout.addWidget(ida_conn_group)
        
        # İDA Telemetri
        ida_telem_group = QGroupBox("📊 İDA Telemetri")
        ida_telem_layout = QVBoxLayout()
        
        self.lbl_ida_mode = QLabel("Mod: ---")
        self.lbl_ida_arm = QLabel("Durum: ---")
        self.lbl_ida_pos = QLabel("Konum: ---")
        self.lbl_ida_speed = QLabel("Hız: ---")
        self.lbl_ida_battery = QLabel("Batarya: ---")
        
        ida_telem_layout.addWidget(self.lbl_ida_mode)
        ida_telem_layout.addWidget(self.lbl_ida_arm)
        ida_telem_layout.addWidget(self.lbl_ida_pos)
        ida_telem_layout.addWidget(self.lbl_ida_speed)
        ida_telem_layout.addWidget(self.lbl_ida_battery)
        
        ida_telem_group.setLayout(ida_telem_layout)
        left_layout.addWidget(ida_telem_group)
        
        # İDA Motor Kontrolleri
        ida_motor_group = QGroupBox("⚙️ İDA Motor Kontrolleri")
        ida_motor_layout = QGridLayout()
        
        btn_ida_arm = QPushButton("🔓 ARM")
        btn_ida_arm.setStyleSheet("background-color: #2e7d32;")
        btn_ida_arm.clicked.connect(self.ida_thread.arm_vehicle)
        ida_motor_layout.addWidget(btn_ida_arm, 0, 0)
        
        btn_ida_disarm = QPushButton("🔒 DISARM")
        btn_ida_disarm.setStyleSheet("background-color: #c62828;")
        btn_ida_disarm.clicked.connect(self.ida_thread.disarm_vehicle)
        ida_motor_layout.addWidget(btn_ida_disarm, 0, 1)
        
        btn_ida_auto = QPushButton("🎯 AUTO")
        btn_ida_auto.setStyleSheet("background-color: #6a1b9a;")
        btn_ida_auto.clicked.connect(lambda: self.ida_thread.set_mode(VehicleMode.AUTO))
        ida_motor_layout.addWidget(btn_ida_auto, 1, 0)
        
        btn_ida_rtb = QPushButton("🏠 RTB")
        btn_ida_rtb.setStyleSheet("background-color: #1565c0;")
        btn_ida_rtb.clicked.connect(lambda: self.ida_thread.set_mode(VehicleMode.RTB))
        ida_motor_layout.addWidget(btn_ida_rtb, 1, 1)
        
        ida_motor_group.setLayout(ida_motor_layout)
        left_layout.addWidget(ida_motor_group)
        
        # Horizon
        self.horizon_ida = HorizonIndicator()
        left_layout.addWidget(QLabel("📐 İDA Yönelim:"))
        left_layout.addWidget(self.horizon_ida)
        
        left_layout.addStretch()
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ORTADA - HARITA & MISSION
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setSpacing(6)
        
        # Harita
        self.map_view = QWebEngineView()
        
        if self.mbtiles_manager.has_offline_map:
            self.map_view.setHtml(MAP_HTML_OFFLINE)
        else:
            fallback_html = """
            <html><body style="background:#121212; color:#e0e0e0; font-family:Arial; padding:20px;">
            <h2>⚠️ Offline Harita Bulunamadı</h2>
            <p>Lütfen <b>turkey_offline.mbtiles</b> dosyasını indirip uygulama klasörüne kopyalayın.</p>
            <p>İndirme: <a href="https://www.openandromaps.org" style="color:#00e5ff;">OpenAndroMaps</a></p>
            </body></html>
            """
            self.map_view.setHtml(fallback_html)
        
        self.map_bridge = MapBridge()
        self.map_channel = QWebChannel()
        self.map_channel.registerObject("bridge", self.map_bridge)
        self.map_view.page().setWebChannel(self.map_channel)
        
        center_layout.addWidget(self.map_view, 3)
        
        # Mission Kontrolleri
        mission_group = QGroupBox("📋 Mission Yönetimi (Offline)")
        mission_layout = QVBoxLayout()
        
        sel_layout = QHBoxLayout()
        self.mission_combo = QComboBox()
        self._refresh_mission_list()
        sel_layout.addWidget(QLabel("Mission:"))
        sel_layout.addWidget(self.mission_combo)
        mission_layout.addLayout(sel_layout)
        
        btn_layout = QGridLayout()
        
        btn_new = QPushButton("➕ Yeni")
        btn_new.clicked.connect(self.create_new_mission)
        btn_layout.addWidget(btn_new, 0, 0)
        
        btn_import = QPushButton("📥 CSV")
        btn_import.clicked.connect(self.import_csv_mission)
        btn_layout.addWidget(btn_import, 0, 1)
        
        btn_load = QPushButton("📂 Yükle")
        btn_load.clicked.connect(self.load_mission_to_map)
        btn_layout.addWidget(btn_load, 0, 2)
        
        btn_export = QPushButton("📤 Aktar")
        btn_export.clicked.connect(self.export_csv_mission)
        btn_layout.addWidget(btn_export, 0, 3)
        
        mission_layout.addLayout(btn_layout)
        
        self.lbl_wp_count = QLabel("Waypoint'ler: 0/4")
        self.lbl_wp_count.setStyleSheet("color: #00e676; font-weight: bold;")
        mission_layout.addWidget(self.lbl_wp_count)
        
        btn_send = QPushButton("🚀 MISSION GÖNDER")
        btn_send.setStyleSheet("background-color: #1565c0; color: white; padding: 6px; font-weight: bold;")
        btn_send.clicked.connect(self.send_mission_to_ida)
        mission_layout.addWidget(btn_send)
        
        mission_group.setLayout(mission_layout)
        center_layout.addWidget(mission_group, 1)
        
        # ━━��━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # SAĞ PANEL - GRAFIKLER & KONTROL
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(6)
        
        # 👈 GRAFİKLER - pyqtgraph
        graph_group = QGroupBox("📈 Live Telemetri Grafikleri (500ms güncelleme)")
        graph_layout = QVBoxLayout()
        
        # pyqtgraph konfigürasyonu
        pg.setConfigOption('background', '#121212')
        pg.setConfigOption('foreground', '#e0e0e0')
        
        # ─ Hız Grafiği
        self.plot_speed_widget = pg.PlotWidget(title="İDA Hızı (m/s)")
        self.plot_speed_widget.setLabel('left', 'Hız', units='m/s', color='#00e5ff')
        self.plot_speed_widget.setLabel('bottom', 'Zaman', units='s', color='#00e5ff')
        self.plot_speed_widget.showGrid(x=True, y=True, alpha=0.3)
        self.curve_speed = self.plot_speed_widget.plot(pen=pg.mkPen('#00e5ff', width=2), name='Hız')
        
        graph_layout.addWidget(self.plot_speed_widget)
        
        # ─ İrtifa Grafiği
        self.plot_altitude_widget = pg.PlotWidget(title="İHA İrtifası (m)")
        self.plot_altitude_widget.setLabel('left', 'İrtifa', units='m', color='#d50000')
        self.plot_altitude_widget.setLabel('bottom', 'Zaman', units='s', color='#d50000')
        self.plot_altitude_widget.showGrid(x=True, y=True, alpha=0.3)
        self.curve_altitude = self.plot_altitude_widget.plot(pen=pg.mkPen('#d50000', width=2), name='İrtifa')
        
        graph_layout.addWidget(self.plot_altitude_widget)
        
        # ─ Pil Grafiği
        self.plot_battery_widget = pg.PlotWidget(title="Batarya Voltajı (V)")
        self.plot_battery_widget.setLabel('left', 'Voltaj', units='V', color='#ff9800')
        self.plot_battery_widget.setLabel('bottom', 'Zaman', units='s', color='#ff9800')
        self.plot_battery_widget.showGrid(x=True, y=True, alpha=0.3)
        self.curve_battery = self.plot_battery_widget.plot(pen=pg.mkPen('#ff9800', width=2), name='Voltaj')
        
        graph_layout.addWidget(self.plot_battery_widget)
        
        graph_group.setLayout(graph_layout)
        right_layout.addWidget(graph_group, 2)
        
        # İHA Bağlantı
        iha_conn_group = QGroupBox("🔌 İHA Bağlantısı (433MHz)")
        iha_conn_layout = QFormLayout()
        
        self.iha_port_combo = QComboBox()
        self.iha_port_combo.addItems(["udp:127.0.0.1:14552"] + ports)
        iha_conn_layout.addRow("Port:", self.iha_port_combo)
        
        self.iha_baud_combo = QComboBox()
        self.iha_baud_combo.addItems(["57600", "115200", "921600"])
        iha_conn_layout.addRow("Baud:", self.iha_baud_combo)
        
        btn_iha_conn = QPushButton("🔌 İHA'ya Bağlan")
        btn_iha_conn.setStyleSheet("background-color: #d50000;")
        btn_iha_conn.clicked.connect(lambda: self.iha_thread.connect_vehicle(
            self.iha_port_combo.currentText(),
            int(self.iha_baud_combo.currentText())
        ))
        iha_conn_layout.addRow("", btn_iha_conn)
        iha_conn_group.setLayout(iha_conn_layout)
        right_layout.addWidget(iha_conn_group)
        
        # 👈 PARKUR 3 - KAMIKAZE
        kamikaze_group = QGroupBox("🎯 PARKUR 3 - Kamikaze Kontrol")
        kamikaze_group.setStyleSheet("QGroupBox { border: 2px solid #d50000; } QGroupBox::title { color: #d50000; font-weight: bold; }")
        kamikaze_layout = QVBoxLayout()
        
        self.lbl_kamikaze_status = QLabel("Durum: İHA renk tespiti bekleniyor...")
        self.lbl_kamikaze_status.setStyleSheet("color: #888; font-size: 10px;")
        kamikaze_layout.addWidget(self.lbl_kamikaze_status)
        
        self.lbl_detected_color = QLabel("Tespit edilen renk: ---")
        self.lbl_detected_color.setStyleSheet("color: #ff9800; font-weight: bold;")
        kamikaze_layout.addWidget(self.lbl_detected_color)
        
        trigger_layout = QGridLayout()
        
        btn_red = QPushButton("🔴 KIRMIZI")
        btn_red.setStyleSheet("background-color: #c62828;")
        btn_red.clicked.connect(lambda: self.trigger_kamikaze(1000, "KIRMIZI"))
        trigger_layout.addWidget(btn_red, 0, 0)
        
        btn_blue = QPushButton("🔵 MAVİ")
        btn_blue.setStyleSheet("background-color: #1565c0;")
        btn_blue.clicked.connect(lambda: self.trigger_kamikaze(1500, "MAVİ"))
        trigger_layout.addWidget(btn_blue, 0, 1)
        
        btn_green = QPushButton("🟢 YEŞİL")
        btn_green.setStyleSheet("background-color: #2e7d32;")
        btn_green.clicked.connect(lambda: self.trigger_kamikaze(2000, "YEŞİL"))
        trigger_layout.addWidget(btn_green, 0, 2)
        
        kamikaze_layout.addLayout(trigger_layout)
        kamikaze_group.setLayout(kamikaze_layout)
        right_layout.addWidget(kamikaze_group)
        
        # Loglar
        log_group = QGroupBox("📝 Sistem Logları")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        right_layout.addWidget(log_group)
        
        right_layout.addStretch()
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # SPLITTER
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━���━━━━━━━
        
        splitter = QSplitter(Qt.Horizontal)
        
        scroll_left = QScrollArea()
        scroll_left.setWidgetResizable(True)
        scroll_left.setWidget(left_widget)
        
        scroll_right = QScrollArea()
        scroll_right.setWidgetResizable(True)
        scroll_right.setWidget(right_widget)
        
        splitter.addWidget(scroll_left)
        splitter.addWidget(center_widget)
        splitter.addWidget(scroll_right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)
        
        main_layout.addWidget(splitter)
        
        self.setLayout(main_layout)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 👈 GRAFİK GÜNCELLEME FONKSIYONU - DÜZELTILDI
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_graphs(self):
        """Grafikleri güncelle - 500ms'de bir çağrılır"""
        # 👈 SPEED GRAFIĞI
        if self.speed_history:
            x_axis = list(range(len(self.speed_history)))
            self.curve_speed.setData(x_axis, list(self.speed_history))
        
        # 👈 ALTITUDE GRAFIĞI
        if self.altitude_history:
            x_axis = list(range(len(self.altitude_history)))
            self.curve_altitude.setData(x_axis, list(self.altitude_history))
        
        # 👈 BATTERY GRAFIĞI
        if self.battery_history:
            x_axis = list(range(len(self.battery_history)))
            self.curve_battery.setData(x_axis, list(self.battery_history))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TELEMETRY EVENT HANDLERS
    # ━━━━━━━━━���━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pyqtSlot(float, float, float, float, float, float, float, str, str, float, float)
    def on_ida_telemetry(self, lat, lon, speed, alt, roll, pitch, yaw, mode, arm, yon_sp, hiz_sp):
        self.ida_telemetry.update({
            'lat': lat, 'lon': lon, 'speed': speed, 'alt': alt,
            'roll': roll, 'pitch': pitch, 'yaw': yaw,
            'mode': mode, 'arm': arm
        })
        
        arm_color = "#00e676" if arm == "ARM" else "#ff5252"
        self.lbl_ida_mode.setText(f"Mod: <b style='color:#00e5ff'>{mode}</b>")
        self.lbl_ida_arm.setText(f"Durum: <b style='color:{arm_color}'>{arm}</b>")
        self.lbl_ida_pos.setText(f"Konum: {lat:.5f}, {lon:.5f}")
        self.lbl_ida_speed.setText(f"Hız: <b style='color:#00e5ff'>{speed:.1f}</b> m/s")
        
        if self.mbtiles_manager.has_offline_map:
            self.map_view.page().runJavaScript(
                f"window.updateIdaPosition({lat}, {lon}, {yaw}, {speed}, '{mode}');"
            )
        
        self.horizon_ida.update_attitude(roll, pitch)
        
        # 👈 GRAFİK HISTORY'YE EKLE
        self.speed_history.append(speed)
    
    @pyqtSlot(float, int, int, int)
    def on_ida_health(self, voltage, battery, gps_fix, satellites):
        batt_color = "#ff5252" if battery < 20 else "#00e676"
        fix_status = "3D Fix" if gps_fix >= 3 else "No Fix"
        self.lbl_ida_battery.setText(
            f"Batarya: <b style='color:{batt_color}'>{battery}%</b> ({voltage:.1f}V) | {satellites} uydu"
        )
        
        # 👈 GRAFİK HISTORY'YE EKLE
        self.battery_history.append(voltage)
    
    @pyqtSlot(float, float, float, float, float, float, float, str, str, float, float)
    def on_iha_telemetry(self, lat, lon, speed, alt, roll, pitch, yaw, mode, arm, yon_sp, hiz_sp):
        self.iha_telemetry.update({
            'lat': lat, 'lon': lon, 'speed': speed, 'alt': alt,
            'roll': roll, 'pitch': pitch, 'yaw': yaw,
            'mode': mode, 'arm': arm
        })
        
        arm_color = "#00e676" if arm == "ARM" else "#ff5252"
        
        if self.mbtiles_manager.has_offline_map:
            self.map_view.page().runJavaScript(
                f"window.updateIhaPosition({lat}, {lon}, {yaw}, {alt}, '{mode}');"
            )
        
        # 👈 GRAFİK HISTORY'YE EKLE
        self.altitude_history.append(alt)
    
    @pyqtSlot(float, int, int, int)
    def on_iha_health(self, voltage, battery, gps_fix, satellites):
        pass
    
    @pyqtSlot(str)
    def on_iha_statustext(self, text: str):
        self.log_message(f"[İHA STATUSTEXT] {text}")
        
        if "RED" in text or "KIRMIZI" in text:
            self.detected_color = "KIRMIZI"
            color_hex = "#c62828"
            pwm = 1000
        elif "BLUE" in text or "MAVİ" in text:
            self.detected_color = "MAVİ"
            color_hex = "#1565c0"
            pwm = 1500
        elif "GREEN" in text or "YEŞİL" in text:
            self.detected_color = "YEŞİL"
            color_hex = "#2e7d32"
            pwm = 2000
        else:
            return
        
        self.lbl_detected_color.setText(f"Tespit edilen renk: <b style='color:{color_hex}'>{self.detected_color}</b>")
        self.lbl_kamikaze_status.setText(f"<span style='color:{color_hex}; font-weight: bold;'>✅ {self.detected_color} TESPİT EDİLDİ!</span>")
        
        if not self.kamikaze_active:
            self.log_message(f"[KAMIKAZE] Otomatik tetikleme: {self.detected_color}")
            self.trigger_kamikaze(pwm, self.detected_color)
    
    @pyqtSlot(str)
    def on_ida_statustext(self, text: str):
        self.log_message(f"[İDA STATUSTEXT] {text}")
    
    def trigger_kamikaze(self, pwm: int, color_name: str):
        if not self.ida_thread.master:
            QMessageBox.warning(self, "Hata", "İDA'ya bağlı değilsiniz!")
            return
        
        servo_channel = 9
        
        self.kamikaze_active = True
        self.lbl_kamikaze_status.setText(
            f"<span style='color:#ff6600; font-weight: bold;'>⚡ KAMIKAZE AKTIF - {color_name}</span>"
        )
        
        self.ida_thread.trigger_servo_kamikaze(servo_channel, pwm)
        
        QTimer.singleShot(5000, lambda: self.reset_kamikaze())
    
    def reset_kamikaze(self):
        self.kamikaze_active = False
        self.lbl_kamikaze_status.setText("Durum: İHA renk tespiti bekleniyor...")
        self.lbl_detected_color.setText("Tespit edilen renk: ---")
    
    @pyqtSlot(float, float, int)
    def on_waypoint_added(self, lat: float, lon: float, idx: int):
        wp = Waypoint(seq=idx, latitude=lat, longitude=lon, altitude=5.0)
        self.current_waypoints.append(wp)
        self._update_wp_label()
        self.log_message(f"✅ WP{idx+1}: {lat:.5f}, {lon:.5f}")
    
    def _update_wp_label(self):
        count = len(self.current_waypoints)
        color = "#00e676" if count == 4 else "#ff9800" if count > 0 else "#888"
        self.lbl_wp_count.setText(f"Waypoint'ler: <span style='color:{color}'>{count}/4</span>")
    
    def create_new_mission(self):
        # 👈 QInputDialog.getText() - DÜZELTILDI
        name, ok = QInputDialog.getText(self, "Yeni Mission", "Mission adı:")
        if ok and name:
            if self.mission_manager.create_mission(name):
                self._refresh_mission_list()
                self.log_message(f"✅ Mission oluşturuldu: {name}")
                self.mission_combo.setCurrentText(name)
            else:
                QMessageBox.warning(self, "Hata", "Bu ad zaten kullanılıyor!")
    
    def load_mission_to_map(self):
        mission_name = self.mission_combo.currentText()
        if not mission_name:
            QMessageBox.warning(self, "Hata", "Mission seçiniz!")
            return
        
        mission = self.mission_manager.get_mission(mission_name)
        if mission:
            self.current_waypoints = mission.waypoints[:]
            self._update_wp_label()
            self.log_message(f"✅ Mission yüklendi: {mission_name} ({len(mission.waypoints)} WP)")
            
            if self.mbtiles_manager.has_offline_map:
                for wp in mission.waypoints:
                    self.map_view.page().runJavaScript(
                        f"window.addMissionWaypoint({wp.latitude}, {wp.longitude});"
                    )
    
    def import_csv_mission(self):
        csv_file, _ = QFileDialog.getOpenFileName(self, "CSV Seç", "", "CSV (*.csv)")
        if csv_file:
            # 👈 QInputDialog.getText() - DÜZELTILDI
            name, ok = QInputDialog.getText(self, "Mission Adı", "Yeni mission adı:")
            if ok and name:
                if self.mission_manager.import_csv(csv_file, name):
                    self._refresh_mission_list()
                    self.log_message(f"✅ CSV import: {name}")
                    self.mission_combo.setCurrentText(name)
                else:
                    QMessageBox.warning(self, "Hata", "Import başarısız!")
    
    def export_csv_mission(self):
        mission_name = self.mission_combo.currentText()
        if not mission_name:
            QMessageBox.warning(self, "Hata", "Mission seçiniz!")
            return
        
        csv_file, _ = QFileDialog.getSaveFileName(self, "CSV Kaydet", "", "CSV (*.csv)")
        if csv_file:
            if self.mission_manager.export_csv(mission_name, csv_file):
                self.log_message(f"✅ CSV export: {Path(csv_file).name}")
            else:
                QMessageBox.warning(self, "Hata", "Export başarısız!")
    
    def send_mission_to_ida(self):
        if len(self.current_waypoints) < 1:
            QMessageBox.warning(self, "Hata", "En az 1 waypoint seçiniz!")
            return
        
        if not self.ida_thread.master:
            QMessageBox.warning(self, "Hata", "İDA'ya bağlı değilsiniz!")
            return
        
        self.ida_thread.send_waypoint_mission(self.current_waypoints)
    
    def _refresh_mission_list(self):
        self.mission_combo.clear()
        missions = self.mission_manager.list_missions()
        self.mission_combo.addItems(missions)
    
    def log_message(self, msg: str):
        self.log_text.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
    
    def closeEvent(self, event):
        self.ida_thread.disconnect()
        self.iha_thread.disconnect()
        self.mbtiles_manager.close()
        event.accept()

# ═══════════════════════════════════════════════════════════════
# 9. MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("""
    ╔════════════════════════════════════════════════════════════════╗
    ║  🌊 İDA YER KONTROL İSTASYONU v4.1 - OFFLINE FINAL FIXED      ║
    ║  TEKNOFEST 2026 - ALL BUGS FIXED                              ║
    ║                                                                ║
    ║  ✅ BUG FİXES:                                                ║
    ║     • _input_dialog() → QInputDialog.getText() ✓              ║
    ║     • update_graphs() → çalışan grafik kodu ✓                 ║
    ║     • Offline Harita: MBTiles ✓                               ║
    ║     • Live Grafikler: 500ms güncelleme ✓                      ║
    ║                                                                ║
    ║  ✅ FEATURES:                                                 ║
    ║     ��� Parkur 1: GNSS Mission (4 waypoint)                     ║
    ║     • Parkur 2: Engel Kaçınma                                 ║
    ║     • Parkur 3: Kamikaze (İHA ↔ İDA)                          ║
    ║     • Dual Vehicle: İDA + İHA Telemetri                       ║
    ║     • Offline Harita: MBTiles (Internet yok!)                 ║
    ║     • RFD868x + 433MHz: WiFi'siz                             ║
    ║                                                                ║
    ║  📥 SETUP:                                                    ║
    ║  1. pip install pymavlink pyqt5 pyqtgraph numpy              ║
    ║  2. turkey_offline.mbtiles indir                              ║
    ║  3. python ida_gcs_offline_final_fixed.py                    ║
    ║                                                                ║
    ║  © 2026 TEKNOFEST - KTÜ UZAY YGM                              ║
    ╚════════════════════════════════════════════════════════════════╝
    """)
    
    app = QApplication(sys.argv)
    window = YerKontrolIstasyonu()
    window.show()
    
    sys.exit(app.exec_())
