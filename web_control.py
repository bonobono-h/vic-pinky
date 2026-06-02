#!/usr/bin/env python3
"""
VicPinky 웹 컨트롤 서버
브라우저에서 자율주행 ↔ 사람추종 모드 전환
실행: python3 web_control.py
접속: http://localhost:8080
"""
import math
import time
import threading
import socket
import numpy as np
import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

# ── 공구실 위치 (홈과 동일) ──────────────────────────────────
HOME_X =  0.47947038485505556
HOME_Y = -1.5220762188852752
ROBOT_SPEED = 0.25  # m/s 평균 이동 속도

TOOLS = {
    'driver':  {'name': '드라이버', 'icon': '🔧'},
    'spanner': {'name': '스패너',   'icon': '🔩'},
    'hammer':  {'name': '망치',     'icon': '🔨'},
    'axe':     {'name': '도끼',     'icon': '🪓'},
}

# ── 공유 상태 ────────────────────────────────────────────────

latest_frame: bytes = None
frame_lock = threading.Lock()

lidar_front: float = None
lidar_rear:  float = None
lidar_lock = threading.Lock()

robot_x: float = 0.0
robot_y: float = 0.0
odom_lock = threading.Lock()

selected_tool: str = None   # 현재 선택된 도구 key
nav_start_time: float = None
nav_lock = threading.Lock()

delivery_done_station: int = None   # 배달 완료된 작업장 번호
delivery_lock = threading.Lock()

WORKSTATIONS = {
    1: (-8.3141, -11.1439),
    2: (-7.0176,  -5.0872),
}

CAM_UDP_PORT = 5006

PILLAR_ANGLES_DEG = [162.0, 200.0, 245.3, 338.7, 23.3]
PILLAR_HALF_WIDTH = 10


# ── UDP 카메라 수신 스레드 ────────────────────────────────────

def _udp_cam_thread():
    global latest_frame
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', CAM_UDP_PORT))
    sock.settimeout(1.0)
    while True:
        try:
            data, _ = sock.recvfrom(65536)
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            _, enc = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            with frame_lock:
                latest_frame = bytes(enc)
        except socket.timeout:
            pass

threading.Thread(target=_udp_cam_thread, daemon=True).start()


# ── ROS2 노드 ────────────────────────────────────────────────

class ModePublisher(Node):
    def __init__(self):
        super().__init__('web_mode_publisher')
        self.pub = self.create_publisher(String, '/robot_mode', 10)
        self.current_mode = 'nav'
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            depth=1,
        )
        self.create_subscription(LaserScan, '/scan', self._lidar_cb, sensor_qos)
        self.create_subscription(Odometry, '/odom', self._odom_cb, sensor_qos)
        self.create_subscription(String, '/delivery_done', self._delivery_done_cb, 10)

    def _delivery_done_cb(self, msg):
        global delivery_done_station
        with delivery_lock:
            delivery_done_station = int(msg.data)

    def set_mode(self, mode: str):
        self.current_mode = mode
        msg = String()
        msg.data = mode
        self.pub.publish(msg)
        self.get_logger().info(f'모드 전환 → {mode}')

    def _odom_cb(self, msg):
        global robot_x, robot_y
        with odom_lock:
            robot_x = msg.pose.pose.position.x
            robot_y = msg.pose.pose.position.y

    def _lidar_cb(self, msg):
        global lidar_front, lidar_rear
        front = self._calc_distance(msg, front=True)
        rear  = self._calc_distance(msg, front=False)
        with lidar_lock:
            lidar_front = front
            lidar_rear  = rear

    def _calc_distance(self, scan, front: bool):
        a_min = math.degrees(scan.angle_min)
        a_inc = math.degrees(scan.angle_increment)
        distances = []
        for i, r in enumerate(scan.ranges):
            if not (scan.range_min < r < scan.range_max):
                continue
            deg = (a_min + i * a_inc) % 360
            if front:
                in_zone = 150 <= deg <= 210
            else:
                in_zone = deg <= 30 or deg >= 330
            if not in_zone:
                continue
            if any(abs((deg - pa + 180) % 360 - 180) < PILLAR_HALF_WIDTH
                   for pa in PILLAR_ANGLES_DEG):
                continue
            distances.append(r)
        if not distances:
            return None
        distances.sort()
        return round(distances[len(distances) // 2], 2)


ros_node: ModePublisher = None

def ros_spin():
    global ros_node
    rclpy.init()
    ros_node = ModePublisher()
    rclpy.spin(ros_node)

threading.Thread(target=ros_spin, daemon=True).start()

# ── FastAPI ──────────────────────────────────────────────────

app = FastAPI()

HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VicPinky Control</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background: #1a1a2e;
      color: #eee;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      gap: 20px;
    }
    h1 { font-size: 1.8rem; color: #e94560; letter-spacing: 2px; }
    .status-box {
      background: #16213e;
      border: 1px solid #0f3460;
      border-radius: 12px;
      padding: 16px 32px;
      font-size: 1.1rem;
      text-align: center;
    }
    .mode-badge {
      display: inline-block;
      padding: 4px 14px;
      border-radius: 20px;
      font-weight: bold;
      font-size: 1rem;
    }
    .nav-badge    { background: #0f3460; color: #4fc3f7; }
    .follow-badge { background: #3d0c45; color: #f48fb1; }
    .btn-row {
      display: flex;
      gap: 16px;
    }
    button {
      padding: 18px 36px;
      font-size: 1.1rem;
      border: none;
      border-radius: 12px;
      cursor: pointer;
      font-weight: bold;
      transition: transform .1s, opacity .2s;
    }
    button:active { transform: scale(0.96); }
    .btn-nav {
      background: #0f3460;
      color: #4fc3f7;
      border: 2px solid #4fc3f7;
    }
    .btn-follow {
      background: #3d0c45;
      color: #f48fb1;
      border: 2px solid #f48fb1;
    }
    .btn-stop {
      background: #4a0000;
      color: #ff6b6b;
      border: 2px solid #ff6b6b;
    }
    .btn-home {
      background: #1a3a1a;
      color: #69f0ae;
      border: 2px solid #69f0ae;
    }
    .btn-tool {
      background: #16213e;
      color: #eee;
      border: 1px solid #0f3460;
      border-radius: 12px;
      padding: 18px;
      font-size: 1.5rem;
      cursor: pointer;
      transition: background .15s;
    }
    .btn-tool:hover { background: #0f3460; }
    .btn-tool span { display:block; font-size:0.9rem; margin-top:6px; }
    .active { opacity: 1; }
    .inactive { opacity: 0.45; }
    small { color: #888; font-size: 0.85rem; }
    .cam-box {
      width: 100%;
      max-width: 480px;
      background: #0d0d1a;
      border: 1px solid #0f3460;
      border-radius: 12px;
      overflow: hidden;
      position: relative;
    }
    .cam-box img {
      width: 100%;
      display: block;
    }
    .cam-label {
      position: absolute;
      top: 8px; left: 10px;
      font-size: 0.75rem;
      color: #4fc3f7;
      background: rgba(0,0,0,0.5);
      padding: 2px 8px;
      border-radius: 8px;
    }
    .dist-panel {
      display: flex;
      gap: 16px;
      width: 100%;
      max-width: 480px;
    }
    .dist-card {
      flex: 1;
      background: #16213e;
      border: 1px solid #0f3460;
      border-radius: 12px;
      padding: 12px 16px;
      text-align: center;
    }
    .dist-label {
      font-size: 0.75rem;
      color: #888;
      letter-spacing: 1px;
      margin-bottom: 4px;
    }
    .dist-value {
      font-size: 1.8rem;
      font-weight: bold;
      font-variant-numeric: tabular-nums;
      transition: color 0.3s;
    }
    .dist-unit { font-size: 0.9rem; color: #888; }
  </style>
</head>
<body>
  <h1>🤖 VicPinky</h1>

  <div class="cam-box">
    <span class="cam-label">LIVE CAM</span>
    <img src="/video" alt="카메라 없음 / 미연결">
  </div>

  <div class="dist-panel">
    <div class="dist-card">
      <div class="dist-label">▲ 전방</div>
      <div class="dist-value" id="dist-front">—</div>
      <div class="dist-unit">m</div>
    </div>
    <div class="dist-card">
      <div class="dist-label">▼ 후방</div>
      <div class="dist-value" id="dist-rear">—</div>
      <div class="dist-unit">m</div>
    </div>
  </div>

  <div class="status-box">
    현재 모드:&nbsp;
    <span id="badge" class="mode-badge nav-badge">NAV</span>
  </div>

  <div class="btn-row">
    <button class="btn-nav" onclick="setMode('nav')">
      🗺️ 자율주행모드
    </button>
    <button class="btn-follow" onclick="setMode('follow')">
      👤 작업자 추종 모드
    </button>
  </div>

  <button class="btn-stop" onclick="setMode('stop')" style="width:100%;max-width:320px;">
    🛑 정지
  </button>

  <button class="btn-home" onclick="toggleToolPanel()" style="width:100%;max-width:480px;">
    🏠 공구실
  </button>

  <!-- 도구 선택 패널 -->
  <div id="tool-panel" style="display:none;width:100%;max-width:480px;">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <button class="btn-tool" onclick="selectTool('driver')">🔧<br><span>드라이버</span></button>
      <button class="btn-tool" onclick="selectTool('spanner')">🔩<br><span>스패너</span></button>
      <button class="btn-tool" onclick="selectTool('hammer')">🔨<br><span>망치</span></button>
      <button class="btn-tool" onclick="selectTool('axe')">🪓<br><span>도끼</span></button>
    </div>
  </div>

  <!-- 이동 상태 카드 -->
  <div id="nav-card" style="display:none;width:100%;max-width:480px;background:#16213e;border:1px solid #0f3460;border-radius:12px;padding:16px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
      <span id="nav-icon" style="font-size:1.8rem;"></span>
      <div>
        <div style="font-size:0.8rem;color:#888;">이동 중</div>
        <div id="nav-name" style="font-size:1.1rem;font-weight:bold;color:#4fc3f7;"></div>
      </div>
      <div id="nav-arrived" style="margin-left:auto;display:none;background:#1a3a1a;color:#69f0ae;padding:4px 12px;border-radius:20px;font-size:0.85rem;font-weight:bold;">✅ 도착!</div>
    </div>
    <div style="display:flex;gap:12px;">
      <div style="flex:1;text-align:center;background:#0d0d1a;border-radius:8px;padding:10px;">
        <div style="font-size:0.75rem;color:#888;margin-bottom:4px;">이동거리는</div>
        <div id="nav-dist" style="font-size:1.6rem;font-weight:bold;color:#ffa502;font-variant-numeric:tabular-nums;">—</div>
        <div style="font-size:0.8rem;color:#888;">m</div>
      </div>
      <div style="flex:1;text-align:center;background:#0d0d1a;border-radius:8px;padding:10px;">
        <div style="font-size:0.75rem;color:#888;margin-bottom:4px;">예상 도착</div>
        <div id="nav-eta" style="font-size:1.6rem;font-weight:bold;color:#2ed573;font-variant-numeric:tabular-nums;">—</div>
        <div style="font-size:0.8rem;color:#888;">초</div>
      </div>
    </div>
  </div>

  <!-- 음성 명령 버튼 -->
  <button id="mic-btn" onclick="startVoice()" style="width:100%;max-width:480px;background:#2d1b4e;color:#ce93d8;border:2px solid #ce93d8;border-radius:12px;padding:16px;font-size:1.1rem;font-weight:bold;cursor:pointer;">
    🎤 음성 명령
  </button>
  <div id="voice-status" style="font-size:0.85rem;color:#888;min-height:1.2em;text-align:center;"></div>

  <small>VicPinky Nav2 + YOLO v8 통합 시스템</small>

  <script>
    async function setMode(mode) {
      const res = await fetch('/mode/' + mode, { method: 'POST' });
      const data = await res.json();
      const badge = document.getElementById('badge');
      if (mode === 'nav') {
        badge.textContent = 'NAV';
        badge.className = 'mode-badge nav-badge';
        document.getElementById('nav-card').style.display = 'none';
      } else if (mode === 'follow') {
        badge.textContent = 'FOLLOW';
        badge.className = 'mode-badge follow-badge';
      } else if (mode === 'stop') {
        badge.textContent = 'STOP';
        badge.className = 'mode-badge';
        badge.style.background = '#4a0000';
        badge.style.color = '#ff6b6b';
      } else {
        badge.textContent = mode.toUpperCase();
        badge.className = 'mode-badge';
        badge.style.background = '#1a3a1a';
        badge.style.color = '#69f0ae';
      }
    }

    function toggleToolPanel() {
      const p = document.getElementById('tool-panel');
      p.style.display = p.style.display === 'none' ? 'block' : 'none';
    }

    async function selectTool(key) {
      document.getElementById('tool-panel').style.display = 'none';
      document.getElementById('nav-card').style.display = 'block';
      document.getElementById('nav-arrived').style.display = 'none';
      await fetch('/tool/' + key, { method: 'POST' });
      const badge = document.getElementById('badge');
      badge.textContent = 'HOME';
      badge.className = 'mode-badge';
      badge.style.background = '#1a3a1a';
      badge.style.color = '#69f0ae';
      updateNavStatus();
    }

    function distColor(val) {
      if (val === null) return '#555';
      if (val < 0.4)  return '#ff4757';
      if (val < 1.0)  return '#ffa502';
      return '#2ed573';
    }

    async function updateDist() {
      try {
        const res = await fetch('/distances');
        const d = await res.json();
        const fe = document.getElementById('dist-front');
        const re = document.getElementById('dist-rear');
        fe.textContent = d.front !== null ? d.front.toFixed(2) : '—';
        re.textContent = d.rear  !== null ? d.rear.toFixed(2)  : '—';
        fe.style.color = distColor(d.front);
        re.style.color = distColor(d.rear);
      } catch(e) {}
    }

    async function updateNavStatus() {
      try {
        const res = await fetch('/nav_status');
        const d = await res.json();
        if (!d.tool) return;
        document.getElementById('nav-icon').textContent = d.tool_icon;
        document.getElementById('nav-name').textContent = d.tool_name + ' 픽업';
        document.getElementById('nav-dist').textContent = d.distance.toFixed(1);
        document.getElementById('nav-eta').textContent = d.eta > 0 ? d.eta : '0';
        if (d.arrived) {
          document.getElementById('nav-arrived').style.display = 'block';
          document.getElementById('nav-dist').style.color = '#2ed573';
        } else {
          document.getElementById('nav-dist').style.color = '#ffa502';
        }
      } catch(e) {}
    }

    let deliveryDoneSpoken = false;
    async function pollDelivery() {
      try {
        const res = await fetch('/delivery_status');
        const d = await res.json();
        if (d.done && !deliveryDoneSpoken) {
          deliveryDoneSpoken = true;
          const msg = voiceToolName
            ? `${d.station}번 작업장에 ${voiceToolName}를 가져다드렸습니다.`
            : `${d.station}번 작업장에 도착했습니다.`;
          speak(msg);
          document.getElementById('nav-arrived').style.display = 'block';
        }
        if (!d.done) deliveryDoneSpoken = false;
      } catch(e) {}
    }

    setInterval(updateDist, 300);
    setInterval(updateNavStatus, 500);
    setInterval(pollDelivery, 800);
    updateDist();

    // ── 음성 명령 ──────────────────────────────────────────────
    const TOOL_KEYWORDS = {
      '드라이버': 'driver',
      '스패너':   'spanner',
      '망치':     'hammer',
      '도끼':     'axe',
    };
    const STATION_KEYWORDS = {
      '1번': 1, '일번': 1, '일 번': 1, '1 번': 1, '첫번': 1, '첫 번': 1,
      '2번': 2, '이번': 2, '이 번': 2, '2 번': 2,
    };

    let voiceToolName = null;
    let arrivedSpoken = false;

    function speak(text, onEnd) {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'ko-KR';
      u.rate = 1.05;
      if (onEnd) u.onend = onEnd;
      window.speechSynthesis.speak(u);
    }

    function startVoice() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) {
        alert('이 브라우저는 음성 인식을 지원하지 않아요. Chrome을 사용해주세요.');
        return;
      }
      const rec = new SR();
      rec.lang = 'ko-KR';
      rec.interimResults = false;
      rec.maxAlternatives = 3;

      const btn = document.getElementById('mic-btn');
      const status = document.getElementById('voice-status');
      btn.style.background = '#4a0060';
      btn.textContent = '🔴 듣는 중...';
      status.textContent = '말씀하세요...';

      rec.onresult = (e) => {
        const candidates = Array.from(e.results[0]).map(r => r.transcript);
        status.textContent = '인식: ' + candidates[0];
        let matched = null;
        outer: for (const text of candidates) {
          for (const [kw, key] of Object.entries(TOOL_KEYWORDS)) {
            if (text.includes(kw)) { matched = { kw, key }; break outer; }
          }
        }
        // 작업장 이동/배달 명령 파싱 (전체 후보에서 검색)
        let deliverStation = null;
        outerS: for (const text of candidates) {
          for (const [kw, num] of Object.entries(STATION_KEYWORDS)) {
            if (text.includes(kw)) { deliverStation = num; break outerS; }
          }
        }
        const fullText = candidates.join(' ');
        const isGoto = fullText.includes('이동') || fullText.includes('가줘') ||
                       fullText.includes('가자') || fullText.includes('가주') ||
                       fullText.includes('가세요') || fullText.includes('이동해');

        // "1번 작업장으로" — 도구 없이 이동
        if (deliverStation && !matched) {
          (async () => {
            await fetch(`/goto/${deliverStation}`, { method: 'POST' });
            speak(`알겠습니다! ${deliverStation}번 작업장으로 이동합니다.`);
            document.getElementById('nav-card').style.display = 'block';
            document.getElementById('nav-arrived').style.display = 'none';
            const badge = document.getElementById('badge');
            badge.textContent = `${deliverStation}번 작업장`;
            badge.className = 'mode-badge';
            badge.style.background = '#1a2a4a';
            badge.style.color = '#80cbc4';
          })();
        } else if (matched && deliverStation) {
          voiceToolName = matched.kw;
          arrivedSpoken = false;
          (async () => {
            await fetch(`/deliver/${deliverStation}`, { method: 'POST' });
            let distText = '';
            try {
              const ns = await fetch('/nav_status');
              const nd = await ns.json();
              if (nd.distance > 0.3) {
                distText = ` 이동거리는 약 ${(nd.distance).toFixed(0)}미터 정도이고, 소요시간은 약 ${nd.eta}초 정도입니다.`;
              }
            } catch(e) {}
            speak(`알겠습니다! ${matched.kw} 가지러 공구실에 들렀다가 ${deliverStation}번 작업장으로 가겠습니다.${distText}`);
            document.getElementById('tool-panel').style.display = 'none';
            document.getElementById('nav-card').style.display = 'block';
            document.getElementById('nav-arrived').style.display = 'none';
            const badge = document.getElementById('badge');
            badge.textContent = 'DELIVER';
            badge.className = 'mode-badge';
            badge.style.background = '#1a2a4a';
            badge.style.color = '#80cbc4';
          })();
        } else if (matched) {
          voiceToolName = matched.kw;
          arrivedSpoken = false;
          (async () => {
            await fetch('/tool/' + matched.key, { method: 'POST' });
            let distText = '';
            try {
              const ns = await fetch('/nav_status');
              const nd = await ns.json();
              if (nd.distance > 0.3) {
                distText = ` 이동거리는 약 ${nd.distance.toFixed(0)}미터 정도이고, 소요시간은 약 ${nd.eta}초 정도입니다.`;
              }
            } catch(e) {}
            speak(`알겠습니다! ${matched.kw} 가지러 출발합니다.${distText}`);
            document.getElementById('tool-panel').style.display = 'none';
            document.getElementById('nav-card').style.display = 'block';
            document.getElementById('nav-arrived').style.display = 'none';
            const badge = document.getElementById('badge');
            badge.textContent = 'HOME';
            badge.className = 'mode-badge';
            badge.style.background = '#1a3a1a';
            badge.style.color = '#69f0ae';
          })();
        } else if (candidates[0].trim().length > 0) {
          speak('등록이 되지 않은 공구입니다.');
          status.textContent = `❌ 미등록 공구: "${candidates[0]}"`;
        } else {
          speak('죄송해요, 다시 말씀해주세요.');
          status.textContent = '❓ 인식 실패 — 다시 시도해보세요';
        }
      };

      rec.onerror = (e) => { status.textContent = '오류: ' + e.error; };
      rec.onend = () => {
        btn.style.background = '#2d1b4e';
        btn.textContent = '🎤 음성 명령';
      };
      rec.start();
    }

    // selectTool에 도구명 파라미터 추가 버전
    async function selectTool(key, toolName) {
      document.getElementById('tool-panel').style.display = 'none';
      document.getElementById('nav-card').style.display = 'block';
      document.getElementById('nav-arrived').style.display = 'none';
      arrivedSpoken = false;
      await fetch('/tool/' + key, { method: 'POST' });
      const badge = document.getElementById('badge');
      badge.textContent = 'HOME';
      badge.className = 'mode-badge';
      badge.style.background = '#1a3a1a';
      badge.style.color = '#69f0ae';
    }

    // nav 상태 폴링 — 도착 시 TTS
    const _origUpdateNavStatus = updateNavStatus;
    async function updateNavStatus() {
      try {
        const res = await fetch('/nav_status');
        const d = await res.json();
        if (!d.tool) return;
        document.getElementById('nav-icon').textContent = d.tool_icon;
        document.getElementById('nav-name').textContent = d.tool_name + ' 픽업';
        document.getElementById('nav-dist').textContent = d.distance.toFixed(1);
        document.getElementById('nav-eta').textContent = d.eta > 0 ? d.eta : '0';
        if (d.arrived) {
          document.getElementById('nav-arrived').style.display = 'block';
          document.getElementById('nav-dist').style.color = '#2ed573';
          if (!arrivedSpoken && voiceToolName) {
            arrivedSpoken = true;
            speak(`다녀왔어요! ${voiceToolName}를 가져왔습니다`);
          }
        } else {
          document.getElementById('nav-dist').style.color = '#ffa502';
        }
      } catch(e) {}
    }
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML

@app.post("/mode/{mode}")
def set_mode(mode: str):
    time.sleep(0.1)
    if ros_node:
        ros_node.set_mode(mode)
    return {"mode": mode, "status": "ok"}

@app.post("/tool/{tool_key}")
def select_tool(tool_key: str):
    global selected_tool, nav_start_time
    if tool_key not in TOOLS:
        return {"status": "error", "msg": "unknown tool"}
    with nav_lock:
        selected_tool = tool_key
        nav_start_time = time.time()
    time.sleep(0.1)
    if ros_node:
        ros_node.set_mode('home')
    return {"status": "ok", "tool": tool_key}

@app.post("/goto/{station}")
def goto_station(station: int):
    time.sleep(0.1)
    if ros_node:
        ros_node.set_mode(f'goto_{station}')
    return {"status": "ok", "station": station}

@app.post("/deliver/{station}")
def deliver(station: int):
    global selected_tool, nav_start_time, delivery_done_station
    with nav_lock:
        nav_start_time = time.time()
    with delivery_lock:
        delivery_done_station = None
    time.sleep(0.1)
    if ros_node:
        ros_node.set_mode(f'deliver_{station}')
    return {"status": "ok", "station": station}

@app.get("/delivery_status")
def delivery_status():
    with delivery_lock:
        done = delivery_done_station
    return {"done": done is not None, "station": done}

@app.get("/nav_status")
def nav_status():
    with odom_lock:
        rx, ry = robot_x, robot_y
    with nav_lock:
        tool = selected_tool
        start = nav_start_time
    one_way = math.sqrt((HOME_X - rx) ** 2 + (HOME_Y - ry) ** 2)
    dist = round(one_way * 2, 2)  # 왕복 거리
    eta = round(dist / ROBOT_SPEED) if one_way > 0.1 else 0
    elapsed = round(time.time() - start) if start else 0
    return {
        "tool": tool,
        "tool_name": TOOLS[tool]['name'] if tool else None,
        "tool_icon": TOOLS[tool]['icon'] if tool else None,
        "distance": round(dist, 2),
        "eta": eta,
        "elapsed": elapsed,
        "arrived": dist < 0.3,
    }

def _no_camera_jpeg():
    import numpy as np, cv2
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    cv2.putText(img, 'NO CAMERA', (60, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (80, 80, 80), 2)
    cv2.putText(img, 'run_yolo.sh ?', (70, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 60), 1)
    _, buf = cv2.imencode('.jpg', img)
    return bytes(buf)

def _mjpeg_generator():
    import time
    no_cam = _no_camera_jpeg()
    while True:
        with frame_lock:
            frame = latest_frame
        data = frame if frame else no_cam
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + data + b'\r\n')
        time.sleep(0.05)

@app.get("/video")
def video_feed():
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/status")
def status():
    return {"mode": ros_node.current_mode if ros_node else "unknown"}

@app.get("/distances")
def distances():
    with lidar_lock:
        return {"front": lidar_front, "rear": lidar_rear}


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8080)
