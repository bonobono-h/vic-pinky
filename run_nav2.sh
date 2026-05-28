#!/bin/bash
export ROS_DOMAIN_ID=28
export FASTRTPS_DEFAULT_PROFILES_FILE=~/dev_ws/vic_pinky/fastdds_vicpinky.xml

pkill -9 -f "component_container" 2>/dev/null
pkill -9 -f "nav2_bringup" 2>/dev/null
pkill -9 -f "pillar_filter_node" 2>/dev/null
pkill -9 -f "topic_tools relay" 2>/dev/null
sleep 2

cd ~/dev_ws/vic_pinky
python3 pillar_filter_node.py &

# Nav2 백그라운드로 시작
bash nav2.sh &
NAV2_PID=$!

wait $NAV2_PID
