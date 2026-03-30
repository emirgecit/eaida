#!/usr/bin/env python3
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
ORANGE_AVOID_DIST = 12.0

LANE_WIDTH_MIN = 5.0
LANE_WIDTH_MAX = 12.0
LANE_DIST_TOLERANCE = 1.0
LANE_YELLOW_BUFFER = 4.0

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
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dLon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def get_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def send_pwm(master, throttle, steering):
    if master is None:
        return
    rc = [65535] * 8
    rc[0] = int(np.clip(steering, 1100, 1900))
    rc[2] = int(np.clip(throttle, 1100, 1900))
    master.mav.rc_channels_override_send(master.target_system, master.target_component, *rc)


class FixedMapPathPlanner:
    def __init__(self):
        self.lanes = []
        self.planned_path = []
        self.current_target = None
        self.current_target_idx = 0
        self.debug_info = ""

    def is_lane_safe_from_yellow(self, lane_lat, lane_lon, yellow_buoys):
        for y in yellow_buoys:
            if get_distance(lane_lat, lane_lon, y['lat'], y['lon']) < LANE_YELLOW_BUFFER:
                return False
        return True

    def build_clean_lanes(self, orange_buoys, target_lat, target_lon, yellow_buoys):
        self.lanes = []
        if len(orange_buoys) < 2:
            self.debug_info = f"orange:{len(orange_buoys)}"
            return []

        buoys = []
        for i, b in enumerate(orange_buoys):
            buoys.append({
                'index': i,
                'buoy': b,
                'dist_to_target': get_distance(b['lat'], b['lon'], target_lat, target_lon),
                'used': False
            })
        buoys.sort(key=lambda x: x['dist_to_target'])

        for i in range(len(buoys)):
            if buoys[i]['used']:
                continue

            best_j = -1
            best_dist = float('inf')

            for j in range(i + 1, min(i + 4, len(buoys))):
                if buoys[j]['used']:
                    continue

                d = get_distance(
                    buoys[i]['buoy']['lat'], buoys[i]['buoy']['lon'],
                    buoys[j]['buoy']['lat'], buoys[j]['buoy']['lon']
                )
                if not (LANE_WIDTH_MIN < d < LANE_WIDTH_MAX):
                    continue

                dd = abs(buoys[i]['dist_to_target'] - buoys[j]['dist_to_target'])
                if dd > LANE_DIST_TOLERANCE:
                    continue

                if d < best_dist:
                    best_j = j
                    best_dist = d

            if best_j != -1:
                b1 = buoys[i]['buoy']
                b2 = buoys[best_j]['buoy']
                lane_lat = (b1['lat'] + b2['lat']) / 2
                lane_lon = (b1['lon'] + b2['lon']) / 2

                if not self.is_lane_safe_from_yellow(lane_lat, lane_lon, yellow_buoys):
                    continue

                self.lanes.append({
                    'lat': lane_lat,
                    'lon': lane_lon,
                    'width': best_dist,
                    'progress': get_distance(lane_lat, lane_lon, target_lat, target_lon),
                })
                buoys[i]['used'] = True
                buoys[best_j]['used'] = True

        self.lanes.sort(key=lambda x: x['progress'])
        self.debug_info = f"lanes:{len(self.lanes)}"
        return self.lanes

    def plan_path(self, boat_lat, boat_lon, orange_buoys, yellow_buoys, target_lat, target_lon):
        self.planned_path = []
        self.current_target_idx = 0

        self.build_clean_lanes(orange_buoys, target_lat, target_lon, yellow_buoys)

        self.planned_path.append({'lat': boat_lat, 'lon': boat_lon, 'type': 'start'})
        for lane in self.lanes:
            self.planned_path.append({'lat': lane['lat'], 'lon': lane['lon'], 'type': 'lane'})
        self.planned_path.append({'lat': target_lat, 'lon': target_lon, 'type': 'target'})

        if len(self.planned_path) > 1:
            wp = self.planned_path[1]
            self.current_target = (wp['lat'], wp['lon'])
            self.current_target_idx = 1
        else:
            self.current_target = (target_lat, target_lon)
            self.current_target_idx = 0

    def update_target(self, boat_lat, boat_lon):
        if not self.current_target or not self.planned_path:
            return self.current_target

        d = get_distance(boat_lat, boat_lon, self.current_target[0], self.current_target[1])
        if d < WAYPOINT_THRESHOLD and self.current_target_idx + 1 < len(self.planned_path):
            self.current_target_idx += 1
            wp = self.planned_path[self.current_target_idx]
            self.current_target = (wp['lat'], wp['lon'])

        return self.current_target

    def get_map(self, boat_lat, boat_lon, boat_heading, orange_buoys, yellow_buoys, target_lat, target_lon):
        w, h = 800, 800
        img = np.zeros((h, w, 3), dtype=np.uint8)

        if boat_lat == 0:
            cv2.putText(img, "GPS BEKLENIYOR", (240, 400), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            return img

        scale = 10.0
        cx, cy = w // 2, h // 2

        def to_xy(lat, lon):
            dist = get_distance(boat_lat, boat_lon, lat, lon)
            brg = get_bearing(boat_lat, boat_lon, lat, lon)
            x = cx + int(dist * scale * math.sin(math.radians(brg)))
            y = cy - int(dist * scale * math.cos(math.radians(brg)))
            return x, y

        tx, ty = to_xy(target_lat, target_lon)
        cv2.rectangle(img, (tx - 15, ty - 15), (tx + 15, ty + 15), (255, 0, 255), 2)

        for b in yellow_buoys:
            x, y = to_xy(b['lat'], b['lon'])
            cv2.circle(img, (x, y), 14, (0, 255, 255), -1)
            cv2.circle(img, (x, y), 14, (0, 0, 0), 1)

        for b in orange_buoys:
            x, y = to_xy(b['lat'], b['lon'])
            cv2.circle(img, (x, y), 8, (0, 165, 255), -1)

        cv2.rectangle(img, (cx - 8, cy - 8), (cx + 8, cy + 8), (0, 255, 0), 2)
        hx = cx + int(30 * math.sin(math.radians(boat_heading)))
        hy = cy - int(30 * math.cos(math.radians(boat_heading)))
        cv2.arrowedLine(img, (cx, cy), (hx, hy), (0, 255, 0), 2)

        if len(self.planned_path) > 1:
            pts = [to_xy(wp['lat'], wp['lon']) for wp in self.planned_path]
            for i in range(len(pts) - 1):
                cv2.line(img, pts[i], pts[i + 1], (255, 0, 255), 2)

        if self.current_target:
            x, y = to_xy(self.current_target[0], self.current_target[1])
            cv2.rectangle(img, (x - 10, y - 10), (x + 10, y + 10), (255, 255, 0), 2)

        cv2.putText(img, f"path:{len(self.planned_path)} lane:{len(self.lanes)}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1)
        return img


class SlamNavigator(Node):
    def __init__(self):
        super().__init__("usv_slam_nav")
        self.bridge = CvBridge()
        self.master = None

        try:
            self.master = mavutil.mavlink_connection(MAV_CONNECTION_STRING)
            self.master.wait_heartbeat(timeout=2)
            self.master.arducopter_arm()
        except:
            pass

        self.lat, self.lon, self.heading = 0.0, 0.0, 0.0
        self.speed = PWM_CRUISE
        self.state_msg = "GPS"

        self.map = {'yellow': [], 'orange': []}
        self.path_planner = FixedMapPathPlanner()
        self.last_plan_time = 0
        self.plan_interval = 1.5

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        self.create_subscription(Image, CAM_TOPIC, self.image_callback, qos)

        threading.Thread(target=self.mavlink_loop, daemon=True).start()
        threading.Thread(target=self.camera_loop, daemon=True).start()
        threading.Thread(target=self.drive_loop, daemon=True).start()

    def image_callback(self, msg):
        try:
            img = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            if not image_queue.full():
                image_queue.put_nowait(img)
        except:
            pass

    def mavlink_loop(self):
        while rclpy.ok():
            if self.master:
                try:
                    msg = self.master.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=0.5)
                    if msg:
                        self.lat = msg.lat / 1e7
                        self.lon = msg.lon / 1e7
                        self.heading = msg.hdg / 100.0
                except:
                    pass

    def process_buoy_observation(self, cx, bottom_y, color_type):
        if self.lat == 0:
            return

        angle_x = (cx - CENTER_X) * (FOV_H / FRAME_W)
        y_diff = bottom_y - (FRAME_H / 2)
        if y_diff <= 10:
            return

        angle_y = y_diff * (FOV_V / FRAME_H)
        dist_m = CAM_HEIGHT / math.tan(angle_y)
        if dist_m > MAX_SIGHT_DIST:
            return

        bearing = (self.heading + math.degrees(angle_x)) % 360
        R = 6378137.0
        d_lat = dist_m * math.cos(math.radians(bearing)) / R
        d_lon = dist_m * math.sin(math.radians(bearing)) / (R * math.cos(math.radians(self.lat)))

        obs_lat = self.lat + math.degrees(d_lat)
        obs_lon = self.lon + math.degrees(d_lon)

        closest, min_d = None, float('inf')
        for buoy in self.map[color_type]:
            d = get_distance(buoy['lat'], buoy['lon'], obs_lat, obs_lon)
            if d < min_d:
                min_d = d
                closest = buoy

        if closest and min_d < MERGE_DIST:
            w = 1.0 / (dist_m ** 2 + 0.1)
            closest['lat'] = (closest['lat'] * closest['weight'] + obs_lat * w) / (closest['weight'] + w)
            closest['lon'] = (closest['lon'] * closest['weight'] + obs_lon * w) / (closest['weight'] + w)
            closest['weight'] += w
        else:
            self.map[color_type].append({'lat': obs_lat, 'lon': obs_lon, 'weight': 1.0 / (dist_m ** 2 + 0.1)})

    def camera_loop(self):
        while rclpy.ok():
            try:
                frame = image_queue.get(timeout=1.0)
                frame = cv2.resize(frame, (FRAME_W, FRAME_H))
                hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (3, 3), 0), cv2.COLOR_BGR2HSV)

                def extract(ranges, ctype, draw):
                    mask = None
                    for lo, hi in ranges:
                        m = cv2.inRange(hsv, lo, hi)
                        mask = m if mask is None else cv2.bitwise_or(mask, m)

                    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    for c in cnts:
                        if cv2.contourArea(c) < MIN_CONTOUR_AREA:
                            continue
                        x, y, w, h = cv2.boundingRect(c)
                        cx = x + w // 2
                        by = y + h
                        cv2.rectangle(frame, (x, y), (x + w, by), draw, 2)
                        self.process_buoy_observation(cx, by, ctype)

                extract(YELLOW_RANGES, 'yellow', (0, 255, 255))
                extract(ORANGE_RANGES, 'orange', (0, 165, 255))

                cv2.putText(frame, self.state_msg, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 1)
                cv2.imshow("1. Kamera", frame)

                map_img = self.path_planner.get_map(
                    self.lat, self.lon, self.heading,
                    self.map['orange'], self.map['yellow'],
                    TARGET_LAT, TARGET_LON
                )
                cv2.imshow("2. Harita", map_img)
                cv2.waitKey(1)

            except Empty:
                pass

    def drive_loop(self):
        while rclpy.ok():
            if self.lat != 0:
                now = time.time()
                if now - self.last_plan_time > self.plan_interval:
                    self.path_planner.plan_path(
                        self.lat, self.lon,
                        self.map['orange'], self.map['yellow'],
                        TARGET_LAT, TARGET_LON
                    )
                    self.last_plan_time = now

                self.path_planner.update_target(self.lat, self.lon)

                if self.path_planner.current_target:
                    target_bearing = get_bearing(
                        self.lat, self.lon,
                        self.path_planner.current_target[0],
                        self.path_planner.current_target[1]
                    )
                else:
                    target_bearing = get_bearing(self.lat, self.lon, TARGET_LAT, TARGET_LON)

                avoidance = 0.0

                for b in self.map['yellow']:
                    d = get_distance(self.lat, self.lon, b['lat'], b['lon'])
                    if d < YELLOW_AVOID_DIST:
                        br = get_bearing(self.lat, self.lon, b['lat'], b['lon'])
                        rel = (br - self.heading + 540) % 360 - 180
                        if abs(rel) < 90:
                            f = ((YELLOW_AVOID_DIST - d) / YELLOW_AVOID_DIST) ** 2
                            avoidance += -f * 30 if rel > 0 else f * 30

                for b in self.map['orange']:
                    d = get_distance(self.lat, self.lon, b['lat'], b['lon'])
                    if d < ORANGE_AVOID_DIST:
                        br = get_bearing(self.lat, self.lon, b['lat'], b['lon'])
                        rel = (br - self.heading + 540) % 360 - 180
                        if abs(rel) < 90:
                            f = ((ORANGE_AVOID_DIST - d) / ORANGE_AVOID_DIST) ** 2
                            avoidance += -f * 15 if rel > 0 else f * 15

                avoidance = max(-35.0, min(35.0, avoidance))
                target_bearing = (target_bearing + avoidance) % 360

                heading_error = (target_bearing - self.heading + 540) % 360 - 180
                steering = 1500 + int(heading_error * STEERING_GAIN)
                steering = max(1100, min(1900, steering))

                self.speed = PWM_CRUISE
                if get_distance(self.lat, self.lon, TARGET_LAT, TARGET_LON) < 4.0:
                    self.speed = PWM_STOP
                    steering = 1500
                    self.state_msg = "HEDEF"
                else:
                    self.state_msg = "ROTADA"

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
