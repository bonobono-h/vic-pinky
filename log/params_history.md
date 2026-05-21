# VicPinky Nav2 파라미터 변경 이력

파라미터 튜닝 기록. 테스트 결과와 함께 값 변화를 추적합니다.

---

## 현재 파라미터 스냅샷 (2026-05-21)

### 컨트롤러: RegulatedPurePursuit (RPP)
| 파라미터 | 값 | 설명 |
|---------|-----|------|
| `desired_linear_vel` | 0.30 m/s | 목표 직진 속도 |
| `lookahead_dist` | 0.30 m | 경로 추종 전방 거리 |
| `min_lookahead_dist` | 0.20 m | 최소 전방 거리 |
| `max_lookahead_dist` | 0.60 m | 최대 전방 거리 |
| `rotate_to_heading_min_angle` | 0.436 rad (25°) | 제자리 회전 시작 각도 |
| `rotate_to_heading_angular_vel` | 0.9 rad/s | 제자리 회전 속도 |
| `use_rotate_to_heading` | true | 코너에서 정지 후 90도 회전 |
| `allow_reversing` | false | 후진 경로 허용 안 함 |
| `max_angular_accel` | 3.2 | 최대 각가속도 |

### 플래너: SmacPlanner2D
| 파라미터 | 값 | 설명 |
|---------|-----|------|
| `tolerance` | 0.5 m | 목표 허용 오차 |
| `allow_unknown` | false | 미탐색 영역 통과 금지 |
| `cost_travel_multiplier` | 2.5 | 비용 민감도 (높을수록 중앙 유도) |
| `use_final_approach_orientation` | true | 목적지에서 지정 방향으로 도착 |

### Global Costmap
| 파라미터 | 값 | 설명 |
|---------|-----|------|
| `robot_radius` | 0.27 m | 실제 로봇 반경 (54cm 기준) |
| `inflation_radius` | 0.42 m | 벽에서 경로 밀어내는 범위 |
| `cost_scaling_factor` | 3.0 | 비용 감쇠 기울기 |

### Local Costmap
| 파라미터 | 값 | 설명 |
|---------|-----|------|
| `robot_radius` | 0.35 m | 실시간 장애물 회피 반경 (라이다 사각지대 포함) |
| `footprint_padding` | 0.03 m | 추가 안전 패딩 |
| `inflation_radius` | 0.15 m | 실시간 장애물 팽창 범위 |
| `cost_scaling_factor` | 3.5 | 비용 감쇠 기울기 |

### Collision Monitor
| 파라미터 | 값 | 설명 |
|---------|-----|------|
| `FootprintApproach.time_before_collision` | 1.2 s | 충돌 예상 시 감속 |
| `RearCritical.action_type` | stop | 후방 장애물 즉시 정지 |
| `scan.topic` | scan_filtered | 라이다 소스 토픽 |
| `scan.min_height` | 0.05 m | 감지 최소 높이 |

### Recovery BT (nav2_bt_obstacle_wait.xml)
| 순위 | 동작 | 조건 |
|------|------|------|
| 1 | BackUp 0.20m | 후방 막히면 Collision Monitor가 자동 정지 |
| 2 | Wait 5s | 동적 장애물(사람) 통과 대기 |
| 3 | Spin 90° | 새 방향으로 경로 재탐색 |
| 4 | ClearCostmap | 유령 장애물 제거 후 재계획 |
| 5 | Wait 10s | 최종 대기 |

---

## 변경 이력

### 2026-05-21 — 주행 마무리 v1 (커밋: 63d4655)

**DWB → RPP 컨트롤러 전환**
- 이유: 공장 환경 직선주행 + 정확한 90도 코너 회전 필요
- `use_rotate_to_heading: true` → 코너에서 정지 후 제자리 회전

**경로 중앙 유도 튜닝 과정**
| 시도 | inflation_radius | cost_travel_multiplier | lookahead_dist | 결과 |
|------|-----------------|----------------------|----------------|------|
| 초기 | 0.15 | 1.5 | 0.35 | 벽에 붙어서 주행 |
| 2차 | 0.30 | 2.0 | 0.35 | 개선됨, 코너에서 일찍 꺾임 |
| 3차 | 0.40 | 2.5 | 0.35 | 조금 나아짐 |
| 4차 | 0.42 | 2.5 | 0.30 | 코너 개선 |
| **실패** | 0.55 | 3.0 | 0.25 | 복도 전체 막혀 로봇 거의 안 움직임 |
| **최종** | **0.42** | **2.5** | **0.30** | 주행 OK ✓ |

**Collision Monitor 버그 수정**
- `scan_filtered_safe` → `scan_filtered` (없는 토픽이라 전혀 동작 안 하던 것 수정)
- `min_height: 0.15 → 0.05` (낮은 장애물도 감지)

**Local Costmap 안전 범위 확대**
- `robot_radius: 0.30 → 0.35` (2D 라이다 사각지대 커버)
- `footprint_padding: 0.03` 추가
- `inflation_radius: 0.10 → 0.15`

**RearCritical 개선**
- `action_type: slowdown → stop` (후방 장애물 즉시 정지)
- `velocity_scaling_factor` 제거 (stop은 불필요)

---

> 다음 튜닝 시 이 파일에 날짜/값/결과 추가
