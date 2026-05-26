#!/bin/bash
export ROS_DOMAIN_ID=28
export FASTRTPS_DEFAULT_PROFILES_FILE=~/dev_ws/vic_pinky/fastdds_vicpinky.xml

pkill -9 -f "component_container" 2>/dev/null
pkill -9 -f "nav2_bringup" 2>/dev/null
sleep 2

cd ~/dev_ws/vic_pinky
python3 pillar_filter_node.py &

# Nav2 백그라운드로 시작
bash nav2.sh &
NAV2_PID=$!

# Nav2 active 될 때까지 대기 후 전역 위치 초기화
echo "Nav2 시작 대기 중..."
sleep 15
source /opt/ros/jazzy/setup.bash
ros2 service call /reinitialize_global_localization std_srvs/srv/Empty {}
echo "AMCL 전역 초기화 완료 — 라이다로 위치 수렴 중..."

wait $NAV2_PID
