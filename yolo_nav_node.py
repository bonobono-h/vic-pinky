#!/usr/bin/env python3
"""
VicPinky YOLO 통합 노드
- NAV 모드: 사람/물체 감지 → Nav2 costmap obstacle 업데이트
- FOLLOW 모드: 사람 추종 → cmd_vel 직접 제어
- HOME 모드: Nav2로 이동 중 ArUco 마커(ID 0) 감지 → 정밀 도킹
- PARK 모드: Nav2로 주차 구역 이동 중 ArUco 마커(ID 1) 감지 → 후진 주차
- /robot_mode 토픽으로 모드 전환
"""
import math
import struct
import subprocess
import threading

import cv2
import numpy as np
import rclpy
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
import socket
from sensor_msgs.msg import Image, LaserScan, PointCloud2, PointField
from std_msgs.msg import String
from ultralytics import YOLO

# ── 홈 위치 ──────────────────────────────────────────────────
HOME_X   =  2.644761717360507
HOME_Y   = -1.215629370274564
HOME_QZ  =  0.043083
HOME_QW  =  0.999071

# ── 카메라 설정 ──────────────────────────────────────────────
CAM_WIDTH   = 640
CAM_HEIGHT  = 480
CAM_HFOV    = 60.0   # 웹캠 수평 화각 (도)
CAM_FX      = (CAM_WIDTH / 2.0) / math.tan(math.radians(CAM_HFOV / 2.0))  # ≈ 554px

# ── UDP 카메라 설정 ──────────────────────────────────────────
CAM_UDP_PORT = 5007   # VicPinky → 노트북 카메라 UDP 포트

# ── ArUco 도킹 설정 ──────────────────────────────────────────
ARUCO_DICT      = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
ARUCO_DET       = cv2.aruco.ArucoDetector(ARUCO_DICT)
DOCK_MARKER_ID  = 0       # HOME 도킹 마커
PARK_MARKER_ID  = 1       # 주차 마커
MARKER_REAL_SIZE = 0.15   # 마커 실제 크기 (m)
ARUCO_TRIGGER_DIST  = 2.35  # 이 거리 이내 감지 시 도킹 시작 (m)
DOCK_APPROACH_STOP  = 0.25  # 전진 도킹: 마커/전방 라이다 이 거리 이하 → 완료 (m)
MAX_APPROACH_DURATION = 30.0  # 전진 도킹 타임아웃 (s)

# ── 주차 위치 ────────────────────────────────────────────────
PARK_X  = HOME_X
PARK_Y  = HOME_Y
PARK_QZ = HOME_QZ
PARK_QW = HOME_QW

# ── 장애물로 처리할 YOLO 클래스 ─────────────────────────────
OBSTACLE_CLASSES = {
    'person':     0.8,
    'chair':      0.4,
    'bottle':     0.25,
    'cup':        0.25,
    'backpack':   0.35,
    'suitcase':   0.45,
    'bicycle':    0.5,
    'dog':        0.5,
    'cat':        0.4,
}

PILLAR_ANGLES_DEG = [162.0, 200.0, 245.3, 338.7, 23.3]
PILLAR_HALF_WIDTH = 10


class YoloNavNode(Node):
    def __init__(self):
        super().__init__('yolo_nav_node')

        # YOLO 모델
        self.model = YOLO('/home/hong/dev_ws/vic_pinky/yolov8n.pt')
        self.mode = 'nav'
        self.scan = None
        self.lock = threading.Lock()

        # ArUco 도킹 상태: None / 'aligning' / 'approaching'
        self.dock_state = None
        self.dock_approach_start = None
        self.dock_target = None  # 'home' or 'park'
        self.docked = False       # 도킹 완료 상태 (탈출 전진 필요)

        # UDP 카메라 최신 프레임
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        threading.Thread(target=self._udp_cam_thread, daemon=True).start()

        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            depth=1,
        )

        # 구독
        self.create_subscription(LaserScan, '/scan',       self.lidar_cb, sensor_qos)
        self.create_subscription(String,    '/robot_mode', self.mode_cb,  10)

        # 퍼블리셔
        self.cmd_pub      = self.create_publisher(Twist,       '/cmd_vel',           10)
        self.obstacle_pub = self.create_publisher(PointCloud2, '/camera_obstacles',  10)
        self.debug_pub    = self.create_publisher(Image,       '/yolo_debug',        10)

        # Nav2 goal 전체 취소 서비스 클라이언트
        self._cancel_client = self.create_client(
            CancelGoal, '/navigate_to_pose/_action/cancel_goal')

        from std_srvs.srv import Empty
        self._amcl_global_loc = self.create_client(Empty, '/reinitialize_global_localization')

        # Nav2 NavigateToPose 액션 클라이언트
        self._nav_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')

        # 카메라 프레임 처리 타이머 (20fps)
        self.create_timer(0.05, self._frame_timer_cb)

        self.get_logger().info('YoloNavNode 시작 — 모드: nav')

    # ── UDP 카메라 수신 스레드 ───────────────────────────────

    def _udp_cam_thread(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', CAM_UDP_PORT))
        sock.settimeout(1.0)
        while True:
            try:
                data, _ = sock.recvfrom(65536)
                frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    with self.frame_lock:
                        self.latest_frame = frame
            except socket.timeout:
                pass

    def _frame_timer_cb(self):
        with self.frame_lock:
            frame = self.latest_frame
        if frame is not None:
            self.image_cb(frame)

    # ── 콜백 ────────────────────────────────────────────────

    def lidar_cb(self, msg):
        with self.lock:
            self.scan = msg

    def mode_cb(self, msg):
        new_mode = msg.data.strip().lower()
        if new_mode == self.mode and self.dock_state is None and not self.docked:
            return
        prev_mode = self.mode
        self.mode = new_mode
        self.dock_state = None  # 모드 전환 시 도킹 상태 초기화
        self.get_logger().info(f'모드 전환: {prev_mode} → {self.mode}')

        if self.mode == 'follow':
            self._cancel_nav2_goals()
            self._stop_relay()
        elif self.mode == 'nav':
            if self.docked:
                self._cancel_nav2_goals()
                self._stop_relay()
                threading.Thread(target=self._exit_dock, daemon=True).start()
            else:
                self._start_relay()
        elif self.mode == 'home':
            self._cancel_nav2_goals()
            self._start_relay()
            self._go_home()
        elif self.mode == 'park':
            self._cancel_nav2_goals()
            self._start_relay()
            self._go_park()
        elif self.mode == 'stop':
            self._cancel_nav2_goals()
            self.cmd_pub.publish(Twist())

    def _exit_dock(self):
        """도킹 완료 위치에서 탈출: 후진으로 전방 라이다 2m 이상 이격"""
        self.get_logger().info('🔙 도킹 탈출: 후진 중...')
        twist = Twist()
        twist.linear.x = -0.15
        start = self.get_clock().now()
        import time as _time
        while True:
            elapsed = (self.get_clock().now() - start).nanoseconds / 1e9
            front = self._lidar_front_distance()
            if front is not None and front > 2.0:
                break
            if elapsed > 20.0:
                self.get_logger().warn('⚠️ 도킹 탈출 타임아웃')
                break
            self.cmd_pub.publish(twist)
            _time.sleep(0.05)
        self.cmd_pub.publish(Twist())
        self.docked = False
        self.get_logger().info('✅ 도킹 탈출 완료 — Nav 시작')
        self._start_relay()

    def _go_home(self):
        if not self._nav_client.wait_for_server(timeout_sec=3.0):
            self.get_logger().warn('NavigateToPose 서버 없음 — 홈 이동 취소')
            return
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = HOME_X
        goal.pose.pose.position.y = HOME_Y
        goal.pose.pose.orientation.z = HOME_QZ
        goal.pose.pose.orientation.w = HOME_QW
        goal.behavior_tree = ''
        future = self._nav_client.send_goal_async(goal)
        future.add_done_callback(self._home_goal_cb)
        self.get_logger().info(f'🏠 홈 이동 시작 (ArUco 대기 중): ({HOME_X:.2f}, {HOME_Y:.2f})')

    def _go_park(self):
        if not self._nav_client.wait_for_server(timeout_sec=3.0):
            self.get_logger().warn('NavigateToPose 서버 없음 — 주차 이동 취소')
            return
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = PARK_X
        goal.pose.pose.position.y = PARK_Y
        goal.pose.pose.orientation.z = PARK_QZ
        goal.pose.pose.orientation.w = PARK_QW
        goal.behavior_tree = ''
        future = self._nav_client.send_goal_async(goal)
        future.add_done_callback(
            lambda f: self.get_logger().info(f'🅿️ 주차 이동 시작 (ArUco 대기 중): ({PARK_X:.2f}, {PARK_Y:.2f})')
        )

    def _home_goal_cb(self, future):
        handle = future.result()
        if not handle.accepted:
            self.get_logger().warn('🏠 홈 목표 거부됨')
            return
        handle.get_result_async().add_done_callback(
            lambda f: self.get_logger().info('🏠 Nav2 홈 도착 완료')
        )

    def _cancel_nav2_goals(self):
        if self._cancel_client.service_is_ready():
            req = CancelGoal.Request()
            self._cancel_client.call_async(req)
            self.get_logger().info('Nav2 목표 전체 취소')
        self.cmd_pub.publish(Twist())

    def _stop_relay(self):
        subprocess.run(['pkill', '-f', 'topic_tools relay'], capture_output=True)
        self.get_logger().info('cmd_vel relay 중지')

    def _start_relay(self):
        subprocess.run(['pkill', '-f', 'topic_tools relay'], capture_output=True)
        subprocess.Popen(
            ['ros2', 'run', 'topic_tools', 'relay', '/cmd_vel_smoothed', '/cmd_vel'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.get_logger().info('cmd_vel relay 재시작 — NAV 모드')

    def image_cb(self, frame):
        frame = frame.copy()

        # ── 도킹 중이면 ArUco 스텝만 ──
        if self.dock_state is not None:
            annotated = self._aruco_dock_step(frame)
            self._publish_debug(annotated, 'DOCKING')
            return

        results = self.model(frame, verbose=False, conf=0.45)[0]

        if self.mode == 'follow':
            self._follow(frame, results)
        elif self.mode in ('home', 'park'):
            # 이동 중 ArUco 마커 감지 → 트리거
            self._check_aruco_trigger(frame)
            self._nav_obstacles(frame, results)
        else:
            self._nav_obstacles(frame, results)

        annotated = results.plot()
        self._publish_debug(annotated, self.mode.upper())

    def _publish_debug(self, annotated, label):
        mode_color = (0, 255, 0) if label == 'NAV' else (0, 100, 255)
        cv2.putText(annotated, f'MODE: {label}', (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, mode_color, 2)
        h, w, c = annotated.shape
        debug_msg = Image()
        debug_msg.header.stamp = self.get_clock().now().to_msg()
        debug_msg.height = h
        debug_msg.width = w
        debug_msg.encoding = 'bgr8'
        debug_msg.step = w * c
        debug_msg.data = annotated.tobytes()
        self.debug_pub.publish(debug_msg)

    # ── ArUco 트리거 감지 (HOME 이동 중) ─────────────────────

    def _aruco_distance(self, corner):
        pts = corner[0]
        w = np.linalg.norm(pts[0] - pts[1])
        h = np.linalg.norm(pts[1] - pts[2])
        avg_px = (w + h) / 2.0
        if avg_px < 1:
            return None
        return (MARKER_REAL_SIZE * CAM_FX) / avg_px

    def _check_aruco_trigger(self, frame):
        target_id = DOCK_MARKER_ID if self.mode == 'home' else PARK_MARKER_ID
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = ARUCO_DET.detectMarkers(gray)
        if ids is None:
            return
        flat = ids.flatten()
        if target_id not in flat:
            return
        idx = list(flat).index(target_id)
        dist = self._aruco_distance(corners[idx])
        if dist is not None and dist <= ARUCO_TRIGGER_DIST:
            self.get_logger().info(f'🎯 ArUco ID{target_id} 감지! {dist:.2f}m — 도킹 시작')
            self._cancel_nav2_goals()
            self._stop_relay()
            self.dock_target = self.mode  # 'home' or 'park'
            self.dock_state = 'aligning'

    # ── ArUco 도킹 스텝 ──────────────────────────────────────

    def _aruco_dock_step(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = ARUCO_DET.detectMarkers(gray)
        annotated = cv2.aruco.drawDetectedMarkers(frame.copy(), corners, ids)
        twist = Twist()

        active_marker_id = DOCK_MARKER_ID if self.dock_target == 'home' else PARK_MARKER_ID

        if self.dock_state == 'aligning':
            if ids is not None and active_marker_id in ids.flatten():
                idx = list(ids.flatten()).index(active_marker_id)
                cx = corners[idx][0][:, 0].mean()
                error = (cx - CAM_WIDTH / 2.0) / (CAM_WIDTH / 2.0)
                if abs(error) < 0.06:
                    self.dock_state = 'approaching'
                    self.dock_approach_start = self.get_clock().now()
                    self.get_logger().info('➡️ 마커 정렬 완료 — 전진 도킹 시작')
                else:
                    twist.angular.z = float(np.clip(-error * 0.5, -0.4, 0.4))
            else:
                twist.angular.z = 0.2
            self.cmd_pub.publish(twist)
            cv2.putText(annotated, 'ALIGNING', (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

        elif self.dock_state == 'approaching':
            elapsed = (self.get_clock().now() - self.dock_approach_start).nanoseconds / 1e9
            dist = None
            steer = 0.0
            if ids is not None and active_marker_id in ids.flatten():
                idx = list(ids.flatten()).index(active_marker_id)
                dist = self._aruco_distance(corners[idx])
                cx = corners[idx][0][:, 0].mean()
                error = (cx - CAM_WIDTH / 2.0) / (CAM_WIDTH / 2.0)
                steer = float(np.clip(-error * 0.3, -0.3, 0.3))
            front = self._lidar_front_distance()

            if elapsed > MAX_APPROACH_DURATION:
                self.cmd_pub.publish(Twist())
                self.dock_state = None
                self.dock_target = None
                self.mode = 'nav'
                self._start_relay()
                self.get_logger().warn('⛔ 전진 도킹 타임아웃 — 중단')
            elif (dist is not None and dist < DOCK_APPROACH_STOP) or \
                 (front is not None and front < DOCK_APPROACH_STOP):
                self.cmd_pub.publish(Twist())
                label = '🅿️ 주차 완료!' if self.dock_target == 'park' else '🏠 ArUco 도킹 완료!'
                self.dock_state = None
                self.dock_target = None
                self.mode = 'nav'
                self.docked = True
                self.get_logger().info(label + ' — nav 명령 시 2m 후진 탈출')
            else:
                twist.linear.x = 0.10
                twist.angular.z = steer
                self.cmd_pub.publish(twist)
            dist_str = f'{dist:.2f}m' if dist else '?'
            front_str = f'{front:.2f}m' if front else '?'
            cv2.putText(annotated, f'APPROACH dist:{dist_str} front:{front_str}', (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)

        return annotated

    # ── 후진 안전 검사 ────────────────────────────────────────

    def _check_reverse_safety(self):
        rear  = self._lidar_rear_distance()
        front = self._lidar_front_distance()
        if rear is None or rear < MIN_REVERSE_CLEARANCE:
            self.get_logger().warn(f'후방 공간 부족: {rear}m (최소 {MIN_REVERSE_CLEARANCE}m)')
            return False
        if front is None or front < MIN_EXIT_CLEARANCE:
            self.get_logger().warn(f'전방 탈출로 없음: {front}m (최소 {MIN_EXIT_CLEARANCE}m)')
            return False
        return True

    # ── 전방 라이다 거리 ─────────────────────────────────────

    def _lidar_front_distance(self):
        with self.lock:
            scan = self.scan
        if scan is None:
            return None
        a_min = math.degrees(scan.angle_min)
        a_inc = math.degrees(scan.angle_increment)
        distances = []
        for i, r in enumerate(scan.ranges):
            if not (scan.range_min < r < scan.range_max):
                continue
            lidar_deg = (a_min + i * a_inc) % 360
            if not (lidar_deg <= 30 or lidar_deg >= 330):
                continue
            skip = False
            for pa in PILLAR_ANGLES_DEG:
                if abs((lidar_deg - pa + 180) % 360 - 180) < PILLAR_HALF_WIDTH:
                    skip = True
                    break
            if not skip:
                distances.append(r)
        return min(distances) if distances else None

    # ── 후방 라이다 거리 ─────────────────────────────────────

    def _lidar_rear_distance(self):
        with self.lock:
            scan = self.scan
        if scan is None:
            return None
        a_min = math.degrees(scan.angle_min)
        a_inc = math.degrees(scan.angle_increment)
        distances = []
        for i, r in enumerate(scan.ranges):
            if not (scan.range_min < r < scan.range_max):
                continue
            lidar_deg = (a_min + i * a_inc) % 360
            if not (150 <= lidar_deg <= 210):
                continue
            skip = False
            for pa in PILLAR_ANGLES_DEG:
                if abs((lidar_deg - pa + 180) % 360 - 180) < PILLAR_HALF_WIDTH:
                    skip = True
                    break
            if not skip:
                distances.append(r)
        return min(distances) if distances else None

    # ── NAV 모드: 장애물 → PointCloud2 → Nav2 ────────────────

    def _nav_obstacles(self, frame, results):
        points = []
        for box in results.boxes:
            cls_name = self.model.names[int(box.cls[0])]
            if cls_name not in OBSTACLE_CLASSES:
                continue
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            cx = (x1 + x2) / 2.0
            width = x2 - x1
            dist = self._lidar_distance(cx, width)
            if dist is None or dist > 3.5:
                continue
            angle_rad = math.radians(
                (cx - CAM_WIDTH / 2.0) / (CAM_WIDTH / 2.0) * (CAM_HFOV / 2.0)
            )
            ox = dist * math.cos(angle_rad)
            oy = -dist * math.sin(angle_rad)
            radius = OBSTACLE_CLASSES[cls_name]
            for deg in range(0, 360, 20):
                px = ox + radius * math.cos(math.radians(deg))
                py = oy + radius * math.sin(math.radians(deg))
                points.append((px, py, 0.3))
        if points:
            self.obstacle_pub.publish(self._make_cloud(points))

    # ── FOLLOW 모드: 사람 추종 ────────────────────────────────

    def _follow(self, frame, results):
        twist = Twist()
        best_box, best_area = None, 0
        for box in results.boxes:
            if self.model.names[int(box.cls[0])] == 'person':
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                area = (x2 - x1) * (y2 - y1)
                if area > best_area:
                    best_area = area
                    best_box = (x1, y1, x2, y2)
        if best_box:
            x1, y1, x2, y2 = best_box
            cx = (x1 + x2) / 2.0
            dist = self._lidar_distance(cx, x2 - x1)
            error = (cx - CAM_WIDTH / 2.0) / (CAM_WIDTH / 2.0)
            if abs(error) < 0.08:
                twist.angular.z = 0.0
            else:
                twist.angular.z = float(np.clip(-error * 0.6, -0.5, 0.5))
            target = 1.2
            if dist is not None:
                gap = dist - target
                twist.linear.x = float(np.clip(gap * 0.6, 0.0, 0.40))
            elif best_area > 20000:
                twist.linear.x = 0.0
            else:
                twist.linear.x = 0.15
        self.cmd_pub.publish(twist)

    # ── 라이다 거리 추정 ─────────────────────────────────────

    def _lidar_distance(self, bbox_cx_px, bbox_w_px):
        with self.lock:
            scan = self.scan
        if scan is None:
            return None
        angle_center = (bbox_cx_px - CAM_WIDTH / 2.0) / (CAM_WIDTH / 2.0) * (CAM_HFOV / 2.0)
        half_w = (bbox_w_px / CAM_WIDTH) * CAM_HFOV / 2.0 + 5.0
        a_min = math.degrees(scan.angle_min)
        a_inc = math.degrees(scan.angle_increment)
        distances = []
        for i, r in enumerate(scan.ranges):
            if not (scan.range_min < r < scan.range_max):
                continue
            lidar_deg = (a_min + i * a_inc) % 360
            if abs(lidar_deg - angle_center) > half_w:
                continue
            skip = False
            for pa in PILLAR_ANGLES_DEG:
                if abs((lidar_deg - pa + 180) % 360 - 180) < PILLAR_HALF_WIDTH:
                    skip = True
                    break
            if not skip:
                distances.append(r)
        return min(distances) if distances else None

    # ── PointCloud2 생성 ─────────────────────────────────────

    def _make_cloud(self, points):
        cloud = PointCloud2()
        cloud.header.stamp = self.get_clock().now().to_msg()
        cloud.header.frame_id = 'base_footprint'
        cloud.height = 1
        cloud.width = len(points)
        cloud.fields = [
            PointField(name='x', offset=0,  datatype=PointField.FLOAT32, count=1),
            PointField(name='y', offset=4,  datatype=PointField.FLOAT32, count=1),
            PointField(name='z', offset=8,  datatype=PointField.FLOAT32, count=1),
        ]
        cloud.is_bigendian = False
        cloud.point_step = 12
        cloud.row_step = 12 * len(points)
        cloud.is_dense = True
        data = bytearray()
        for p in points:
            data += struct.pack('fff', float(p[0]), float(p[1]), float(p[2]))
        cloud.data = bytes(data)
        return cloud


def main():
    rclpy.init()
    node = YoloNavNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
