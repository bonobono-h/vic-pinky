#!/bin/bash
echo "Nav2 프로세스 종료 중..."

pkill -f "nav2_bringup" 2>/dev/null
pkill -f "ros2 launch" 2>/dev/null
pkill -f "topic_tools" 2>/dev/null
pkill -f "rviz2" 2>/dev/null
pkill -f "nav2_activate" 2>/dev/null

sleep 2

# 남은 거 강제 킬
PIDS=$(ps aux | grep -E "ros2|rviz2|nav2|relay" | grep -v grep | awk '{print $2}')
if [ -n "$PIDS" ]; then
    echo "$PIDS" | xargs kill -9 2>/dev/null
fi

sleep 1

REMAINING=$(ps aux | grep -E "ros2|rviz2|nav2|relay" | grep -v grep)
if [ -z "$REMAINING" ]; then
    echo "완료! 깔끔하게 정리됐어요."
else
    echo "아직 남은 프로세스:"
    echo "$REMAINING"
fi
