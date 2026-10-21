# GeoAgent 政务内网（Linux x86_64）离线部署说明

> 适用对象：**无外网**的政务内网 Linux 服务器（x86_64），部署 FastAPI 后端 + nginx + PostgreSQL/PostGIS。
> 交付方式：交付包整体拷入内网后按本文档执行，全程不需要联网。
> 开发规范与业务口径见仓库根 `AGENTS.md`。

---

## 1. 交付包内容

```text
geoagent-offline-20261014/
├── app/geoagent-src-*.tar.gz   源码（不含虚拟环境与运行数据）
├── runtime/
│   ├── bin/uv、bin/uvx         Linux x86_64 版 uv 0.10.8（部署机已有 uv 时可不用）
│   ├── python-dist/            自带 CPython 3.12.15（Linux x86_64 官方发行包 + 官方 sha256）
│   ├── wheels-cp312/           Python 3.12 的 68 个 Linux 依赖轮子
│   └── manifest/               依赖清单（requirements-server.txt 为实际安装列表）
├── web/                        前端构建产物（Vite dist，跨平台通用）
├── manifest/SHA256SUMS         全部文件校验和
└── installers/                 系统级离线包（nginx / PostgreSQL / PostGIS / LibreOffice，需按发行版补齐）
```

> 本期交付**不含养老可达性评估**（`ElderCareAgent`）。它依赖的 `pyrosm`/`cykhash` 已从安装列表中移除，
> 因此安装过程完全不需要编译器；相关源码仍在仓库里，后续要用时再单独补齐依赖。

## 2. 部署机要求

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| 架构 | x86_64 | `uname -m` |
| glibc | **≥ 2.28** | 由 `pyproj 3.7.2`、`numpy 2.5.2` 的轮子标签决定；CentOS 7（2.17）不支持 |
| 可用系统 | Rocky/Alma/RHEL 8+、麒麟 Kylin V10、统信 UOS 20、Ubuntu 20.04+、Debian 10+ | |
| Python | **不需要预装** | 包内自带 CPython 3.12.15；想用系统 Python 3.12 也可以 |
| uv | 已有即可，没有可用包内版本 | 安装脚本会自动判断 |
| 磁盘 | /opt 预留 ≥ 5GB | 依赖 + 运行数据 + 快报输出 |
| 其他 | nginx、PostgreSQL+PostGIS、pg_dump | `installers/` 为空时从内网软件源安装 |

先跑预检脚本：

```bash
bash scripts/preflight_linux.sh
```

重点确认：`uname -m` 为 `x86_64`、glibc ≥ 2.28。Python 由交付包自带，不依赖系统版本。

## 3. 安装步骤

### 3.1 创建账号与目录

```bash
sudo useradd --system --create-home --home-dir /opt/geoagent --shell /sbin/nologin geoagent
sudo mkdir -p /opt/geoagent/{app,data,reports,logs,web,runtime} /etc/geoagent
sudo chown -R geoagent:geoagent /opt/geoagent
sudo chmod 750 /etc/geoagent
```

### 3.2 放运行时、源码与前端

```bash
BUNDLE=/path/to/geoagent-offline-20261014      # 交付包解压后的目录

sudo cp -a  $BUNDLE/runtime/. /opt/geoagent/runtime/
sudo chmod +x /opt/geoagent/runtime/bin/uv /opt/geoagent/runtime/bin/uvx 2>/dev/null || true
sudo tar -xzf $BUNDLE/app/geoagent-src-*.tar.gz -C /opt/geoagent/app
sudo cp -a  $BUNDLE/web/. /opt/geoagent/web/
sudo chown -R geoagent:geoagent /opt/geoagent
```

> `skills/` 目录必须可写：`skills/qa-library/assets/datasets.json` 是期次登记的唯一来源，
> 页面「套合入库」会写入新期次。

### 3.3 离线安装 Python 依赖（关键步骤）

交付包自带 CPython 3.12.15（Linux x86_64 官方发行包），部署机不需要预装 Python 与 pip。
一键脚本会自动完成解包解释器、建虚拟环境、装依赖三件事：

```bash
sudo bash $BUNDLE/scripts/install_offline_linux.sh $BUNDLE
```

想手工执行的话（与脚本等价）：

```bash
sudo useradd --system --create-home --home-dir /opt/geoagent --shell /sbin/nologin geoagent 2>/dev/null || true
sudo mkdir -p /opt/geoagent/{app,data,reports,logs,web,runtime}
sudo cp -a $BUNDLE/runtime/. /opt/geoagent/runtime/
sudo chmod +x /opt/geoagent/runtime/bin/uv /opt/geoagent/runtime/bin/uvx
sudo tar -xzf $BUNDLE/app/geoagent-src-*.tar.gz -C /opt/geoagent/app
sudo cp -a $BUNDLE/web/. /opt/geoagent/web/

# 解包自带解释器（先按官方 sha256 校验发行包，Linux 上解包会正确还原符号链接）
cd /opt/geoagent/runtime/python-dist
sha256sum -c SHA256.txt
sudo tar -xzf cpython-3.12.15+*-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz -C /opt/geoagent/runtime
sudo chown -R geoagent:geoagent /opt/geoagent

sudo -iu geoagent
export PATH=/opt/geoagent/runtime/bin:$PATH        # 用部署机自带 uv 时可省略
export UV_PYTHON_DOWNLOADS=never
cd /opt/geoagent/app/backend
uv venv --python /opt/geoagent/runtime/python/bin/python3.12 .venv
uv pip install --python .venv/bin/python --no-index \
    --find-links /opt/geoagent/runtime/wheels-cp312 \
    -r /opt/geoagent/runtime/manifest/requirements-server.txt
.venv/bin/python -c "import sys, fastapi, asyncpg, geopandas, pyogrio, shapely, mcp, docx, dotenv; print('deps ok', sys.version.split()[0])"
```

如果想改用部署机自带的 Python 3.12，把 `--python` 指向 `/usr/bin/python3.12` 即可，轮子目录不变。
若那台机器是 3.13/3.14，需要另配对应版本的轮子——当前包只带 cp312 一套，
告诉我版本我按那个版本重新出包。
### 3.4 配置环境变量

编辑 `/etc/geoagent/geoagent.env`（可先复制 `$BUNDLE` 内 `deploy/geoagent.env.example`）：

```env
# 模型：指向内网 OpenAI 兼容网关（不改代码即可生效）
OPENAI_BASE_URL=http://<内网模型网关>/v1
OPENAI_API_KEY=<网关分配的 key>
GEOAGENT_DEFAULT_MODEL=qwen3.8-27b

# 数据库
GEOAGENT_PG_DSN=postgresql://geoagent_ro:<密码>@<库地址>:5432/land_change
GEOAGENT_CHAT_DSN=postgresql://geoagent_rw:<密码>@<库地址>:5432/geoagent_chat
GEOAGENT_CHAT_SCHEMA=geoagent
GEOAGENT_PG_WHITELIST=data."2026_1_change_landuse",data."2026_2_change_landuse",knowledge_base.dict_tblx,knowledge_base.dict_land_classification_summary

# 运行时目录
GEOAGENT_DATA_DIR=/opt/geoagent/data
GEOAGENT_REPORTS_DIR=/opt/geoagent/reports

# 入口验证：内网没有票据校验地址时用 dev
GEOAGENT_AUTH_MODE=dev
GEOAGENT_AUTH_SECRET=<openssl rand -base64 48>
GEOAGENT_AUTH_COOKIE_SECURE=1        # 已用 https 时置 1；仍是 http 时必须置 0

# MCP 对外接口
GEOAGENT_MCP_TOKENS=<令牌>:<调用方名>
GEOAGENT_PUBLIC_BASE_URL=https://<域名>

# 可选
# GEOAGENT_PDF_CONVERTER=/usr/bin/soffice
# GEOAGENT_PG_DUMP=/usr/pgsql-16/bin/pg_dump
```

```bash
sudo chown root:geoagent /etc/geoagent/geoagent.env
sudo chmod 640 /etc/geoagent/geoagent.env
```

### 3.5 数据库准备

```sql
-- 业务库只读账号
CREATE ROLE geoagent_ro LOGIN PASSWORD '<密码>';
GRANT CONNECT ON DATABASE land_change TO geoagent_ro;
GRANT USAGE ON SCHEMA data, knowledge_base TO geoagent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA data, knowledge_base TO geoagent_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA data, knowledge_base GRANT SELECT ON TABLES TO geoagent_ro;

-- 会话与套合写入账号
CREATE ROLE geoagent_rw LOGIN PASSWORD '<密码>';
CREATE DATABASE geoagent_chat OWNER geoagent_rw;
-- 套合入库需要写回业务库 data schema 时：GRANT USAGE, CREATE ON SCHEMA data TO geoagent_rw;
```

会话表由应用启动时自动创建（`geoagent` schema）。本机新装数据库时记得
`CREATE EXTENSION IF NOT EXISTS postgis;`。

### 3.6 启动服务

```bash
sudo cp /opt/geoagent/app/deploy/geoagent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now geoagent
sudo systemctl status geoagent
curl -s http://127.0.0.1:8000/api/health          # 期望 {"status":"ok"}
```

报“找不到 .venv/bin/python”，说明 3.3 没在 `backend` 目录下执行。

### 3.7 nginx 反向代理

```bash
sudo cp /opt/geoagent/app/deploy/nginx-geoagent.conf /etc/nginx/conf.d/geoagent.conf
# 按需修改 server_name 与证书路径
sudo nginx -t && sudo systemctl reload nginx

# SELinux 为 enforcing 时（麒麟/CentOS 系）必须放行
sudo setsebool -P httpd_can_network_connect 1
sudo firewall-cmd --permanent --add-service=https && sudo firewall-cmd --reload
```

## 4. 验收

```bash
cd /opt/geoagent/app/backend
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/datasets            # 应列出已登记期次

.venv/bin/python ../scripts/smoke_pg.py
.venv/bin/python ../scripts/eval_sql_agent.py --refs-only

TOKEN=$(grep -E '^GEOAGENT_MCP_TOKENS=' /etc/geoagent/geoagent.env | cut -d= -f2 | cut -d, -f1)
.venv/bin/python ../scripts/check_mcp.py --url http://127.0.0.1:8000/mcp/ --token "$TOKEN" --skip-ask

ss -lntp | grep -E '8000|443'                          # 8000 只能监听 127.0.0.1
```

浏览器（内网客户端访问 `https://<域名>`）：
1. 页面正常打开；
2. 选一期数据，问标准问题（如“耕地转为建设用地的面积是多少”），出现工具卡片、表格/图表与结论；
3. 生成快报，能在线预览并下载 Word；
4. 有几何的期次问预警图斑，地图与表格联动；
5. 套合入库一套 Shapefile 后，“数据表”下拉出现新期次。

## 5. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 安装报“找不到匹配的发行版” | 用了系统 3.13/3.14 但包内只有 cp312 轮子；改用包内自带解释器，或按该版本重新出包 |
| `pyproj`/`numpy` 报平台不兼容 | 部署机 glibc < 2.28，需换系统或在新环境重新出包 |
| 启动日志提示“配置看起来是空的” | systemd 未读到 env 文件，检查 `ExecStart` 的 `--env-file` 与文件权限 |
| 页面能开但请求全部 401 | `GEOAGENT_AUTH_MODE=ticket` 但内网访问不到校验地址；改 dev 或指内网转发 |
| 登录后刷新掉线 | `GEOAGENT_AUTH_COOKIE_SECURE=1` 但访问的是 http；改 0 或配好 https |
| nginx 502 | SELinux 未放行，或后端未启动 |
| 地图空白但图斑能显示 | 内网访问不到天地图瓦片（客户端需出网），属预期 |
| 快报生成报写文件失败 | `GEOAGENT_REPORTS_DIR` 不可写，检查 systemd 的 `ReadWritePaths` |
| 删除期次报备份失败 | 缺 `pg_dump`，用 `GEOAGENT_PG_DUMP` 指定路径 |
| 命令行登记期次后查询被护栏拒绝 | 白名单启动时加载，重启后端生效；页面套合入库会自动刷新 |

日志：`journalctl -u geoagent -f`、`/opt/geoagent/data/pg_audit.jsonl`、
`/opt/geoagent/data/auth_audit.jsonl`、`/var/log/nginx/`。

## 6. 附录：本期部署的能力边界

| 功能 | 依赖模型/外网 | 本次交付 |
| --- | --- | --- |
| 标准问题库命中的统计问答 | 否 | 可用 |
| 受控 SQL、确定性统计工具 | 否 | 可用 |
| Word 快报生成（默认模板 / 上传模板回填） | 否 | 可用 |
| MCP `generate_briefing`、`list_datasets` | 否 | 可用 |
| 套合入库、期次管理、区县地图、预警图斑上图 | 否 | 可用 |
| 会话持久化、审计日志 | 否 | 可用 |
| 未命中问题库的自由问数、通用对话、Web 入口“生成快报” | 是（内网模型网关） | 需网关 |
| 用户登录（ticket 票据校验） | 是（省域平台或内网转发） | 需转发，否则用 dev |
| 地图底图瓦片 | 客户端需出网 | 需内网瓦片代理，否则无底图 |
| Embedding 语义检索 | 是 | 缺失时自动降级为关键词检索 |
| 养老可达性评估（ElderCareAgent） | 否 | **本期不部署**（需补 pyrosm/cykhash） |