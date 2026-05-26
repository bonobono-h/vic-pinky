#!/usr/bin/env python3
import cv2
import socket
import time

TARGET_IP = "192.168.5.3"
TARGET_PORT = 5006
FPS = 20

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, FPS)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print('카메라 열기 실패')
    exit(1)

print(f'📡 {TARGET_IP}:{TARGET_PORT} 카메라 UDP 송출 시작 ({FPS}fps)...')

interval = 1.0 / FPS
next_time = time.time()

while True:
    ret, frame = cap.read()
    if not ret:
        continue
    now = time.time()
    if now < next_time:
        continue
    next_time = now + interval

    frame = cv2.flip(frame, -1)
    _, enc = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    data = enc.tobytes()
    if len(data) < 65500:
        sock.sendto(data, (TARGET_IP, TARGET_PORT))
