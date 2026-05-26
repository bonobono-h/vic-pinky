#!/usr/bin/env python3
"""
VicPinky 웹 컨트롤 서버
브라우저에서 자율주행 ↔ 사람추종 모드 전환
실행: python3 web_control.py
접속: http://localhost:8080
"""
import threading
import socket
import numpy as np
import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

# ── 공유 상태 ────────────────────────────────────────────────

latest_frame: bytes = None
frame_lock = threading.Lock()

CAM_UDP_PORT = 5006  # VicPinky → 노트북 카메라 UDP 포트


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

    def set_mode(self, mode: str):
        self.current_mode = mode
        msg = String()
        msg.data = mode
        self.pub.publish(msg)
        self.get_logger().info(f'모드 전환 → {mode}')


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
  </style>
</head>
<body>
  <h1>🤖 VicPinky</h1>

  <div class="cam-box">
    <span class="cam-label">LIVE CAM</span>
    <img src="/video" alt="카메라 없음 / 미연결">
  </div>

  <div class="status-box">
    현재 모드:&nbsp;
    <span id="badge" class="mode-badge nav-badge">NAV</span>
  </div>

  <div class="btn-row">
    <button class="btn-nav" onclick="setMode('nav')">
      🗺️ 자율주행
    </button>
    <button class="btn-follow" onclick="setMode('follow')">
      👤 사람 추종
    </button>
  </div>

  <button class="btn-stop" onclick="setMode('stop')" style="width:100%;max-width:320px;">
    🛑 정지
  </button>

  <small>VicPinky Nav2 + YOLO v8 통합 시스템</small>

  <script>
    async function setMode(mode) {
      const res = await fetch('/mode/' + mode, { method: 'POST' });
      const data = await res.json();
      const badge = document.getElementById('badge');
      if (mode === 'nav') {
        badge.textContent = 'NAV';
        badge.className = 'mode-badge nav-badge';
      } else if (mode === 'follow') {
        badge.textContent = 'FOLLOW';
        badge.className = 'mode-badge follow-badge';
      } else {
        badge.textContent = 'STOP';
        badge.className = 'mode-badge';
        badge.style.background = '#4a0000';
        badge.style.color = '#ff6b6b';
      }
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
    import time
    time.sleep(0.1)  # ROS2 노드 초기화 대기
    if ros_node:
        ros_node.set_mode(mode)
    return {"mode": mode, "status": "ok"}

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


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8080)
