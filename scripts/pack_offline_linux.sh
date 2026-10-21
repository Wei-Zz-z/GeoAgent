#!/usr/bin/env bash
# GeoAgent 离线交付包制作脚本（在【联网的 Linux 打包机】上执行）
#
# 用途：把后端依赖、uv、前端产物、源码与清单收进一个目录，供无外网的政务内网部署机使用。
# 用法：bash scripts/pack_offline_linux.sh [输出目录]
#
# 可选环境变量：
#   INCLUDE_DEV=1          额外收集 pytest 等测试依赖
#   INCLUDE_ELDER_DATA=1   把根目录 data/（养老可达性分析底数，约 130MB）打进源码包
#   SKIP_FRONTEND=1        跳过前端构建，复用已有 frontend/dist
#
# 重要：打包机的 CPU 架构必须与部署机一致，glibc 不得高于部署机
#       （否则会挑到部署机装不上的轮子）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d)"
OUT_DIR="${1:-$REPO_ROOT/outputs/geoagent-offline-$STAMP}"

echo "架构：$(uname -m)   glibc：$(ldd --version | head -1)"
echo "输出目录：$OUT_DIR"
mkdir -p "$OUT_DIR"/{app,runtime/bin,runtime/uv-python,runtime/uv-cache,web,installers,manifest}

echo "[1/5] 收集后端依赖（轮子落到交付目录，不污染本机缓存）"
export UV_CACHE_DIR="$OUT_DIR/runtime/uv-cache"
export UV_PYTHON_INSTALL_DIR="$OUT_DIR/runtime/uv-python"
export UV_PYTHON_DOWNLOADS=automatic
cd "$REPO_ROOT/backend"
if [ "${INCLUDE_DEV:-0}" = "1" ]; then
  uv sync --locked --all-groups --reinstall
else
  uv sync --locked --reinstall
fi

echo "[2/5] 收集 uv 与托管解释器"
cp "$(command -v uv)" "$OUT_DIR/runtime/bin/uv"

echo "[3/5] 构建前端"
if [ "${SKIP_FRONTEND:-0}" != "1" ]; then
  cd "$REPO_ROOT/frontend"
  npm ci
  npm run build
fi
cp -a "$REPO_ROOT/frontend/dist/." "$OUT_DIR/web/"

echo "[4/5] 打包源码"
cd "$REPO_ROOT"
EXCLUDES=(--exclude='./backend/.venv' --exclude='./frontend/node_modules'
          --exclude='./frontend/dist' --exclude='./.git'
          --exclude='./outputs' --exclude='./backend/data' --exclude='./backend/reports'
          --exclude='./backend/.pytest_cache' --exclude='*/__pycache__' --exclude='*.pyc')
[ "${INCLUDE_ELDER_DATA:-0}" = "1" ] || EXCLUDES+=(--exclude='./data')
tar "${EXCLUDES[@]}" -czf "$OUT_DIR/app/geoagent-src-$STAMP.tar.gz" .

echo "[5/5] 生成清单与校验和"
cd "$REPO_ROOT/backend"
uv export --frozen --no-hashes > "$OUT_DIR/manifest/requirements-full.txt" || echo "警告：依赖清单生成失败"
uv tree --frozen > "$OUT_DIR/manifest/uv-tree-$STAMP.txt" || echo "警告：依赖树生成失败"
cd "$OUT_DIR"
find . -type f ! -name 'SHA256SUMS' -exec sha256sum {} + > manifest/SHA256SUMS

echo
echo "完成：$OUT_DIR"
echo "下一步：确认 sha256sum -c manifest/SHA256SUMS，并把系统级安装包（nginx/PostgreSQL/PostGIS/LibreOffice）放进 installers/"