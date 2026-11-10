# sqlweb_offline_common

通用型离线 SQL 网页控制台。**一套代码，三种操作系统，六种数据库。**

为无外网、不能装客户端的运维/实施场景设计：目标机上只要有一个 Python 3.8+，解压即可跑，浏览器打开就能查库。

---

## 1. 它解决什么问题

现场常见三类困境：

| 困境 | 本项目的做法 |
| --- | --- |
| 内网机器装不了 Navicat / DBeaver | 纯 Web，浏览器访问，无需客户端 |
| 有安全扫描，裸奔的 Web 工具会被通报 | 强制登录 + 动态图片验证码 + 限流，扫描器看到的是 401 |
| 每换一种数据库就要换一套工具 | 六种数据库统一到同一界面，驱动按库自动降级 |

而前一版 `sqlweb_offline_kylin_v10` 是**为麒麟 V10 单一环境定制**的：驱动写死、数据源靠改 `config.json` 重启、没有登录。这一版是它的通用重写。

---

## 2. 特性

- **跨平台**：Linux（含麒麟/统信/CentOS/RHEL/Ubuntu）、Windows、macOS。全部纯标准库，不依赖任何 C 扩展
- **跨数据库**：PostgreSQL、MySQL/MariaDB、SQL Server、Oracle、达梦 DM8、SQLite
- **强制登录**：PBKDF2-HMAC-SHA256（20 万次迭代）+ 会话 Cookie，未登录访问 API 一律 401
- **动态验证码**：纯标准库手写 PNG（`zlib` + `struct`），不依赖 Pillow，单张 < 2 KB
- **界面配数据源**：增 / 改 / 删 / 设默认，先测连再保存，保存即热加载，不重启
- **只读保护**：默认拦截 INSERT / UPDATE / DELETE / DDL，可按数据源单独放开
- **多库浏览**：PostgreSQL / SQL Server 这类一个实例挂多库的，对象树先列库再列 schema，点库名即换 `dbname` 重连取数（不写回配置）
- **子路径部署**：支持 nginx 反代到 `/sqlweb/` 这类前缀，不需要改端口

---

## 3. 快速开始

### 3.1 Linux / macOS

```bash
tar -xzf sqlweb_offline_common.tar.gz
cd sqlweb_offline_common

# 环境检查（不启动）
./install.sh --check

# 离线装驱动到 .venv，然后前台启动
./install.sh --install-deps

# 或者：后台启动
./install.sh --install-deps --background
./stop.sh          # 停止
tail -f sqlweb.log # 看日志
```

不想用 `install.sh` 也可以：

```bash
./start.sh                      # 用 .venv 或系统 python3
./start.sh --port 8899          # 指定端口
python3 sqlweb.py --config /path/to/config.json
```

macOS 上双击 `start.command` 同样可以启动。

### 3.2 Windows

**推荐方式：运行 `install.bat`**。它内部已经带上了执行策略参数，不需要改系统设置：

```bat
install.bat                          :: 等同 -InstallDeps，装驱动并启动
install.bat -Check                   :: 只看环境
install.bat -InstallDeps -Port 8899 -NoStart
```

也可以用 PowerShell 直接调 `install.ps1`：

```powershell
.\install.ps1 -Check                        # 只看环境
.\install.ps1 -InstallDeps                  # 装驱动并启动
.\install.ps1 -InstallDeps -Port 8899 -NoStart
```

> **遇到「禁止运行脚本」时**：Windows 客户端默认执行策略是 `Restricted`，直接 `.\install.ps1` 会报
> `无法加载文件 ... 因为在此系统上禁止运行脚本`（`PSSecurityException` / `UnauthorizedAccess`）。
> 这是 PowerShell 的策略限制，不是脚本本身有问题。三种解法任选其一：
>
> ```powershell
> # 1) 用包装器（推荐，零副作用）
> .\install.bat -Check
>
> # 2) 单次绕过，只影响这一次
> powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Check
>
> # 3) 只对当前用户放开（长期有效，策略改为 RemoteSigned）
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

运行起来之后，双击 `start.bat` 即可启动服务。

### 3.3 首次启动必做：设置管理员

第一次启动**没有** `auth.json`，服务会进入初始化模式，控制台打印：

```
================================================================
  首次启动：服务尚未初始化管理员账号
  请打开 http://<本机IP>:8899/login 填写账号口令
  初始化令牌（必填，仅本次启动有效）：
      513153
================================================================
```

打开 `http://<机器IP>:8899/login`，填：用户名、口令（≥8 位）、**初始化令牌**。

> 为什么要令牌：如果只靠"谁先访问谁设密码"，在有多个使用者的内网里，先手可能被别人抢走。令牌只在本次启动的进程内存里，重启即变。

设置完成后 `auth.json` 落盘（权限 `0600`），以后就是普通登录。

---

## 4. 支持的数据库与驱动矩阵

驱动按优先级自动降级：**先官方驱动，再纯 Python 驱动，都没有就明确报错**（不会静默失败）。

| 数据库 | 驱动（优先级从高到低） | 依赖形态 | 跨平台 |
| --- | --- | --- | --- |
| PostgreSQL | `psycopg2` → `pg8000` | 二进制 / 纯 Python | 全平台（pg8000 兜底） |
| MySQL / MariaDB | `PyMySQL` | 纯 Python | 全平台 |
| SQL Server | `pymssql` | 二进制（含 FreeTDS） | Linux/Windows/macOS 均有 wheel |
| Oracle | `oracledb`（thin 模式）→ `cx_Oracle` | 二进制（thin 模式免客户端） | 全平台 |
| 达梦 DM8 | `dmPython` → `dmpy`（dm-pure） | 官方二进制 / **纯 Python** | **macOS 只能走 dmpy** |
| SQLite | 标准库 `sqlite3` | 无 | 全平台 |

### 4.1 达梦在 macOS 上的特殊处理

达梦官方 `dmPython` **只发 Linux/Windows 的 wheel，且全版本没有 sdist**（PyPI 遍历确认），macOS 上根本装不上；达梦也没有 macOS 版 ODBC 驱动，所以 `pyodbc` 路线同样走不通。

本项目内置三级降级：

```
dmPython（官方，Linux/Windows 优先）
   ↓ 导入失败
dmpy（dm-pure，第三方纯 Python 实现，MIT，py3-none-any）
   ↓ 也导入失败
明确报错：告知缺什么驱动、怎么装
```

`dmpy` 已用真实 DM8 实例实测通过：认证、查询、中文往返（UTF-8 与 GB18030 均验证）、参数化、元数据、分页、CLOB 读取、错误码映射。

已知 `dmpy` 的三个限制（不影响查数场景）：

- 事务 `rollback` 不生效（它是自动提交语义）
- 参数化插 CLOB 会报 `object of type 'int' has no len()`
- 占位符出现在 SELECT 列表（如 `SELECT ?, ?+1`）时类型标注有偏差

### 4.2 离线安装的驱动（`wheels/`）

包内 `wheels/` 放了 7 个 **`py3-none-any`** 纯 Python 包，共约 470 KB，Linux / Windows / macOS 通用，Python 3.8 ~ 3.13 可装：

```
PyMySQL-1.1.1-py3-none-any.whl                    # MySQL
pg8000-1.30.5-py3-none-any.whl                    # PostgreSQL 纯 Python 实现
dm_pure-1.3.5-py3-none-any.whl                    # 达梦纯 Python 实现
scramp-1.4.6-py3-none-any.whl                     # pg8000 依赖（SCRAM 认证）
asn1crypto-1.5.1-py2.py3-none-any.whl             # scramp 依赖
python_dateutil-2.9.0.post0-py2.py3-none-any.whl  # pg8000 依赖
six-1.17.0-py2.py3-none-any.whl                   # dateutil 依赖
```

> **版本为什么锁在这里**：`pg8000 1.31+` 把依赖钉成 `scramp==1.5.0`，而 `scramp 1.5.0` 要求 Python ≥ 3.11；`python-dateutil 4.0.0` 同样要求 ≥ 3.11。这两条会让整套 wheel 在 3.8~3.10 上装不进去（`ERROR: Package 'scramp' requires a different Python: 3.10.2 not in '>=3.11'`）。所以锁到 `pg8000 1.30.5 + scramp 1.4.6 + dateutil 2.9.0.post0 + PyMySQL 1.1.1`，这一组的最低要求是 Python 3.8。

覆盖了 SQLite、MySQL、PostgreSQL、达梦四种，**开箱即用**。

SQL Server 和 Oracle 的驱动进不了这个"通用 any"目录——它们**只发平台相关、且按 CPython 版本分开的二进制 wheel**，没有 `py3-none-any`：

| 驱动 | 最新版 | wheel 形态 | 意味着 |
| --- | --- | --- | --- |
| `pymssql` | 2.4.6 | `cp39`~`cp315` × {win_amd64, win_arm64, manylinux_x86_64, manylinux_aarch64, musllinux_*, macosx_arm64, macosx_x86_64} | 光一个 Python 版本就要 8 个平台文件，6 个版本合计 40+ |
| `oracledb` | 26.1.0 | `cp310`~`cp315` × {win32, win_amd64, win_arm64, manylinux2014_x86_64, manylinux2014_aarch64, musllinux_*, macosx_universal2} | 同上，且最低 cp310 |

要开箱支持这两个库，就得按"Python 版本 × 操作系统 × CPU 架构"备一整个矩阵，包体积从几百 KB 涨到数十 MB，而目标机的组合事先并不知道。

**正确做法**：目标机需要这两类库时，在**有网机器**上用**与目标机相同的 Python 版本**下载一次，把 whl 丢进 `wheels/` 再重跑安装：

```bash
# 在有网机器上（示例：Python 3.10 / Windows）
python -m pip download -d wheels pymssql oracledb
```

然后重新安装依赖：

```bash
./install.sh --install-deps      # Linux / macOS
.\install.ps1 -InstallDeps       # Windows
```

界面不会因为缺这两个库而无法启动，只会在对应数据源上提示"驱动缺失"。

---

## 5. 安全设计

这一节是"规避安全扫描"诉求的落点。

### 5.1 认证

| 项 | 实现 |
| --- | --- |
| 口令存储 | `auth.json` 只存 `salt` + `PBKDF2-HMAC-SHA256` 哈希，20 万次迭代，文件权限 `0600`，原子写入（tmp + `os.replace`） |
| 时序侧信道 | 用户名错也跑一次假 PBKDF2，账号是否存在无法通过响应时间判断 |
| 失败提示 | 用户名错与口令错返回**同一句话** |
| 会话 | 32 字节随机 token，`httpOnly` + `SameSite=Strict` Cookie；TTL 8 小时，闲置 30 分钟失效；进程内存表，重启全部失效 |
| 登录限速 | 按 IP，1 分钟内失败 5 次即封 60 秒（HTTP 429） |
| 改密 | 需原口令；改密成功后**强制注销所有会话** |
| 初始化 | 首启需控制台一次性令牌，防被抢先设置 |

### 5.2 验证码

- 4 位数字，5×7 点阵放大绘制，每个数字独立 y 轴抖动，配 3~5 条干扰线 + 60~120 个噪点
- **一次性消费**：取出即删，重放无效
- 5 分钟过期，最多存 500 条
- 响应头 `Cache-Control: no-store`，图片不落缓存
- 生成逻辑在 `captcha.py`，纯 `zlib` + `struct` 拼 PNG 字节流

### 5.3 只读保护

默认 `readonly: true`，在语句层面拦截（不是简单的字符串匹配）：

- 先剥掉 `--` 行注释和 `/* */` 块注释（跳过字符串字面量内部）
- 再按分号切分（尊重引号、括号层级）
- 每条语句取首关键字，白名单 `select / with / values / explain / show / table / declare / desc`

所以 `-- 注释\nSELECT ...` 不会被误杀，`INSERT INTO t VALUES('a;b')` 里的分号也不会误切。

按数据源可单独设 `"readonly": false` 放开写权限。

---

## 6. 配置

### 6.1 界面配置（推荐）

登录后点右上角 **数据源**：

1. **新增数据源** → 选类型（下拉框里直接标出"可用/驱动缺失"）
2. 填主机、端口、账号、口令、库名
3. **先点"测试连接"**：失败会把驱动原始报错原样显示（例如 `RuntimeError: SQLite 文件不存在：/nope/x.db`），改好后再保存
4. **保存数据源** → 写回 `config.json` 并热加载，**不需要重启**
5. 可"设默认 / 编辑 / 删除"

> 编辑已有数据源时，口令留空表示**沿用原口令**，不必重新输入。

### 6.2 文件配置

`config.json`（由 `config.example.json` 生成）：

```json
{
  "title": "SQLWeb 离线控制台",
  "host": "0.0.0.0",
  "port": 8899,
  "base_path": "",
  "readonly": true,
  "max_rows": 1000,
  "connect_timeout": 10,
  "statement_timeout_ms": 30000,
  "default_db": "",
  "databases": [
    {
      "key": "pg1",
      "label": "生产 PostgreSQL",
      "driver": "postgresql",
      "host": "10.0.0.10",
      "port": 5432,
      "user": "readonly_user",
      "password": "***",
      "dbname": "appdb",
      "readonly": true
    },
    {
      "key": "dm1",
      "label": "达梦 DM8",
      "driver": "dameng",
      "host": "10.0.0.11",
      "port": 5236,
      "user": "SYSDBA",
      "password": "***",
      "readonly": true
    },
    {
      "key": "lite",
      "label": "本地 SQLite",
      "driver": "sqlite",
      "path": "/data/demo.db",
      "readonly": true
    }
  ],
  "redis": []
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `host` / `port` | 监听地址与端口。只给本机用填 `127.0.0.1` |
| `base_path` | nginx 子路径前缀，如 `/sqlweb`；根路径部署留空 |
| `readonly` | 全局默认只读，可被单个数据源的 `readonly` 覆盖 |
| `max_rows` | 单次查询最多返回行数（上限 20000），超出标记"已截断" |
| `connect_timeout` | 连接超时（秒） |
| `statement_timeout_ms` | 语句超时（毫秒）。PG 走 `statement_timeout`，MySQL 走 `max_execution_time` |
| `databases[].driver` | `postgresql` / `mysql` / `sqlserver` / `oracle` / `dameng` / `sqlite` |
| `databases[].dbname` | PG / MySQL / SQLServer 填库名；Oracle 填 **服务名或 SID**；达梦留空（用 user 当 schema） |
| `databases[].path` | SQLite 文件绝对路径（`dbname` 会被自动同步） |

---

## 7. nginx 反向代理（子路径）

服务本身监听 `8899`，由 nginx 转出去：

```nginx
location /sqlweb/ {
    proxy_pass http://127.0.0.1:8899/;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout 10s;
    proxy_send_timeout    120s;
    proxy_read_timeout    120s;

    client_max_body_size 20m;
    proxy_buffering off;
}
```

同时把 `config.json` 的 `base_path` 设成 `/sqlweb`。

服务侧会：剥离前缀、把 `window.__BASE__` 注入前端、Cookie 的 `Path` 自动带上前缀。**不设 `base_path` 时一切按根路径工作**，两种部署方式都不用改代码。

> 权限提醒：`/var/lib/nginx/proxy` 与 `client_body` 临时目录的属主必须与 nginx worker 用户一致，否则大结果集会 500 / 413 Permission denied。

---

## 8. 与麒麟版 v2.3 的差异

| 项 | 麒麟版 v2.3 | 本版 common |
| --- | --- | --- |
| 数据库 | PG + 达梦（driver 分派写死） | 6 种，独立适配层 |
| 驱动来源 | 只打包 cp37–cp312 的 Linux wheel | 优先纯 Python，跨平台 |
| 登录 | 无 | 强制登录 + 验证码 + 限流 |
| 数据源 | 改 `config.json` 重启 | 界面增删改 + 测试连接 + 热加载 |
| 验证码 | 无 | 纯标准库 PNG |
| 启动预检 | 会去连默认库，连不上拒绝启动 | **不连默认库**，连接按需建立 |
| Redis 面板 | 有（`/api/redis/*` 5 个接口） | 保留，`redis` 段有配置才启用 |
| 前端 | 单库 + Redis 双面板 | 多数据源 + 对象树 + 数据源管理 + 改密 |

---

## 9. 目录结构

```
sqlweb_offline_common/
├── sqlweb.py              主程序：HTTP 路由、认证中间件、数据源 API、查询执行
├── drivers.py             多数据库适配层（6 个 Driver 类）
├── auth.py                AuthStore / SessionManager / RateLimiter / CaptchaStore
├── captcha.py             纯标准库 PNG 验证码
├── redis_panel.py         Redis 面板（移植自 v2.3）
├── login.html             登录页（含首次初始化引导）
├── index.html             主界面（对象树 / SQL 编辑器 / 结果表 / 数据源管理 / 改密）
├── config.example.json    配置模板
├── wheels/                7 个纯 Python wheel，离线装驱动
├── install.sh             Linux/macOS 安装脚本
├── install.ps1            Windows 安装脚本
├── start.sh               Linux/macOS 启动
├── start.command          macOS 双击启动
├── start.bat              Windows 启动
├── stop.sh                停止后台服务
├── README.md              本文件
├── config.json            （运行后生成，含连接信息，权限 600）
├── auth.json              （初始化后生成，含口令哈希，权限 600）
└── sqlweb.log             （--background 时生成）
```

---

## 10. 已知限制

- **SQL Server / Oracle 需要二进制 wheel**：包内未带，见 4.2
- **`dmpy` 的事务语义不完整**：只读查数不受影响；需要事务的场景请用官方 `dmPython`
- **会话存内存**：重启服务要重新登录（对离线运维工具是有意为之）
- **仅 HTTP**：不做 TLS。对外暴露请走 nginx + HTTPS，或只绑 `127.0.0.1`
- **数据源口令明文存 `config.json`**：这是离线内网工具的取舍。文件权限已设 `0600`，但服务器被拿到 root 就等于丢口令。若有更高要求，可改为启动时用环境变量注入
- **跨库浏览是逐次重连**：PostgreSQL / SQL Server 一个连接只绑一个库，切库靠换 `dbname` 新建连接，切一次多一次建连开销（内网通常 < 0.2 s）。不要在一条 SQL 里跨库 JOIN —— 驱动层不支持，请分成两条查询
- **MySQL 的库就是 schema**：`information_schema.SCHEMATA` 返回的已经是全部库，所以对象树只显示一层，不再套库节点。Oracle / 达梦的"库"即用户，同理单层

---

## 11. 排障

| 现象 | 处理 |
| --- | --- |
| 界面里某库显示"驱动缺失" | 装对应 wheel：`pip install --no-index --find-links=wheels <包名>`；或在 `install.sh --install-deps` 时把 wheel 放进 `wheels/` |
| 登录页提示"验证码已失效" | 验证码 5 分钟过期且一次性，刷新图片重试 |
| 登录 429 | 同一 IP 1 分钟内失败 5 次被限速，等 60 秒 |
| 忘记管理员口令 | 删掉 `auth.json` 后重启，回到初始化模式（会打印新的初始化令牌） |
| 改了 `config.json` 但界面没变 | 界面配置走接口落盘；手改文件需要重启服务。界面里改则不需要 |
| 达梦在 mac 上报 `ModuleNotFoundError: dmPython` | 装纯 Python 驱动：`pip install dm-pure`，服务会自动降级 |
| Windows 下中文乱码 | 用 `chcp 65001` 或在 PowerShell 里跑；`start.bat` 已内置 |

---

## 12. 许可

内部运维工具，按现状提供。
