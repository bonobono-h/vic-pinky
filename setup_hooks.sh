#!/bin/bash
# Git 훅 설정 스크립트 (최초 1회 실행)
# 이 저장소를 새로 클론한 경우 실행하세요.

set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "Git 훅 경로를 .githooks 로 설정합니다..."
git -C "$REPO_ROOT" config core.hooksPath .githooks
chmod +x "$REPO_ROOT/.githooks/"*

echo "완료! 이제 커밋마다 log/changes.log 에 이력이 자동으로 기록됩니다."
