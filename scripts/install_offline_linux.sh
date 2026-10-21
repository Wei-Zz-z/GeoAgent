#!/usr/bin/env bash
# GeoAgent 离线安装脚本（在【部署机】执行，需要 root）
#
# 用法：sudo bash install_offline_linux.sh <交付包目录>
#
# 交付包自带 CPython 3.12.15（Linux x86_64）与对应依赖轮子，部署机不需要预装 Python。
# 脚本做四件事：放文件、解包自带解释器、离线建虚拟环境装依赖、生成配置模板。
set -euo pipefail

BUNDLE="${1:?用法: sudo bash install_offline_linux.sh <交付包目录>}"
APP=/opt/geoagent

if [ "$(id -u)" != "0" ]; then
  echo "请用 root 执行（sudo bash $0 ...）" >&2
  exit 1
fi

# 1. 账号与目录
id geoagent >/dev/null 2>&1 || useradd --system --create-home --home-dir "$APP" --shell /sbin/nologin geoagent
mkdir -p "$APP"/{app,data,reports,logs,web,runtime} /etc/geoagent
chmod 750 /etc/geoagent

# 2. 放运行时、源码与前端
echo "复制运行时与源码..."
cp -a "$BUNDLE/runtime/." "$APP/runtime/"
chmod +x "$APP/runtime/bin/uv" "$APP/runtime/bin/uvx" 2>/dev/null || true
tar -xzf "$BUNDLE"/app/geoagent-src-*.tar.gz -C "$APP/app"
cp -a "$BUNDLE/web/." "$APP/web/"

# 3. 解包自带 Python 并校验完整性
PY_DIST="$(ls "$APP"/runtime/python-dist/cpython-*-x86_64-unknown-linux-gnu-install_only*.tar.gz 2>/dev/null | head -1)"
if [ -x "$APP/runtime/python/bin/python3.12" ]; then
  echo "自带 Python 已解包，跳过"
elif [ -n "$PY_DIST" ]; then
  echo "解包自带 Python：$(basename "$PY_DIST")"
  ( cd "$APP/runtime/python-dist" && sha256sum -c <(grep -F "$(basename "$PY_DIST")" SHA256.txt) )
  tar -xzf "$PY_DIST" -C "$APP/runtime"
else
  echo "交付包内未找到自带 Python，将使用系统解释器" >&2
fi

PY="$APP/runtime/python/bin/python3.12"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3.12 || true)"
  echo "回退使用系统解释器：${PY:-未找到}" >&2
fi
[ -n "$PY" ] && [ -x "$PY" ] || { echo "既没有自带 Python，也没有系统 python3.12，无法继续" >&2; exit 1; }
echo "使用解释器：$PY（$("$PY" -V 2>&1)）"

# 4. uv：优先用部署机已有的，没有就用交付包里的
UV_BIN="$(command -v uv || true)"
if [ -z "$UV_BIN" ]; then
  UV_BIN="$APP/runtime/bin/uv"
  echo "未检测到系统 uv，改用交付包内的：$UV_BIN"
fi

chown -R geoagent:geoagent "$APP"

# 5. 离线建虚拟环境并安装依赖（不做任何网络解析）
echo "离线安装 Python 依赖..."
sudo -u geoagent env \
  PATH="$APP/runtime/bin:$PATH" \
  UV_CACHE_DIR="$APP/runtime/uv-cache" \
  UV_PYTHON_DOWNLOADS=never \
  bash -c "cd $APP/app/backend && '$UV_BIN' venv --python '$PY' .venv && \
           '$UV_BIN' pip install --python .venv/bin/python --no-index \
             --find-links '$APP/runtime/wheels-cp312' -r '$APP/runtime/manifest/requirements-server.txt'"

# 6. 自检
echo "自检："
sudo -u geoagent bash -c "cd $APP/app/backend && .venv/bin/python -c \"import sys, fastapi, asyncpg, geopandas, pyogrio, shapely, mcp, docx, dotenv; print('deps ok', sys.version.split()[0])\""

# 7. 配置模板（不覆盖已存在文件，不自动启用服务）
if [ ! -f /etc/geoagent/geoagent.env ]; then
  cp "$APP/app/deploy/geoagent.env.example" /etc/geoagent/geoagent.env
  echo "已生成 /etc/geoagent/geoagent.env（请填写模型、数据库、密钥后再启动服务）"
fi
chown root:geoagent /etc/geoagent/geoagent.env
chmod 640 /etc/geoagent/geoagent.env

if [ ! -f /etc/systemd/system/geoagent.service ]; then
  cp "$APP/app/deploy/geoagent.service" /etc/systemd/system/geoagent.service
  systemctl daemon-reload
  echo "已安装 /etc/systemd/system/geoagent.service（编辑 .env 后执行 systemctl enable --now geoagent）"
fi

echo
echo "安装完成。后续步骤："
echo "  1) 编辑 /etc/geoagent/geoagent.env（模型网关、数据库、GEOAGENT_AUTH_SECRET）"
echo "  2) systemctl enable --now geoagent && curl -s http://127.0.0.1:8000/api/health"
echo "  3) 配置 nginx：cp $APP/app/deploy/nginx-geoagent.conf /etc/nginx/conf.d/geoagent.conf"
echo "  详见 docs/Linux离线部署说明.md"