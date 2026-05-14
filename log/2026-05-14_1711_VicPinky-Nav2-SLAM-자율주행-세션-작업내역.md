# VicPinky Nav2 + SLAM 자율주행 — 세션 작업 내역

> 타임스탬프: 2026-05-14 17:11  
> 브랜치: main  
> 작업자: bonobono-h

---

## 커밋 목록

| 해시 | 시각 | 메시지 |
|------|------|--------|
| `2ee0de4` | 2026-05-14 14:45 | feat: VicPinky 자율주행 프로젝트 초기 커밋 (Nav2 + SLAM + 객체추종) |
| `651ea86` | 2026-05-14 17:10 | feat: 커밋 자동 변경 이력 기록 시스템 추가 |
| `d921b27` | 2026-05-14 17:10 | chore: 변경 로그 업데이트 |

---

## 이번 세션에서 해결한 문제들

### 1. RViz2 맵이 안 보이는 문제 (GLSL 셰이더 버그)

**증상:** RViz2에서 `/map` 토픽을 추가해도 맵이 렌더링되지 않음, GL 에러 발생

**원인:** `/opt/ros/jazzy/share/rviz_rendering/ogre_media/materials/glsl120/indexed_8bit_image.frag`
- OpenGL 4.6 코어 프로파일에서 `sampler1D` 미지원
- Ogre 머티리얼 파일에서 `texture test_20x20.png 1d` → 런타임에 1D 텍스처 생성, `sampler2D`와 충돌

**수정 파일:**
- `/opt/ros/jazzy/.../indexed_8bit_image.frag` — `sampler1D` 제거, 픽셀값으로 인라인 색상 계산으로 대체
- `/opt/ros/jazzy/.../indexed_8bit_image.material` — `texture ... 1d` → `2d` 변경

**최종 셰이더 코드:**
```glsl
#version 120
varying vec2 UV;
uniform sampler2D eight_bit_image;
uniform sampler2D palette;
uniform float alpha;

void main()
{
  float val = texture2D(eight_bit_image, UV).x;
  vec3 color;
  if (val < 0.01) {
    color = vec3(0.0, 0.0, 0.0);       // 벽 (검정)
  } else if (val > 0.98) {
    color = vec3(1.0, 1.0, 1.0);       // 자유 공간 (흰색)
  } else if (val > 0.9) {
    color = vec3(0.96, 0.96, 0.96);    // 자유 공간 경계
  } else {
    color = vec3(0.5, 0.5, 0.5);       // 미탐색 영역 (회색)
  }
  gl_FragColor = vec4(color, alpha);
}
```

---

### 2. RViz2 재시작 후 맵이 다시 사라지는 문제 (QoS 불일치)

**원인:** `map_server`는 `TRANSIENT_LOCAL` QoS로 퍼블리시, RViz2는 `VOLATILE`로 구독
→ RViz2가 나중에 구독하면 이미 발행된 맵을 못 받음

**해결 (RViz2 켠 후 터미널에서):**
```bash
source /opt/ros/jazzy/setup.bash && export ROS_DOMAIN_ID=28
ros2 service call /map_server/load_map nav2_msgs/srv/LoadMap \
  "{map_url: '/home/hong/my_map.yaml'}"
```

---

### 3. cmd_vel이 안 나와서 로봇이 안 움직이는 문제

**원인:** Nav2의 cmd_vel 체인이 막혀 있었음
```
controller_server → /cmd_vel_nav
  → velocity_smoother → /cmd_vel_smoothed
    → collision_monitor → /cmd_vel  ← 여기까지 안 도달
```
`velocity_smoother`가 `cmd_vel_nav`를 받아도 `cmd_vel_smoothed`를 퍼블리시하지 않음

**해결:** `topic_tools relay`로 우회
```bash
ros2 run topic_tools relay /cmd_vel_smoothed /cmd_vel &
```
→ `nav2.sh`에 자동 실행되도록 추가

---

### 4. bt_navigator가 반복적으로 비활성화되는 문제

**원인:** bond timeout이 너무 짧아서 lifecycle manager가 bt_navigator를 주기적으로 deactivate

**해결 (`nav2_params.yaml`):**
```yaml
bt_navigator:
  ros__parameters:
    bond_heartbeat_period: 0.1
    bond_timeout: 60.0
```

**임시 수동 활성화:**
```bash
ros2 lifecycle set /bt_navigator activate
```

---

### 5. 로봇이 자꾸 제자리에서 삥글삥글 도는 문제

**원인:** `use_rotate_to_heading: true` — 목표 방향으로 먼저 회전 후 이동하는 동작이
2D Pose Estimate 방향 오차와 맞물려 계속 회전 유발

**해결 (`nav2_params.yaml`):**
```yaml
FollowPath:
  use_rotate_to_heading: false
```

---

### 6. 좁은 문/복도에서 로봇이 부딪히는 문제

**원인:** `inflation_radius: 0.55m` — 로봇 반경(0.30m)보다 팽창이 너무 커서
좁은 문 통과 경로를 생성하지 못하고 벽에 붙어 이동

**해결 (`nav2_params.yaml`):**
```yaml
local_costmap:
  inflation_layer:
    inflation_radius: 0.20   # 0.55 → 0.20

global_costmap:
  inflation_layer:
    inflation_radius: 0.35   # 0.55 → 0.35
```

---

## 수정/추가된 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `nav2.sh` | `topic_tools relay /cmd_vel_smoothed /cmd_vel` 자동 실행 추가 |
| `nav2_params.yaml` | bt_navigator bond 타임아웃, rotate_to_heading 비활성화, inflation_radius 축소 |
| `README.md` | Nav2 실행 절차, RViz2 설정, 트러블슈팅 표 전면 추가 |
| `.githooks/post-commit` | 커밋 자동 로그 기록 훅 신규 추가 |
| `log/changes.log` | 변경 이력 누적 파일 |
| `setup_hooks.sh` | 클론 후 훅 설정 스크립트 |
| `.gitignore` | `log/*` + `!log/changes.log` 예외 처리 |
| `/opt/ros/jazzy/.../indexed_8bit_image.frag` | *(시스템 파일, 레포 외부)* GLSL sampler1D 제거 |
| `/opt/ros/jazzy/.../indexed_8bit_image.material` | *(시스템 파일, 레포 외부)* 1d → 2d 텍스처 타입 변경 |

---

## Nav2 실행 순서 (현재 기준)

### 빅핑키에서
```bash
~/bringup.sh
```

### 노트북에서
```bash
# 터미널 1 — Nav2 실행
cd ~/dev_ws/vic_pinky && ./nav2.sh /home/hong/my_map.yaml

# 터미널 2 — RViz2
source /opt/ros/jazzy/setup.bash && export ROS_DOMAIN_ID=28 && rviz2

# 터미널 3 — 맵이 안 보이면 (RViz2 시작 후)
source /opt/ros/jazzy/setup.bash && export ROS_DOMAIN_ID=28
ros2 service call /map_server/load_map nav2_msgs/srv/LoadMap \
  "{map_url: '/home/hong/my_map.yaml'}"
```

### RViz2에서 자율 주행
1. **"2D Pose Estimate"** 클릭 → 로봇 현재 위치에서 실제 방향으로 드래그
2. **"2D Goal Pose"** 클릭 → 목적지 클릭+드래그
3. 로봇 출발

---

## 하드웨어 메모

| 항목 | 값 |
|------|----|
| ROS_DOMAIN_ID | 28 |
| 빅핑키 IP | 192.168.5.1 |
| 노트북 IP | 192.168.5.3 |
| LiDAR | RPLIDAR C1 (`/dev/ttyUSB2`) |
| laser_link 마운트 | `rpy="0 0 π"` → 스캔 0°=로봇 후방, 180°=전방 |
| 맵 파일 위치 | `~/my_map.yaml`, `~/my_map.pgm` |
