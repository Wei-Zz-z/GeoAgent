#!/usr/bin/env bash
# GeoAgent 部署机预检：只读检查，不改动任何系统配置
# 用法：bash preflight_linux.sh
set -uo pipefail

echo "== 架构与系统 =="
uname -m
head -3 /etc/os-release 2>/dev/null
echo "glibc: $(ldd --version 2>/dev/null | head -1)"
echo "内核: $(uname -r)"

echo
echo "== 资源 =="
free -h 2>/dev/null | head -2
df -h /opt /var 2>/dev/null

echo
echo "== 时间（登录态与票据校验依赖系统时间） =="
timedatectl status 2>/dev/null | head -3 || date

echo
echo "== Python =="
for v in python3.12 python3.13 python3.14 python3; do
  if command -v "$v" >/dev/null 2>&1; then
    echo "  $v -> $("$v" -V 2>&1)  ($(command -v "$v"))"
  fi
done

echo
echo "== 已有组件 =="
nginx -v 2>&1 | head -1
psql --version 2>/dev/null || echo "  psql: 未安装"
pg_dump --version 2>/dev/null || echo "  pg_dump: 未安装（删除期次前的自动备份需要）"
soffice --version 2>/dev/null | head -1 || echo "  soffice: 未安装（仅 PDF/.doc 转换需要）"
gcc --version 2>/dev/null | head -1 || echo "  gcc: 未安装（当前交付内容不需要编译器）"

echo
echo "== 安全基线 =="
getenforce 2>/dev/null || echo "  SELinux: 未启用"
systemctl is-active firewalld 2>/dev/null || systemctl is-active ufw 2>/dev/null || echo "  防火墙: 未知"
echo "监听端口:"
ss -lntp 2>/dev/null | head -10

echo
echo "== 结论速查 =="
echo "  1) uname -m 必须是 x86_64"
echo "  2) glibc 必须 >= 2.28（否则 pyproj/numpy 的轮子装不上）"
echo "  3) Python 由交付包自带（CPython 3.12.15），部署机无需预装"