#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from nav2_msgs.action import NavigateThroughPoses
from geometry_msgs.msg import PoseStamped
import math

# 유리문 앞 웨이포인트 (돌아올 때 기준, amcl_pose로 측정)
GLASS_DOOR_X   = -11.170723748415115
GLASS_DOOR_Y   = -3.2165240638147097
GLASS_DOOR_Z   = -0.10422196922348517  # quaternion z
GLASS_DOOR_W   =  0.9945540614422018   # quaternion w

# 홈 위치 (맵 원점)
HOME_X   = 0.0
HOME_Y   = 0.0
HOME_Z   = 0.0
HOME_W   = 1.0

def make_pose(x, y, qz, qw):
    pose = PoseStamped()
    pose.header.frame_id = 'map'
    pose.pose.position.x = float(x)
    pose.pose.position.y = float(y)
    pose.pose.position.z = 0.0
    pose.pose.orientation.x = 0.0
    pose.pose.orientation.y = 0.0
    pose.pose.orientation.z = float(qz)
    pose.pose.orientation.w = float(qw)
    return pose

class ReturnHome(Node):
    def __init__(self):
        super().__init__('return_home')
        self._client = ActionClient(self, NavigateThroughPoses, 'navigate_through_poses')

    def send_goal(self):
        self.get_logger().info('Nav2 서버 대기 중...')
        self._client.wait_for_server()

        goal = NavigateThroughPoses.Goal()
        goal.poses = [
            make_pose(GLASS_DOOR_X, GLASS_DOOR_Y, GLASS_DOOR_Z, GLASS_DOOR_W),
            make_pose(HOME_X, HOME_Y, HOME_Z, HOME_W),
        ]

        self.get_logger().info(
            f'경로 전송: 유리문 앞 ({GLASS_DOOR_X:.2f}, {GLASS_DOOR_Y:.2f}) → 홈 (0, 0)'
        )
        future = self._client.send_goal_async(goal)
        future.add_done_callback(self._goal_response)

    def _goal_response(self, future):
        handle = future.result()
        if not handle.accepted:
            self.get_logger().error('목표 거부됨!')
            rclpy.shutdown()
            return
        self.get_logger().info('이동 시작!')
        handle.get_result_async().add_done_callback(self._result)

    def _result(self, future):
        self.get_logger().info('홈 도착 완료!')
        rclpy.shutdown()

def main():
    rclpy.init()
    node = ReturnHome()
    node.send_goal()
    rclpy.spin(node)

if __name__ == '__main__':
    main()
