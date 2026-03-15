#!/usr/bin/env python3
"""
🚤 USV SLAM Navigation v5.1 - CRITICAL FIXES (Mapping Logic RESTORED)
✅ Orijinal haritalandırma mantığı geri alındı
✅ Sadece 4 kritik fix uygulandı
"""
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import numpy as np
import threading
import time
import math
from pymavlink import mavutil
from queue import Queue, Empty

# ===================== AYARLAR =====================
CAM_TOPIC = "/usv/webcam/image_raw"
MAV_CONNECTION_STRING = "udp:127.0.0.1:14550"

FRAME_W, FRAME_H = 640, 480
CENTER_X = FRAME_W // 2

CAM_HEIGHT = 0.8       
FOV_H = 1.8            
FOV_V = FOV_H * (FRAME_H / FRAME_W) 

TARGET_LAT = -35.360921 
TARGET_LON = 149.165296

PWM_STOP = 1500
PWM_CRUISE = 1750       
STEERING_GAIN = 8       

MAX_SIGHT_DIST = 15.0   
MERGE_DIST = 4.0        
MIN_CONTOUR_AREA = 80   

YELLOW_AVOID_DIST = 15.0
ORANGE_AVOID_DIST = 12.0  # 👈 FİX #3: Turuncu'dan da kaçış

LANE_WIDTH_MIN = 5.0
LANE_WIDTH_MAX = 12.0
LANE_DIST_TOLERANCE = 1.0
LANE_YELLOW_BUFFER = 4.0

# 👈 FİX #1: Waypoint threshold 50m → 3.5m
WAYPOINT_THRESHOLD = 3.5

YELLOW_RANGES = [(np.array([20, 80, 80]), np.array([40, 255, 255]))]
ORANGE_RANGES = [
    (np.array([0, 80, 80]), np.array([18, 255, 255])),
    (np.array([160, 80, 80]), np.array([180, 255, 255]))
]

image_queue = Queue(maxsize=1)

def get_bearing(lat1, lon1, lat2, lon2):
    dLon = math.radians(lon2 - lon1)
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    y = math.sin(dLon) * math.cos(lat2)
    x = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dLon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def get_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    a = math.sin(math.radians(lat2-lat1)/2)**2 + \
        math.cos(math.radians(lat1))*math.cos(math.radians(lat2)) * \
        math.sin(math.radians(lon2-lon1)/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

def send_pwm(master, throttle, steering):
    if master is None: return
    rc = [65535]*8
    rc[0] = int(np.clip(steering, 1100, 1900))
    rc[2] = int(np.clip(throttle, 1100, 1900))
    master.mav.rc_channels_override_send(master.target_system, master.target_component, *rc)

# ═══════════════════════════════════════════════════════════════
# PATHFINDING (ORIJINAL MANTIK KORUNDU)
# ═══════════════════════════════════════════════════════════════

class FixedMapPathPlanner:
    """
    👈 ORC NAL HARITALANDIRMA MANTIGI
    - Orange-Orange kapıları (senin kodundaki gibi)
    - Sadece 4 kritik fix uygulandı
    """
    
    def __init__(self):
        self.lanes = []
        self.planned_path = []
        self.current_target = None
        self.current_target_idx = 0
        self.debug_info = ""
    
    def is_lane_safe_from_yellow(self, lane_lat, lane_lon, yellow_buoys):
        """Lane'de sarı var mı kontrol et"""
        for y in yellow_buoys:
            dist = get_distance(lane_lat, lane_lon, y['lat'], y['lon'])
            if dist < LANE_YELLOW_BUFFER:
                return False
        return True
    
    def build_clean_lanes(self, orange_buoys, target_lat, target_lon, yellow_buoys):
        """
        👈 ORIJINAL: Orange-Orange kapıları
        Sadece orange dubaları birbirleriyle eşleştir
        """
        
        self.lanes = []
        
        if len(orange_buoys) < 2:
            self.debug_info = f"Yok: {len(orange_buoys)} turuncu"
            return []
        
        # Dubaları hedefe olan mesafeye göre sırala
        buoys_with_dist = []
        for i, b in enumerate(orange_buoys):
            dist_to_target = get_distance(b['lat'], b['lon'], target_lat, target_lon)
            buoys_with_dist.append({
                'index': i,
                'buoy': b,
                'dist_to_target': dist_to_target,
                'used': False
            })
        
        buoys_with_dist.sort(key=lambda x: x['dist_to_target'])
        
        # Hedefe yakın sırada lane'leri oluştur
        for i in range(len(buoys_with_dist)):
            if buoys_with_dist[i]['used']:
                continue
            
            best_partner_idx = -1
            best_partner_dist = float('inf')
            
            for j in range(i + 1, min(i + 4, len(buoys_with_dist))):
                if buoys_with_dist[j]['used']:
                    continue
                
                # İki duba arası
                duba_dist = get_distance(
                    buoys_with_dist[i]['buoy']['lat'], buoys_with_dist[i]['buoy']['lon'],
                    buoys_with_dist[j]['buoy']['lat'], buoys_with_dist[j]['buoy']['lon']
                )
                
                # Kalite kontrol
                if not (LANE_WIDTH_MIN < duba_dist < LANE_WIDTH_MAX):
                    continue
                
                dist_diff = abs(buoys_with_dist[i]['dist_to_target'] - buoys_with_dist[j]['dist_to_target'])
                if dist_diff > LANE_DIST_TOLERANCE:
                    continue
                
                if duba_dist < best_partner_dist:
                    best_partner_idx = j
                    best_partner_dist = duba_dist
            
            if best_partner_idx != -1:
                b1 = buoys_with_dist[i]['buoy']
                b2 = buoys_with_dist[best_partner_idx]['buoy']
                
                lane_lat = (b1['lat'] + b2['lat']) / 2
                lane_lon = (b1['lon'] + b2['lon']) / 2
                
                # Sarı kontrolü
                if not self.is_lane_safe_from_yellow(lane_lat, lane_lon, yellow_buoys):
                    continue
                
                lane = {
                    'lat': lane_lat,
                    'lon': lane_lon,
                    'width': best_partner_dist,
                    'buoy_left': b1,
                    'buoy_right': b2,
                    'progress': get_distance(lane_lat, lane_lon, target_lat, target_lon),
                }
                
                self.lanes.append(lane)
                buoys_with_dist[i]['used'] = True
                buoys_with_dist[best_partner_idx]['used'] = True
        
        self.lanes.sort(key=lambda x: x['progress'])
        self.debug_info = f"{len(self.lanes)} lane OK"
        return self.lanes
    
    def plan_path_clean(self, boat_lat, boat_lon, orange_buoys, yellow_buoys,
                       target_lat, target_lon):
        """Path planlama"""
        
        self.planned_path = []
        self.current_target_idx = 0
        
        self.build_clean_lanes(orange_buoys, target_lat, target_lon, yellow_buoys)
        
        self.planned_path.append({
            'lat': boat_lat,
            'lon': boat_lon,
            'type': 'start'
        })
        
        for lane in self.lanes:
            self.planned_path.append({
                'lat': lane['lat'],
                'lon': lane['lon'],
                'type': 'lane',
                'width': lane['width']
            })
        
        self.planned_path.append({
            'lat': target_lat,
            'lon': target_lon,
            'type': 'target'
        })
        
        if len(self.planned_path) > 1:
            wp = self.planned_path[1]
            self.current_target = (wp['lat'], wp['lon'])
            self.current_target_idx = 1
        else:
            self.current_target = (target_lat, target_lon)
            self.current_target_idx = len(self.planned_path) - 1
        
        return self.planned_path
    
    def update_target(self, boat_lat, boat_lon):
        """
        👈 FİX #1: Waypoint threshold 50m → 3.5m
        """
        if not self.current_target or not self.planned_path:
            return self.current_target
        
        dist = get_distance(boat_lat, boat_lon, self.current_target[0], self.current_target[1])
        
        # 👈 KRITIK FİX: 50m → 3.5m
        if dist < WAYPOINT_THRESHOLD:
            if self.current_target_idx + 1 < len(self.planned_path):
                self.current_target_idx += 1
                wp = self.planned_path[self.current_target_idx]
                self.current_target = (wp['lat'], wp['lon'])
        
        return self.current_target
    
    def get_visualization(self, boat_lat, boat_lon, boat_heading,
                         orange_buoys, yellow_buoys, target_lat, target_lon):
        """Harita çiz"""
        MAP_W, MAP_H = 800, 800
        map_img = np.zeros((MAP_H, MAP_W, 3), dtype=np.uint8)
        
        if boat_lat == 0:
            cv2.putText(map_img, "GPS BEKLENIYOR...", (250, 400), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            return map_img
        
        SCALE = 10.0
        center_x = MAP_W // 2
        center_y = MAP_H // 2
        
        def latlon_to_xy(target_lat, target_lon):
            dist = get_distance(boat_lat, boat_lon, target_lat, target_lon)
            bearing = get_bearing(boat_lat, boat_lon, target_lat, target_lon)
            x = center_x + int(dist * SCALE * math.sin(math.radians(bearing)))
            y = center_y - int(dist * SCALE * math.cos(math.radians(bearing)))
            return x, y
        
        # HEDEF (MOR)
        tx, ty = latlon_to_xy(target_lat, target_lon)
        cv2.rectangle(map_img, (tx-15, ty-15), (tx+15, ty+15), (255, 0, 255), 2)
        cv2.putText(map_img, "T", (tx+20, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 1)
        
        # SARIYALAR (SARI - BÜ YÜK)
        for b in yellow_buoys:
            bx, by = latlon_to_xy(b['lat'], b['lon'])
            cv2.circle(map_img, (bx, by), 16, (0, 255, 255), -1)
            cv2.circle(map_img, (bx, by), 16, (0, 0, 0), 1)
        
        # TURUNCULAR (TURUNCU - ORTA)
        for b in orange_buoys:
            bx, by = latlon_to_xy(b['lat'], b['lon'])
            cv2.circle(map_img, (bx, by), 10, (0, 165, 255), -1)
        
        # BOAT (YEŞİL)
        boat_x, boat_y = center_x, center_y
        cv2.rectangle(map_img, (boat_x-8, boat_y-8), (boat_x+8, boat_y+8), (0, 255, 0), 2)
        
        hx = boat_x + int(30 * math.sin(math.radians(boat_heading)))
        hy = boat_y - int(30 * math.cos(math.radians(boat_heading)))
        cv2.arrowedLine(map_img, (boat_x, boat_y), (hx, hy), (0, 255, 0), 2)
        
        # PATH (MOR)
        if len(self.planned_path) > 1:
            path_pts = [latlon_to_xy(wp['lat'], wp['lon']) for wp in self.planned_path]
            for i in range(len(path_pts) - 1):
                cv2.line(map_img, path_pts[i], path_pts[i+1], (255, 0, 255), 2)
            
            for i, pt in enumerate(path_pts):
                if i == len(path_pts) - 1:
                    cv2.circle(map_img, pt, 3, (255, 0, 255), -1)
                elif i == 0:
                    cv2.circle(map_img, pt, 3, (0, 255, 0), -1)
        
        # SONRAKI HEDEF (CYAN)
        if self.current_target:
            ntx, nty = latlon_to_xy(self.current_target[0], self.current_target[1])
            cv2.rectangle(map_img, (ntx-10, nty-10), (ntx+10, nty+10), (255, 255, 0), 2)
        
        # INFO
        cv2.putText(map_img, "v5.1 - CRITICAL FIXES", (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 1)
        cv2.putText(map_img, f"Path: {len(self.planned_path)} | Lane: {len(self.lanes)}", 
                   (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (150, 150, 150), 1)
        cv2.putText(map_img, f"WP: {WAYPOINT_THRESHOLD}m | Plan: 1.5s | Orange kaçış", 
                   (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 100), 1)
        
        return map_img


class SlamNavigator(Node):
    def __init__(self):
        super().__init__("usv_slam_nav")
        self.bridge = CvBridge()
        self.get_logger().info("🚤 v5.1 - CRITICAL FIXES + ORIGINAL MAPPING")

        self.master = None
        try:
            self.master = mavutil.mavlink_connection(MAV_CONNECTION_STRING)
            self.master.wait_heartbeat(timeout=2)
            self.master.arducopter_arm()
        except: pass

        self.lat, self.lon, self.heading = 0.0, 0.0, 0.0
        self.speed = PWM_CRUISE
        
        self.map = {'yellow': [], 'orange': []}
        self.target_heading_vis = 0.0 
        self.state_msg = "GPS BEKLENIYOR..."
        
        self.path_planner = FixedMapPathPlanner()
        self.last_plan_time = 0
        # 👈 FİX #4: Replan interval 5.0 → 1.5
        self.plan_interval = 1.5

        qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=1)
        self.create_subscription(Image, CAM_TOPIC, self.image_callback, qos)

        threading.Thread(target=self.mavlink_loop, daemon=True).start()
        threading.Thread(target=self.camera_to_map_loop, daemon=True).start()
        threading.Thread(target=self.map_to_drive_loop, daemon=True).start()

    def image_callback(self, msg):
        try:
            img = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            if not image_queue.full(): 
                image_queue.put_nowait(img)
        except: pass

    def mavlink_loop(self):
        while rclpy.ok():
            if self.master:
                try:
                    msg = self.master.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=0.5)
                    if msg:
                        self.lat, self.lon, self.heading = msg.lat/1e7, msg.lon/1e7, msg.hdg/100.0
                except: pass

    def process_buoy_observation(self, cx, bottom_y, color_type):
        if self.lat == 0: return 
        
        angle_x = (cx - CENTER_X) * (FOV_H / FRAME_W)
        y_diff = bottom_y - (FRAME_H / 2)
        
        if y_diff <= 10: return 
        
        angle_y = y_diff * (FOV_V / FRAME_H)
        dist_m = CAM_HEIGHT / math.tan(angle_y)
        
        if dist_m > MAX_SIGHT_DIST: return 
        
        bearing = (self.heading + math.degrees(angle_x)) % 360
        R = 6378137.0
        d_lat = dist_m * math.cos(math.radians(bearing)) / R
        d_lon = dist_m * math.sin(math.radians(bearing)) / (R * math.cos(math.radians(self.lat)))
        
        obs_lat = self.lat + math.degrees(d_lat)
        obs_lon = self.lon + math.degrees(d_lon)
        
        closest_buoy = None
        min_distance = float('inf')
        
        for buoy in self.map[color_type]:
            d = get_distance(buoy['lat'], buoy['lon'], obs_lat, obs_lon)
            if d < min_distance:
                min_distance = d
                closest_buoy = buoy
                
        if closest_buoy and min_distance < MERGE_DIST:
            weight = 1.0 / (dist_m**2 + 0.1) 
            closest_buoy['lat'] = (closest_buoy['lat'] * closest_buoy['weight'] + obs_lat * weight) / (closest_buoy['weight'] + weight)
            closest_buoy['lon'] = (closest_buoy['lon'] * closest_buoy['weight'] + obs_lon * weight) / (closest_buoy['weight'] + weight)
            closest_buoy['weight'] += weight 
        else:
            self.map[color_type].append({'lat': obs_lat, 'lon': obs_lon, 'weight': 1.0 / (dist_m**2 + 0.1)})

    def generate_global_map_image(self):
        return self.path_planner.get_visualization(
            self.lat, self.lon, self.heading,
            self.map['orange'], self.map['yellow'],
            TARGET_LAT, TARGET_LON
        )

    def camera_to_map_loop(self):
        win_cam = "1. Kamera"
        win_map = "2. Harita"
        
        while rclpy.ok():
            try:
                frame = image_queue.get(timeout=1.0)
                frame_draw = cv2.resize(frame, (FRAME_W, FRAME_H))
                hsv = cv2.cvtColor(cv2.GaussianBlur(frame_draw, (3,3), 0), cv2.COLOR_BGR2HSV)

                def extract_buoys(ranges, color_type, draw_color):
                    mask = None
                    for lo, hi in ranges:
                        m = cv2.inRange(hsv, lo, hi)
                        mask = m if mask is None else cv2.bitwise_or(mask, m)
                    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
                    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    
                    for c in cnts:
                        if cv2.contourArea(c) < MIN_CONTOUR_AREA: continue
                        x, y, w, h = cv2.boundingRect(c)
                        cx = x + w//2
                        bottom_y = y + h
                        cv2.rectangle(frame_draw, (x,y), (x+w,bottom_y), draw_color, 2)
                        self.process_buoy_observation(cx, bottom_y, color_type)

                extract_buoys(YELLOW_RANGES, 'yellow', (0, 255, 255))
                extract_buoys(ORANGE_RANGES, 'orange', (0, 165, 255))

                cv2.putText(frame_draw, f"DURUM: {self.state_msg}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 1)
                cv2.imshow(win_cam, frame_draw)
                
                map_img = self.generate_global_map_image()
                cv2.imshow(win_map, map_img)
                cv2.waitKey(1)

            except Empty: pass

    def map_to_drive_loop(self):
        while rclpy.ok():
            if self.lat != 0:
                # 👈 FİX #4: Replan interval 1.5s
                current_time = time.time()
                if current_time - self.last_plan_time > self.plan_interval:
                    self.path_planner.plan_path_clean(
                        self.lat, self.lon,
                        self.map['orange'], self.map['yellow'],
                        TARGET_LAT, TARGET_LON
                    )
                    self.last_plan_time = current_time
                
                self.path_planner.update_target(self.lat, self.lon)
                
                if self.path_planner.current_target:
                    target_bearing = get_bearing(self.lat, self.lon, 
                                               self.path_planner.current_target[0],
                                               self.path_planner.current_target[1])
                else:
                    target_bearing = get_bearing(self.lat, self.lon, TARGET_LAT, TARGET_LON)
                
                avoidance_angle = 0.0
                
                # Sarı'dan kaçış
                for b in self.map['yellow']:
                    dist = get_distance(self.lat, self.lon, b['lat'], b['lon'])
                    if dist < YELLOW_AVOID_DIST:
                        bearing_to_buoy = get_bearing(self.lat, self.lon, b['lat'], b['lon'])
                        rel_angle = (bearing_to_buoy - self.heading + 540) % 360 - 180
                        
                        if abs(rel_angle) < 90:
                            force = ((YELLOW_AVOID_DIST - dist) / YELLOW_AVOID_DIST) ** 2
                            if rel_angle > 0:
                                avoidance_angle -= force * 30
                            else:
                                avoidance_angle += force * 30
                
                # 👈 FİX #3: Turuncu'dan da kaçış
                for b in self.map['orange']:
                    dist = get_distance(self.lat, self.lon, b['lat'], b['lon'])
                    if dist < ORANGE_AVOID_DIST:
                        bearing_to_buoy = get_bearing(self.lat, self.lon, b['lat'], b['lon'])
                        rel_angle = (bearing_to_buoy - self.heading + 540) % 360 - 180
                        
                        if abs(rel_angle) < 90:
                            force = ((ORANGE_AVOID_DIST - dist) / ORANGE_AVOID_DIST) ** 2
                            if rel_angle > 0:
                                avoidance_angle -= force * 15
                            else:
                                avoidance_angle += force * 15
                
                avoidance_angle = max(-35.0, min(35.0, avoidance_angle))
                target_bearing = (target_bearing + avoidance_angle) % 360
                self.target_heading_vis = target_bearing
                
                self.state_msg = "ROTADA"
                self.speed = PWM_CRUISE
                
                heading_error = (target_bearing - self.heading + 540) % 360 - 180
                steering = 1500 + int(heading_error * STEERING_GAIN)
                steering = max(1100, min(1900, steering))
                
                global_dist = get_distance(self.lat, self.lon, TARGET_LAT, TARGET_LON)
                if global_dist < 4.0 and global_dist > 0:
                    self.speed = PWM_STOP
                    steering = 1500
                    self.state_msg = "HEDEF!"

                send_pwm(self.master, self.speed, steering)
            
            time.sleep(0.05)

def main():
    rclpy.init()
    node = SlamNavigator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
