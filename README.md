# sqlweb_offline_common

通用型离线 SQL 网页控制台。**一套代码，三种操作系统，六种数据库，一个 Python 3.7 就能跑。**

为无外网、不能装客户端、不能装 pip 包的运维/实施场景设计：目标机上只要有一个 **Python 3.7 及以上**，解压即可跑，浏览器打开就能查库。

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
- **跨数据库**：PostgreSQL、MySQL/MariaDB、SQL Server、Oracle、达梦 DM8、SQLite、**Redis**（Redis 在同一个"数据库类型"下拉里，见 5.5）
- **零依赖部署**：驱动随包放在 `vendor/`，**不需要 pip、不需要 venv、不需要外网**，Python 3.7 ~ 3.13 都直接可用
- **强制登录**：PBKDF2-HMAC-SHA256（20 万次迭代）+ 会话 Cookie，未登录访问 API 一律 401
- **动态验证码**：纯标准库手写 PNG（`zlib` + `struct`），不依赖 Pillow，单张 < 2 KB
- **界面配数据源**：增 / 改 / 删 / 设默认，先测连再保存，保存即热加载，不重启
- **只读保护**：默认拦截 INSERT / UPDATE / DELETE / DDL，可按数据源单独放开
- **多库浏览**：PostgreSQL / SQL Server 这类一个实例挂多库的，对象树先列库再列 schema，点库名即换 `dbname` 重连取数（不写回配置）
- **多账号并发**：状态库 `sqlweb.db` 的 `users` 表存多个账号，管理员可逐个分配"能看哪几个数据源"；多人不同账号同时用，会话与限速互不影响
- **可见模式白名单**：每个数据源可配 `visible_schemas`，只把要给人看的库/模式列进对象树（达梦一长串 SYS / SYSDBA / EAL* 全都能藏掉）
- **Redis 面板**：Redis 既在"新增数据源 → 数据库类型"下拉里（选 Redis 就填连接方式（单机 / 哨兵 Sentinel）、主机或哨兵节点清单、master 名称、库号 / ACL 用户名，测试连与保存都落到状态库 `sqlweb.db` 的 `redis` 表），也保留独立面板：概览（版本 / 内存 / 命中率 / keyspace）、SCAN 浏览 key（带类型与 TTL）、按 key 展开 string / hash / list / set / zset，以及命令框直接执行（只读实例按白名单放行）。管理员可在两处增删改 Redis 实例，并按账号分配可见实例
- **表名搜索**：对象树顶部输入框，按表名模糊搜（不区分大小写）。已展开的库在本地即时过滤；停顿 350 ms 或回车则跨**所有可见模式**搜表，点结果直接看列、双击直接生成 `SELECT * FROM schema.table`。搜索严格受 `visible_schemas` 白名单约束，没权限的模式不会被搜到
- **对象树按类型分组**（common-1.3.0 新增）：schema 展开后分成 **表 / 视图 / 存储过程 / 函数 / 序列 / 触发器** 六组，每组带数量，空组不显示；点视图、过程、函数直接看**定义（DDL）**
- **对象树三套交互**（common-1.3.0 新增）：双击表看列结构 + 建表语句、双击视图/过程/函数看语句；右击表可「查询（前 100 行）/ 生成 INSERT 语句（取 1 行真实数据，只填不执行）/ 查看建表语句 / 复制表名」；右击过程与函数可「执行」——按参数清单弹窗列出参数名、类型、IN/OUT 与输入框，实时预览 `CALL` / `SELECT ... FROM DUAL` 语句，跑完直接出结果集。详见 5.10
- **编辑器语法高亮 + 自动补全**（common-1.3.0 新增）：零外部依赖（不引 CodeMirror），关键字 / 字符串 / 数字 / 注释分色；输入时按"关键字 / 表名 / 别名.列名"给候选，Tab 或回车落地
- **性能**（common-1.3.0 新增）：元数据 TTL 缓存（默认 120 s）+ 每数据源空闲连接复用（最多 2 条，取出先 `SELECT 1` 验活）+ >2 KB 的 JSON 响应 gzip
- **子路径部署**：支持 nginx 反代到 `/sqlweb/` 这类前缀，不需要改端口

---

## 3. 快速开始

### 3.1 Linux / 麒麟 / macOS：一条命令

```bash
tar -xzf sqlweb_offline_common_common-1.3.0.tar.gz
cd sqlweb_offline_common_common-1.3.0

./install.sh
```

`./install.sh` **不带参数即"一键部署"**：挑解释器 → 自检程序文件 → 探测六种驱动 → 生成 `config.json`（首次启动即导入 `sqlweb.db`，随后改名留档）→ 后台启动 → 打印访问地址和初始化令牌。跑完直接打开浏览器即可。

> 如果打包工具丢了执行权限（提示 `Permission denied`），用 `sh install.sh` 同样有效，不影响任何功能。

常用参数：

```bash
./install.sh              # 一键：检查 + 生成配置 + 后台启动 + 打印地址与令牌
./install.sh --check      # 只检查环境，不改任何文件（查驱动、查 Python、给建议）
./install.sh --foreground # 前台启动，Ctrl+C 退出（调试用）
./install.sh --no-start   # 只准备（生成配置），不启动
./install.sh --port 8080  # 换端口（已有 sqlweb.db 就写状态库，否则写 config.json 种子）
./install.sh --base-path /sqlweb   # nginx 子路径前缀（写 "/" 表示根路径）
./install.sh --host 127.0.0.1   # 只监听本机

./stop.sh                 # 停止后台服务
python3 setconfig.py      # 看当前生效配置（端口 / 子路径 / 只读 / 数据源数）
tail -f sqlweb.log        # 看日志（每次启动会保留上一轮为 sqlweb.log.prev）
```

不想用 `install.sh` 也可以：

```bash
./start.sh                      # 自动挑 .venv 或系统 python3
./start.sh --port 8899
python3 sqlweb.py --config /path/to/sqlweb.db      # 也认旧的 config.json：首启一次性导入后改名留档
```

macOS 上双击 `start.command` 同样可以启动。

### 3.2 Windows

**推荐方式：运行 `install.bat`**。它内部已经带上了执行策略参数，不需要改系统设置：

```bat
install.bat                          :: 检查环境并启动
install.bat -Check                   :: 只看环境
install.bat -InstallDeps -Port 8899 -NoStart
```

也可以用 PowerShell 直接调 `install.ps1`：

```powershell
.\install.ps1 -Check
.\install.ps1 -Port 8899 -NoStart
```

`vendor/` 在包里时，`-InstallDeps` 会自动跳过 pip 安装——Windows 同样不需要联网装驱动。

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
>
> 运行起来之后，双击 `start.bat` 即可启动服务。

### 3.3 麒麟 V10 / 信创专项

麒麟 V10 自带 `python3` 是 **3.7.9**，本版已把门槛降到 3.7，并绕开了 pip 与 venv，所以**直接可用**：

```bash
./kylin.sh            # 一键部署（与 ./install.sh 等价，检测到麒麟会提示）
./kylin.sh check      # 只检查环境
```

需要开机自启时（**要 root**）：

```bash
sudo ./kylin.sh service install     # 注册 systemd 服务、设为开机自启、立即启动
sudo ./kylin.sh service status      # 查看状态
sudo ./kylin.sh service uninstall   # 卸载（程序文件与配置保留）
```

服务名是 `sqlweb-offline`，unit 文件写在 `/etc/systemd/system/sqlweb-offline.service`，日志同时落 `sqlweb.log`。

> 本机没有 systemd（容器环境）时，`service` 子命令会**明确报错并让你改用 `./install.sh`**，不会假装注册成功。

麒麟上的驱动情况（3.7.9 实测）：

| 数据库 | 3.7.9 下 | 说明 |
| --- | --- | --- |
| PostgreSQL | ✅ `pg8000` | 纯 Python |
| MySQL / MariaDB | ✅ `pymysql` | 纯 Python |
| 达梦 DM8 | ✅ `dmpy`（dm-pure） | 纯 Python |
| SQLite | ✅ 标准库 | 无依赖 |
| SQL Server | ⚠️ 不可用 | `pymssql` 要求 Python 3.8+ 的二进制 wheel |
| Oracle | ⚠️ 不可用 | `oracledb` 要求 Python 3.8+ 的二进制 wheel |

现场若必须连 SQL Server / Oracle，升级到系统自带的更高版本解释器（如 `python3.8`）即可，程序与包都不用换；或给该解释器预装对应驱动，`drivers.py` 会自动识别。

### 3.4 首次启动必做：设置管理员

第一次启动时状态库里还没有账号，服务会进入初始化模式，控制台（和 `install.sh` 的输出）会打印：

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

设置完成后账号写进 `sqlweb.db` 的 `users` 表（库文件权限 `0600`），以后就是普通登录。

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

### 4.1 达梦在 macOS / Linux 低版本上的特殊处理

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

### 4.2 `vendor/` 是什么，为什么不用 pip

包内有两套等价的驱动载体：

| 目录 | 形态 | 用途 |
| --- | --- | --- |
| `vendor/` | 已经解包好的纯 Python 包（含 `.dist-info`） | **运行时直接 `sys.path` 加载，零安装** —— 默认走这条 |
| `wheels/` | 10 个 `py3-none-any` whl | 留档 / 需要重装或补齐时用，`install.sh` 会在 vendor 不完整时**就地解包**（只用标准库 `zipfile`，不调 pip） |

为什么不能简单 `pip install`：`pg8000 1.30.5`、`scramp 1.4.6`、`dm_pure 1.3.5` 的元数据都写着 `Requires-Python >=3.8`，而麒麟的 `python3` 是 3.7.9，**pip 会硬拒**。但它们本身都是 `py3-none-any` 纯 Python 代码，解包即可用。

所以本版的组合是：

- `pg8000` 降到 **1.29.8**（声明 `>=3.7`）、`scramp` 降到 **1.4.2**（声明 `>=3.7`），3.7 上 pip 也装得进
- 补齐 3.7 缺的 `importlib_metadata 4.13.0` + `zipp 3.15.0` + `typing_extensions 4.7.1`（3.7 没有 `importlib.metadata`，scramp 会回退到 `importlib_metadata`，后者又需要 `typing_extensions.Protocol`）
- `dm_pure 1.3.5` 全版本都写 `>=3.8`，**只能走 vendor 解包**（这正是 vendor 目录存在的主因）

`wheels/` 现有 10 个：

```
PyMySQL-1.1.1-py3-none-any.whl                    # MySQL
pg8000-1.29.8-py3-none-any.whl                    # PostgreSQL 纯 Python 实现
dm_pure-1.3.5-py3-none-any.whl                    # 达梦纯 Python 实现
scramp-1.4.2-py3-none-any.whl                     # pg8000 依赖（SCRAM 认证）
asn1crypto-1.5.1-py2.py3-none-any.whl             # scramp 依赖
python_dateutil-2.9.0.post0-py2.py3-none-any.whl  # pg8000 依赖
six-1.17.0-py2.py3-none-any.whl                   # dateutil 依赖
importlib_metadata-4.13.0-py3-none-any.whl        # Python 3.7 补 importlib.metadata
zipp-3.15.0-py3-none-any.whl                      # importlib_metadata 依赖
typing_extensions-4.7.1-py3-none-any.whl          # 3.7 补 Protocol
```

覆盖了 SQLite、MySQL、PostgreSQL、达梦四种，**开箱即用**。

> **版本为什么锁在这里**：`pg8000 1.31+` 把依赖钉成 `scramp==1.5.0`，而 `scramp 1.5.0` 要求 Python ≥ 3.11；`python-dateutil 4.0.0` 同样要求 ≥ 3.11。为了兼容 3.8~3.13 同时兼顾 3.7，锁到 `pg8000 1.29.8 + scramp 1.4.2 + dateutil 2.9.0.post0 + PyMySQL 1.1.1`。

SQL Server 和 Oracle 的驱动进不了这个"通用 any"目录——它们**只发平台相关、且按 CPython 版本分开的二进制 wheel**，没有 `py3-none-any`：

| 驱动 | 最新版 | wheel 形态 | 意味着 |
| --- | --- | --- | --- |
| `pymssql` | 2.4.6 | `cp39`~`cp315` × {win_amd64, win_arm64, manylinux_x86_64, manylinux_aarch64, musllinux_*, macosx_arm64, macosx_x86_64} | 光一个 Python 版本就要 8 个平台文件，6 个版本合计 40+ |
| `oracledb` | 26.1.0 | `cp310`~`cp315` × {win32, win_amd64, win_arm64, manylinux2014_x86_64, manylinux2014_aarch64, musllinux_*, macosx_universal2} | 同上，且最低 cp310 |

要开箱支持这两个库，就得按"Python 版本 × 操作系统 × CPU 架构"备一整个矩阵，包体积从几百 KB 涨到数十 MB，而目标机的组合事先并不知道。

**正确做法**：目标机需要这两类库时，在**有网机器**上用**与目标机相同的 Python 版本**下载一次，把 whl 丢进 `wheels/`：

```bash
# 在有网机器上（示例：Python 3.10 / Windows）
python -m pip download -d wheels pymssql oracledb
```

然后重跑一次 `./install.sh`（vendor 不完整时会自动把新 whl 解包进去）；也可以直接 `pip install` 到系统解释器，程序会优先用它。

界面不会因为缺这两个库而无法启动，只会在对应数据源上提示"驱动缺失"。

---

## 5. 安全设计

这一节是"规避安全扫描"诉求的落点。

### 5.1 认证

| 项 | 实现 |
| --- | --- |
| 口令存储 | `sqlweb.db` 的 `users` 表只存 `salt` + `PBKDF2-HMAC-SHA256` 哈希，20 万次迭代；库文件权限 `0600`；每次落盘是一个事务（不会留半个文件） |
| 时序侧信道 | 用户名错也跑一次假 PBKDF2，账号是否存在无法通过响应时间判断 |
| 失败提示 | 用户名错与口令错返回**同一句话** |
| 会话 | 32 字节随机 token，`httpOnly` + `SameSite=Strict` Cookie；TTL 8 小时，闲置 30 分钟失效；进程内存表，重启全部失效 |
| 登录限速 | 按 **IP + 账号** 计次，1 分钟内失败 5 次即封 60 秒（HTTP 429）；同一出口 IP 的多人互不牵连 |
| 改密 | 需原口令；改密成功后只注销**该账号**的既有会话 |
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
- 再按分号切分（尊重引号、括号层级、**以及 `BEGIN … END` 程序块**）
- 每条语句取首关键字，白名单 `select / with / values / explain / show / table / declare / desc`

所以 `-- 注释\nSELECT ...` 不会被误杀，`INSERT INTO t VALUES('a;b')` 里的分号也不会误切。

**程序块不再被切碎**：`CREATE OR REPLACE PROCEDURE … AS BEGIN … END;` 里的每个分号都是过程体内部的，整段作为一条语句提交（`END IF` / `END LOOP` / `END CASE` 算块内闭合，不算语句结束；单独的 `BEGIN;` 事务不会被当成块）。这是 common-1.3.0 修的：以前按分号硬切会把过程拆成「……AS BEGIN NULL」+「END」，服务端必然报语法错误，看起来就是“这种存储过程建不了”。

按数据源可单独设 `"readonly": false` 放开写权限。**在只读数据源上执行 `CREATE PROCEDURE` / `CREATE TABLE` 会被入口拦下**，提示里直接指出「去数据源管理把它改成可写」。

### 5.4 多账号与可见范围（common-1.1.0 新增）

**账号**：`sqlweb.db` 的 `users` 表存多个账号，各自独立 salt / hash / 迭代次数。

旧版单用户 `auth.json` 首次启动时自动导入状态库并迁成 `users[0]`（管理员，全部数据源可见），**旧口令继续可用**，下一次写盘时固化为 v2，不需要重新初始化。

| 角色 | 能力 |
| --- | --- |
| `admin` 管理员 | 看全部数据源；可管账号、管数据源、改配置 |
| `user` 普通账号 | 只能看被授权的那几个数据源；其它源界面里不出现，直接调接口返回 403 |

账号管理入口：登录后右上角 **账号** → 新增 / 编辑 / 重设口令 / 删除。

- 用户名唯一，新建时口令至少 8 位
- 禁止删掉最后一个管理员，也不能删自己
- 改某账号的口令或授权范围，只注销**该账号**的既有会话；其他人正在用的会话不受影响
- 普通账号看不到"数据源管理"和"账号"两个入口

**可见模式（只显示选择的那几个库/模式）**：在 **数据源管理 → 编辑 → 可见模式** 填逗号分隔的白名单，例如 `MUAPP,MUCONFIG`。
对象树、`/api/schemas`、`/api/tables`、`/api/columns` 以及执行 SQL 时传的 `schema` 参数，全部按这张白名单校验。

- 留空 = 全部可见（保持旧行为）
- 大小写不敏感，`muapp,muconfig` 与 `MUAPP,MUCONFIG` 等价
- 达梦里"库"就是 schema（即用户名），所以填 `MUAPP,MUCONFIG` 就能把 `SYS / SYSAUDITOR / SYSDBA / SYSSSO / EALDWD / EALEPM / EALODS` 一起藏掉
- 白名单外的 schema 不是"藏起来但能绕"：带 `schema` 参数读表或执行 SQL 会直接 403
- **各家"模式"的含义不一样，别填错层**：PostgreSQL 的库（database）和模式（schema）是两层，白名单要填 schema（常见 `public`），把库名填进去会一个都匹配不上；MySQL 没有独立 schema，填的就是库名；Oracle 填用户名；SQL Server 填 `dbo` 这类 schema；达梦填用户名或 `CREATE SCHEMA` 建的独立模式。数据源表单里的说明会随所选驱动自动切换
- **白名单零命中时不再只显示"没有可显示的模式"**：`/api/overview` 会额外回 `white_missing`（配了但库里没有的名字）与 `schemas_hint`（该库真实存在的模式，最多 12 个），对象树直接把这两条摊开讲，并提示去数据源管理改哪个字段

**并发**：每次请求独立建连接、用完即关；服务端多线程处理请求；状态库的写操作（增删改数据源 / Redis 实例 / 账号）
用一把进程内锁串行化。所以多人同时查询互不干扰，有人同时在改数据源也不会读坏配置。

实测：8 线程 × 6 次、两个账号混跑同一数据源，48/48 成功，平均 11 ms，无串号。

### 5.5 Redis 面板与按账号授权（common-1.2.0 新增，common-1.3.0 并入数据源下拉）

Redis 有两个入口，**改的是同一份状态库 `sqlweb.db` 的 `redis` 表**；common-1.3.0 起顶栏不再单开 Redis 按钮，实例直接进数据源下拉的「Redis 缓存」组：

1. **新增数据源 → 数据库类型 = Redis**（common-1.3.0 起）：字段是连接方式（单机 / 哨兵）、主机、端口、哨兵节点、master 名称、库号 `db`、ACL 用户名、口令、只读，测试连接与保存直接落到 `redis` 段，实例也列在数据源管理列表里（带 `redis` 角标），可"编辑 / 删除"。ACL 用户名与口令**编辑时留空即沿用原值**。
2. **顶栏数据源下拉的「Redis 缓存」组**（选中某个实例即打开该实例面板，功能更全）：登录后，只有状态库里有实例、且该账号被授权时这一组才出现。

| 区域 | 内容 |
| --- | --- |
| 实例下拉 | 多实例切换；标签后带 `127.0.0.1:6379 / db0 · 只读` 这类摘要 |
| 概览 | 版本、角色 / 模式（master / standalone）、已连接客户端、运行天数、已用 / 峰值 / maxmemory、dbN 的 keys 与过期数、DBSIZE、命中 / 未命中、过期 / 淘汰、总命令数、maxmemory 策略 |
| 浏览 | `SCAN` 匹配（默认 `*`）+ 每批条数（≤500），列出 key / 类型 / TTL（`常驻` 或秒数），超过一批可"继续"翻页 |
| key 详情 | string 超 4 KB 截断；hash / zset 二维列表；list 带下标；set / stream 等给类型说明 |
| 命令执行 | 单行命令（`GET user:1`、`HGETALL cfg:app`），返回 JSON 与耗时；`readonly` 实例只放行读命令白名单（GET / TYPE / TTL / SCAN / HGETALL / LRANGE / SMEMBERS / ZRANGE / INFO 等），写命令直接拒绝并给出改法 |
| 实例配置（仅管理员） | 新增 / 编辑 / 删除实例，**改完立即生效**，不用重启 |

**授权**：管理员在 **账号 → 编辑 → 可见 Redis** 勾选该账号能看哪些实例；不勾=该账号的数据源下拉里没有「Redis 缓存」组，直接调 `/api/redis/*` 返回 403。

- 管理员默认看全部实例，不受勾选限制
- 每个实例独立 `readonly` 开关；写入需求要显式关掉（界面上就是那个"只读"勾选框）
- `password` / `username` 只存在服务端状态库，`/api/config` 返回给前端的实例信息不含口令

**接口**：`GET /api/redis/overview|scan|key|info`、`POST /api/redis/run`（以上 5 个为查看与执行），加上新增的 `POST /api/redis/test|save|delete`（仅管理员，在配置写锁内执行）。

#### 5.5.1 Redis 哨兵（Sentinel）集群（common-1.3.0 新增）

「连接方式」多一项 `哨兵 Sentinel`：不再填一台固定的地址，而是填**哨兵节点清单 + master 名称**，真正连哪台 master 每次由哨兵现给，主从切换后自动跟上。

| 字段 | 说明 | 一套三节点哨兵集群的填法 |
| --- | --- | --- |
| 连接方式 | `单机 / 直连` 或 `哨兵 Sentinel` | 哨兵 Sentinel |
| 哨兵节点 | 每行一个 `host:port`（也支持逗号 / 分号分隔、带 `redis://` 前缀、省略端口默认 26379） | `10.8.18.27:26379`、`10.8.18.28:26379`、`10.8.18.29:26379` |
| master 名称 | 哨兵监控的 master 名，即 `SENTINEL get-master-addr-by-name` 的参数 | `mymaster` |
| 库号 db | 数据节点上的逻辑库 | `0` |
| 口令 | 数据节点口令；哨兵若开了 `requirepass` 用同一个（实现上先免密问，回 `NOAUTH` 再带口令重问一次） | `zSAN#@34` |
| 只读 | 沿用原语义，只放行读命令白名单 | 建议只读 |

连接顺序：**并发**向所有哨兵问 `get-master-addr-by-name`（并发是为了不让不通的节点叠加超时——3 个哨兵串行各 3 s 是 9 s，并发只要 ≈3 s）→ 拿到的地址先 `ROLE` 校验必须是 master（故障转移中间态若指向从库会直接报错让你重试，不会把从库当 master 用）→ 连 master 再 `AUTH` / `SELECT db`。解析结果按实例缓存 5 秒，翻 key 列表时不会每页都去问哨兵。

- `POST /api/redis/test` 的返回多两个字段：`mode`（`standalone` / `sentinel`）、`target`（哨兵解析到的 `host:port`）；失败时把**每个哨兵的探测结果**放进错误文案，不用去翻日志
- `/api/config` 返回的实例信息里带 `mode` / `sentinels` / `master_name`，但**仍然不含口令**
- 顶栏数据源下拉与实例下拉在哨兵模式下显示 `哨兵 mymaster · db0`，不显示机器地址（地址是动态的）
- 哨兵模式不填主机/端口，表单会自动隐藏这两项；保存时 `host/port` 落成第一个哨兵地址 + 26379，只用于列表展示，**不参与连接**
- 本机（开发机）到 `10.8.18.0/24` 生产网段 TCP 全超时，所以本地只用假哨兵验证解析链路（`过程文件\_redis_sentinel_test.py`，自起最小 RESP 服务端覆盖 7 组用例）；真实连通性需在能进该网段的机器上点"测试连接"

### 5.6 左侧对象树按表名搜索（common-1.3.0 新增）

对象树标题下多了一个输入框，两种模式自动切换：

| 触发 | 行为 |
| --- | --- |
| 输入 1 个字符 | 只在**已经展开、已经加载过的表**里本地过滤，即时出结果（不连库） |
| 停顿 350 ms（≥2 字符）或按回车 / 点"搜" | 调 `GET /api/search_tables`，跨当前数据源的**全部可见模式**搜表名，结果按 `schema.table` 扁平列出，并在顶部标出扫了几个模式、命中几张、耗时多少 |
| `Esc` / 清空输入框 | 退出搜索结果，回到原来的树 |

- 搜索**走同一张 `visible_schemas` 白名单**：白名单外的模式连"扫"都不扫，所以没权限的对象既不会出现在树里，也不会被搜索暴露
- 命中结果点一下 → 右侧出列结构；双击 → 直接生成 `SELECT * FROM schema.table LIMIT 100` 并执行
- 每次搜索最多 8 秒、最多 1000 条（默认 300 条，超出即标"已截断"并提前返回），不会因为某个模式卡住把页面拖死
- 换数据源、换库、点"刷新"都会自动清空搜索词，避免拿旧源的关键字去新源搜

### 5.7 对象树：视图 / 存储过程 / 函数 / 序列 / 触发器（common-1.3.0 新增）

展开一个 schema，后端只发**一个**请求 `GET /api/objects?db=…&schema=…`，一次把六类对象按组返回：

```
main ▾ 2
 ├ 表 ▾ 2        t_order / t_user
 ├ 视图 ▾ 1      v_user
 ├ 存储过程 ▾ n   …
 ├ 函数 ▾ n       …
 ├ 序列 ▾ n       …
 └ 触发器 ▾ n     …
```

- **空组不渲染**：SQLite 没有存储过程/函数，就只出现"表"和"视图"两组
- **组默认收起**：展开一个 schema 只看到「表 462 / 视图 38 / 存储过程 40 …」这几行，点组标题才列出对象——大库才不会一展开就铺满几百行；组标题可点开/收起，状态随会话记着，换库或刷新树时重置；搜索框有关键字时组强制展开，免得命中项被折叠挡住
- 对象名的单击 / 双击 / 右击各有不同动作，完整矩阵见 **5.10**（双击看结构或语句、右击查询 / 生成 INSERT / 执行过程与函数）
- 顶部"过滤表名…"同时过滤**所有类型**的对象名；跨模式搜索仍走 `/api/search_tables`（只搜表）
- 各驱动的对象来源与定义来源：

| 驱动 | 对象来源 | 定义来源 | 备注 |
| --- | --- | --- | --- |
| PostgreSQL | `pg_class`（r/p/f/v/m/S）+ `pg_proc`（prokind 区分过程/函数）+ `pg_trigger`（`NOT tgisinternal`，排掉外键自动生成的内部触发器） | `pg_get_functiondef`（过程同样适用）/ `pg_get_viewdef` / `pg_get_triggerdef` / `pg_sequence` 读序列参数 | PG 10 无 `prokind`，自动退回"全部归函数"；物化视图按 `relkind='m'` 输出 `CREATE MATERIALIZED VIEW`（不是 `CREATE VIEW`）；PG 9.x 无 `pg_sequence` 视图，序列定义退为空串 |
| MySQL / MariaDB | `information_schema.TABLES` + `ROUTINES` + `TRIGGERS` | `ROUTINES.ROUTINE_DEFINITION` / `SHOW CREATE VIEW` / `SHOW CREATE TRIGGER` | 过程体需要账号有 `SHOW ROUTINE`/相应权限，否则只能拿到名字 |
| SQL Server | `sys.objects`（U/V/P/FN/IF/TF/SO/TR/SV） | `sys.sql_modules.definition` | |
| Oracle | `ALL_OBJECTS`（失败退回 `ALL_TABLES`+`ALL_VIEWS`） | 视图 `ALL_VIEWS.TEXT`；过程/函数/触发器 `ALL_SOURCE` | |
| 达梦 DM8 | 同 Oracle（`ALL_OBJECTS` / `ALL_SOURCE` 兼容视图） | 同上 | 低版本若 `ALL_OBJECTS` 不可用，会退回只列表与视图 |
| SQLite | `sqlite_master`（table/view） | `sqlite_master.sql` | 没有过程/函数/序列/触发器，组自然为空 |

- 新增接口（都走与表/列相同的 `visible_schemas` 白名单，越界返回 403）：

| 接口 | 作用 |
| --- | --- |
| `GET /api/objects?schema=` | 一次返回 `{table:[],view:[],procedure:[],function:[],sequence:[],trigger:[]}`；`&refresh=1` 绕过缓存 |
| `GET /api/source?schema=&name=&kind=` | 返回该对象的定义文本（`data` 为空串表示库里取不到，不是错误） |
| `GET /api/catalog?schema=` | 表 + 列的类型字典，供编辑器补全，缓存 300 s |

`GET /api/tables`、`/api/columns` 保留不变（老前端与脚本仍可用）。

**真机实测记录（PostgreSQL 18.6，本机 127.0.0.1:5432 / 库 `sqlweb_objtest` / schema `app`）**

在真库上建齐对象后逐项点验，结果是"六组齐、定义全有值"：

| 对象 | 库里实际对象 | 树里分组 | 点开看到的定义首行 |
| --- | --- | --- | --- |
| 表 | `t_user` / `t_order` | 表 2 | 表走"看列结构"（单击出列，双击出 `SELECT … LIMIT 100`） |
| 视图 | `v_user` / 物化视图 `mv_dept` | 视图 2 | `CREATE OR REPLACE VIEW "app"."v_user" AS …` / `CREATE MATERIALIZED VIEW "app"."mv_dept" AS …` |
| 存储过程 | `proc_bump(int)`（PG11+ `CREATE PROCEDURE`） | 存储过程 1 | `CREATE OR REPLACE PROCEDURE app.proc_bump(IN p_id integer)` |
| 函数 | `fn_grade(int)` / 触发器函数 `trg_touch()` | 函数 2 | `CREATE OR REPLACE FUNCTION app.fn_grade(a integer)` |
| 序列 | `seq_demo` + serial 隐式 `t_user_id_seq` / `t_order_id_seq` | 序列 3 | `CREATE SEQUENCE "app"."seq_demo"` |
| 触发器 | `trg_user_bi BEFORE INSERT ON app.t_user` | 触发器 1 | `CREATE TRIGGER trg_user_bi BEFORE INSERT ON app.t_user FOR EACH ROW EXECUTE FUNCTION app.trg_touch();` |

实测中修掉的三个 PG 缺陷（都在 `drivers.py`）：

1. **触发器不列出**——原实现只查 `pg_class` + `pg_proc`，触发器在 `pg_trigger` 上，组永远是空的；现按 `tgrelid` 关联所属表并过滤 `tgisinternal`。
2. **物化视图定义前缀错**——`relkind='m'` 的对象被当成普通视图输出 `CREATE OR REPLACE VIEW`，现按 `relkind` 区分。
3. **序列没有定义**——现读 `pg_sequence` 的 `seqstart/seqincrement/seqmin/seqmax/seqcache/seqcycle` 拼 `CREATE SEQUENCE`。

顺带修了前端一处显示错位：顶栏"源 + 库"合一下拉在首屏会停在整实例库清单的第一项（如 `HIK_APP`），实际执行的却是数据源自己配的库；现在按 `dbname` 对齐，显示的就是真实连的库。

HTTP 层同时核对：`/api/schemas` 只回白名单里的 `app`，`/api/objects` 六组齐全，六个 `/api/source` 全部有值，`/api/catalog` 4 张表带列，白名单外的 `/api/source?schema=public` 返回 403「模式「public」不在该数据源开放范围内」。

### 5.8 SQL 编辑器：语法高亮 + 自动联想（common-1.3.0 新增）

**语法高亮**：顶层是一个 `textarea`（文字透明、只留光标和选区），底下叠一层同字体同内边距的 `<pre>` 负责上色，滚动位置双向同步。所以：

- 不引任何第三方编辑器，包体积不变，断网也能用
- 中文注释、字符串、`--` 行注释、`/* */` 块注释都按 token 分色；关键字蓝、字符串绿、数字橙、注释灰
- Tab 键插入两空格（不会跳焦点），`Ctrl+Enter` 执行不变

**自动联想**：输入时按当前词和上下文给候选，最多 14 条，`↑ ↓` 选择、`Tab`/`Enter` 落地、`Esc` 关闭：

| 你打的 | 候选来源 |
| --- | --- |
| `sel` | SQL 关键字（`select` …） |
| `t_user` 之后、`FROM t_user` 只涉及一张表 | 该表的列名 |
| 别名或表名加点，如 `t.`（`FROM t_user t`） | 该表全部列名（别名会按 `FROM/JOIN/UPDATE/INTO` 自动解析） |
| 无关上下文 | 当前 schema 的表 / 视图 / 过程 / 函数名 |

补全数据来自 `/api/catalog`（一次批量取表+列，前端按 schema 缓存）；展开某个 schema 时会顺带预热，敲键盘时基本零等待。

### 5.9 性能：连接复用、元数据缓存、响应压缩（common-1.3.0 新增）

对象树是"点一下查一次"的交互，原来每次点击都要**新建连接 → 查系统表 → 关连接**。1.3.0 做了三件很轻的事：

| 机制 | 行为 | 影响面 |
| --- | --- | --- |
| 元数据 TTL 缓存 | `/api/objects`、`/api/source`、`/api/catalog`、`/api/tables` 按"数据源 + 库 + schema"缓存 120 s（字典 300 s）；`&refresh=1` 强制重查 | 反复展开同一 schema 不再打库；改完对象想立刻看到，加 `refresh=1` 或重启 |
| 连接复用 | 每个数据源保留最多 2 条空闲连接，下次请求先 `SELECT 1`（Oracle/达梦用 `SELECT 1 FROM DUAL`）验活，失败就丢弃重连；SQLite 因 `check_same_thread` 不复用 | 省掉 TCP 握手 + 登录鉴权；连接异常自动降级为新建，不会把坏连接传下去 |
| 响应压缩 | 任何 `/api/*` JSON 超过 2 KB 且客户端支持时用 gzip（**发的是真 gzip 容器，头 `1f 8b`**；早期写成 zlib 容器会触发浏览器 `ERR_CONTENT_DECODING_FAILED`，见 11 节排障） | 补全字典 / 对象树这类响应通常压到 1/5 以内 |

改数据源（保存 / 删除 / 设默认）时缓存与空闲连接一起清掉，避免换源后还看到旧 schema。

### 5.10 对象树交互：单击 / 双击 / 右击（common-1.3.0 新增）

左侧树不再是"点一下看列"，按对象类型给出三套动作：

| 对象 | 单击 | 双击 | 右击 |
| --- | --- | --- | --- |
| 表 | 右侧出列结构（列名/类型/可空/默认值/键，带「查看建表语句」） | 列结构 + 建表语句弹窗（DDL 一键复制） | 查询（前 100 行）/ 生成 INSERT 语句 / 查看建表语句 / 复制表名 |
| 视图 | 弹窗出视图定义 | 同左 | 查询（前 100 行）/ 查看视图语句 / 复制名称 |
| 存储过程 | 弹窗出过程语句 | 同左 | 执行（填参数后运行）/ 查看过程语句 / 复制名称 |
| 函数 | 弹窗出函数语句 | 同左 | 执行（填参数后运行）/ 查看函数语句 / 复制名称 |
| 序列 / 触发器 | 弹窗出定义 | 同左 | 查看定义 / 复制名称 |
| 分组标题（表/视图/…） | 展开、收起（**默认收起**） | — | 无菜单（点标题本身即可） |
| 模式（schema） | 展开、收起 | — | 刷新该模式（强制绕过缓存）/ 复制模式名 |

- **查询（前 100 行）**：右击表或视图点这条，先把 `SELECT * FROM "schema"."表" LIMIT 100` **写进 SQL 编辑器并同步高亮**，再立刻执行，所以「左边点了查询 → 右边既能看到语句、也能看到结果」。编辑器是"透明 textarea + 高亮层叠字"的实现，程序里改 `value` 必须同时刷高亮层（`syncHL()`），否则会看起来一片空白——清空按钮与生成 INSERT 走的是同一套 `setEditorSql()`。
- **生成 INSERT**：取该表 1 行真实数据拼 `INSERT INTO "schema"."table" (...) VALUES (...)`，只填进编辑器、**不执行**；空表用 `?` 占位并在状态栏写明"空表，值用 ? 占位"。字符串走驱动方言的 `quote_literal`（`O'Brien` → `'O''Brien'`），纯数字不加引号，`NULL` 不加引号。
- **执行过程 / 函数**：弹窗按参数清单生成"参数名 + 类型 · IN/OUT + 输入框"，下方实时预览将执行的语句；`OUT` 参数置灰（值按 `NULL` 传，并在返回里给一条 note）；结果区复用的表格渲染，有结果集（`SELECT ... FROM DUAL` / `CALL` 里带 `SELECT`）就出列，没有就提示"该对象执行完成，没有返回结果集"。
- **只读数据源**：执行被拦下（`stage=readonly`），但仍然把可复制的 `CALL ...` 语句回给前端 —— 只读环境里也能拿到一条准确语句去 DBA 那边跑。
- 右击菜单贴鼠标出现，超出视口自动翻边；点空白、Esc、窗口 resize 都会收起。

**六种驱动是同一套行为（分组默认收起 / 右击查询同时写进编辑器 / 组标题无右键菜单）**，差异只在「库里实际有什么对象」和「取前 N 行的方言」——空组不渲染，所以 SQLite 只有两组、达梦某库可能只有三组，这不是漏做：

| 驱动 | 点验环境 | 展开 schema 看到的分组（全部默认收起） | 右击「查询」写进编辑器的语句 | 实测结果 |
| --- | --- | --- | --- | --- |
| PostgreSQL | 172.168.9.164:4321 / `HC_APP`（462 表） | 表 462 / 视图 38 / 存储过程 40 / 函数 2 / 序列 4 | `SELECT * FROM "public"."act_evt_log" LIMIT 100;` | 0 行 · 49 ms |
| 达梦 DM8（DBA 账号） | 172.168.3.122:5236 / `SQLWEB_DMTEST` | 表 2 / 视图 1 / 存储过程 2 / 函数 1 / 序列 1 / 触发器 1 | `SELECT * FROM "SQLWEB_DMTEST"."T_USER" LIMIT 100;` | 1 行 · 42 ms |
| 达梦 DM8（业务账号） | 172.168.3.122:5236 / `EALEPM`（345 表） | 表 345 / 视图 23 / 存储过程 5 | `SELECT * FROM "EALEPM"."ACT_WF_GROUP_MULTI" LIMIT 100;` | 0 行 · 23 ms |
| MySQL / Doris | 10.189.5.86:29992 / `dm_fin_guanbao`（1735 表 + 52 视图） | 表 1735 / 视图 52 | ``SELECT * FROM `dm_fin_guanbao`.`dim_account` LIMIT 100;`` | 100 行 · 379 ms |
| SQLite | 包内 `demo.sqlite` / `main` | 表 2 / 视图 1 | `SELECT * FROM "main"."t_order" LIMIT 100;` | 1 行 · 2 ms |
| Oracle 19c EE | 172.168.7.31:1521 / `ORCL`，业务库账号 `SGHB_8089` | 表 1491 / 视图 32 / 存储过程 133 / 函数 2 / 序列 2 | `SELECT * FROM "SGHB_8089"."ACT_EVT_LOG" WHERE ROWNUM <= 100;` | 0 行 · 161 ms |
| Oracle 19c EE | 同上，中央库账号 `SGCONFIG_8089`（只有表） | 表 75 | `SELECT * FROM "SGCONFIG_8089"."BIZ_REMOTE_DEFINE" WHERE ROWNUM <= 100;` | 0 行 · 138 ms |
| SQL Server 2019 | 172.168.6.32:1433 / `JG_YS001`（业务库，`dbo`） | 表 740 / 视图 115 / 存储过程 439 / 函数 6 | `SELECT TOP 100 * FROM [dbo].[ACT_EVT_LOG];` | 0 行 · 74 ms |

> 方言校验脚本：`过程文件\_dialect_check.py`，从 `index.html` 里抠出 `quoteIdent` / `selectAllSql` 在 node 里对六种驱动各生成一条，用来复核 Oracle / SQL Server 的 `ROWNUM` / `TOP` 与三种标识符引号（`"`、`` ` ``、`[]`）。
>
> 现场拿到实例后补测到的两个雷：① SQL Server 2019 上 `sa` 能登实例却进不了指定的库时，`pymssql` 报的是 `18456 登录失败` 而不是「库不存在」，实际库里并没有那个名字（`USE [JG_CONF]` 直接 `911 Database does not exist`）——遇到登录失败先用 `master` 库登进去 `SELECT name FROM sys.databases` 核对库名；② Oracle thin 模式连 19c 不需要装客户端，`oracledb` 直连即通，两个账号各自是一个 schema，按账号建两个数据源即可（`dbname` 填服务名 `ORCL`，`visible_schemas` 填账号名）。

**参数探测（六驱动）**：`drivers.proc_args(conn, schema, name, kind)`

| 驱动 | 来源 | 关键处理 |
| --- | --- | --- |
| Oracle / 达梦 | `ALL_ARGUMENTS`（`PACKAGE_NAME IS NULL`，按 `SEQUENCE` 排） | 函数返回值混在参数里（达梦 `ARGUMENT_NAME='V_RET'`、Oracle 名为空），必须剔除，否则调用凭空多一个参数；达梦 `POSITION` 从 0 起，排序不能用它 |
| PostgreSQL | `pg_proc` + `proargnames` / `proargmodes` / `COALESCE(proallargtypes, proargtypes)` + `format_type` | psycopg2 给 list、pg8000 给 `{a,b}` 文本，两种都接住 |
| MySQL / MariaDB | `information_schema.PARAMETERS` | 过程/函数共用 |
| SQL Server | `sys.parameters` + `TYPE_NAME` | 过程/函数共用 |
| SQLite | — | 返回空 |

**语句生成**：`sqlweb.proc_call_sql(driver, schema, name, kind, args)` —— 参数**内联成字面量**而不是占位符（达梦 `?`、psycopg2 `%s`、pymysql `%s`、oracledb `:1` 各不相同，统一占位符反而处处踩坑）。函数在达梦/Oracle 用 `SELECT "S"."F"(...) AS "RETURN_VALUE" FROM DUAL`，SQL Server 用 `EXEC`，其余 `CALL`。

**接口**（与表/列同一套 `visible_schemas` 白名单，越界 403）：

| 接口 | 作用 |
| --- | --- |
| `GET /api/proc_args?schema=&name=&kind=` | 参数清单 `[{name, mode, type, default}]`，缓存 60 s，`&refresh=1` 重查 |
| `POST /api/proc` | 体 `{db, schema, name, kind, args:[{name, mode, value}], max_rows}`；返回 `{ok, call_sql, columns, rows, affected, notes, elapsed_ms}`，只读源返回 `stage=readonly` 并附 `call_sql` |

**达梦真机实测（DM8 8.4 企业版，172.168.3.122:5236，schema `SQLWEB_DMTEST`）**

| 对象 | 参数探测 | 生成的语句 | 执行结果 |
| --- | --- | --- | --- |
| `P_NOOP(MSG VARCHAR(100))` | `[{name:'MSG', mode:'IN', type:'VARCHAR(100)'}]` | `CALL "SQLWEB_DMTEST"."P_NOOP"('hello')` | 成功，无结果集，约 40 ms |
| `F_ADD(A INT, B INT)` | `[A INT, B INT]`（`V_RET` 已剔除） | `SELECT "SQLWEB_DMTEST"."F_ADD"(1, 2) AS "RETURN_VALUE" FROM DUAL` | 返回 `[[3]]` |
| `P_ROWS`（无参，内部 `SELECT`） | `[]`（弹窗提示"该对象没有入参，直接执行即可"） | `CALL "SQLWEB_DMTEST"."P_ROWS"()` | 返回 `K/V` 各一列、`A/1` 一行 |

达梦另有两处与 Oracle 的差异已在这版里吃掉：

1. **schema 列表要合并两个来源**。达梦的 `CREATE SCHEMA` 只建模式对象、不建用户，`DBA_USERS` / `ALL_USERS` 里根本没有它 —— 只查用户表会让"独立模式"整块从树上消失。现在三源合并（`DBA_USERS` + `ALL_USERS` + `SYS.SYSOBJECTS WHERE TYPE$='SCH'`）并去重，系统模式（`SYS` / `SYSDBA` / `SYSAUDITOR` / `SYSSSO` / `CTISYS` / `SYSJOB` / `GUEST`）排在业务模式之后。
2. **业务账号的字典权限边界**：`EALEPM` 这类业务账号只有 `CREATE SESSION` / `RESOURCE`，读任何 `SYS.*` 一律 `-5504`，只能用 `ALL_*` 兼容视图；`TABLEDEF()` 与 `DBMS_METADATA.GET_DDL()` 在其下返回空（表 DDL 是本工具按字典拼的）；`ALL_TAB_COLUMNS` 看不到自增列，要看 `SYS.SYSCOLUMNS.INFO2`（要 DBA）；`ALL_TABLES.NUM_ROWS` 全 NULL。用一个 DBA 账号建数据源，自增列、列注释、行数才齐全。
3. **不再执行 `SET SCHEMA`**（这条曾经让每条达梦查询白等 10 秒）。包内 `wheels/` 带的纯 Python 驱动 dm-pure（`dmpy`）执行 `SET SCHEMA` / `ALTER SESSION SET CURRENT_SCHEMA` 时，达梦对"切换成功"的响应 dmpy 解析不了，会一直读到 socket 超时才抛 `TimeoutError`，而 `_use_schema` 又把异常吞掉 —— 于是前端看到的每条查询都耗时 `10044 ms`、而且 schema 根本没切过去。现在达梦直接跳过这句：对象树生成的语句本来就带 `"SCHEMA"."对象"` 全限定名，手写 SQL 时也请用全限定名。实测同一条 `SELECT * FROM "SQLWEB_DMTEST"."T_USER" LIMIT 100` 由 10044 ms 降到 42 ms，`EALEPM` 大库由 10044 ms 降到 23 ms。

> **为什么业务库里建不了存储过程**：`CREATE OR REPLACE PROCEDURE EALEPM.PROC_x(...)` 在 `EALEPM` 账号下报错，不是工具拦的，是账号没有 `CREATE PROCEDURE` 权限（该账号只有 `CREATE SESSION` / `RESOURCE`）。要么让 DBA 执行 `GRANT CREATE PROCEDURE TO EALEPM;`，要么用 `SYSDBA` 建完再 `GRANT EXECUTE` 给业务账号。工具侧对"能建的账号"已经实测通过（`SQLWEB_DMTEST` 下的过程/函数都是由本工具执行 DDL 建的）。

---

## 6. 配置

### 6.1 界面配置（推荐）

登录后点右上角 **数据源**：

1. **新增数据源** → 选类型（下拉框里直接标出"可用/驱动缺失"）
2. 填主机、端口、账号、口令、库名
3. **先点"测试连接"**：失败会把驱动原始报错原样显示（例如 `RuntimeError: SQLite 文件不存在：/nope/x.db`），改好后再保存
4. **保存数据源** → 写进状态库 `sqlweb.db` 并热加载，**不需要重启**
5. 可"设默认 / 编辑 / 删除"

> 首次部署时状态库里的 `datasources` 是**空的**，登录后界面会直接进入"无数据源"引导态，点一下就能加第一台库。
> 编辑已有数据源时，口令留空表示**沿用原口令**，不必重新输入。

**加账号**（仅管理员）：右上角 **账号** → **新增账号** → 填用户名、口令，选角色，勾上这个账号能看的数据源 → 保存。

**加 Redis**（仅管理员，两种方式等价）：**新增数据源** → 数据库类型选 **Redis** → 填主机 / 端口 / 库号 / ACL 用户名 /（口令）→ 测试连接 → 保存。也可在顶栏下拉里选中该实例后，点面板里的「新增实例」，改的是同一份配置。
普通账号登录后，顶栏数据源下拉里只会出现被授权的源。

**只显示选中的库/模式**：在数据源表单的 **可见模式** 里填逗号分隔的白名单（留空=全部）。
对达梦这类"一个实例下几十个 schema"的场景，填 `MUAPP,MUCONFIG` 即可，对象树里就只剩这两项。

**加 Redis 实例**（仅管理员）：顶栏数据源下拉里选中任一 Redis 实例 → 滚到 **Redis 实例配置** → **新增实例** → 填名称、主机、端口、库号、ACL 用户名（Redis 6+ 才需要）、口令，决定是否只读 → 先 **测试连接**（返回版本、角色、dbsize、已用内存）→ **保存**，立即生效不重启。
口令留空表示沿用原口令；改主机端口和口令时都建议先测连再保存。

### 6.2 文件配置

**旧版 / 导入用的 JSON 格式**（`config.json`，由 `config.example.json` 生成，两者字段一致）。

> common-1.3.0 起运行状态存在 `sqlweb.db`（SQLite，见 6.3），这个 JSON 只在**首次启动时被导入一次**，之后不再参与读写。
> 下表的字段与状态库里的内容一一对应（`databases` → `datasources` 表、`redis` → `redis` 表、其余标量 → `kv` 表），所以字段含义照旧。

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
      "readonly": true,
      "visible_schemas": ["MUAPP", "MUCONFIG"]
    },
    {
      "key": "lite",
      "label": "本地 SQLite",
      "driver": "sqlite",
      "path": "/data/demo.db",
      "readonly": true
    }
  ],
  "redis": [
    {
      "key": "epm-cache",
      "label": "生产缓存",
      "host": "10.0.0.20",
      "port": 6379,
      "db": 0,
      "username": "",
      "password": "***",
      "readonly": true,
      "timeout": 5
    }
  ]
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
| `databases[].visible_schemas` | 可见模式白名单，数组或逗号分隔字符串均可；留空/不写=全部可见。大小写不敏感 |
| `redis` | Redis 实例数组，**为空时顶栏不出现 Redis 按钮**。字段：`key` / `label` / `host` / `port` / `db` / `username` / `password` / `readonly` / `timeout` |
| `redis[].db` | 库号（0-15），对应 `SELECT n` |
| `redis[].username` | Redis 6+ ACL 用户名，没有就留空（老版本只认口令） |
| `redis[].readonly` | 该实例只读：命令框只放行读命令白名单，写命令直接拒绝 |
| `redis[].timeout` | 连接与单条命令超时（秒），默认 5 |

### 6.3 状态存储：单文件 SQLite（common-1.3.0 起）

运行状态（数据源、Redis 实例、账号）不再分散在很多个 JSON 里，统一落在程序目录的
**`sqlweb.db`（单文件 SQLite）**：

| 表 | 内容 |
| --- | --- |
| `kv` | 标量配置：`title` / `host` / `port` / `base_path` / `readonly` / `max_rows` / `default_db` … |
| `datasources` | 每个数据源一行；`key` 主键，`payload` 存主机、端口、账号、口令、`visible_schemas` 等 |
| `redis` | 每个 Redis 实例一行（含 ACL 用户名与口令），只存在服务端 |
| `users` | 每个账号一行：`salt` + `hash`（PBKDF2-SHA256，20 万次）+ `role` + `allow_dbs` / `allow_redis` |
| `meta` | 版本与迁移记录 |

为什么换：JSON 是"读全量、改一处、写全量"，多人同时改只能靠进程内锁串行化，写入途中进程被杀容易留半个文件；而且账号、数据源、Redis 实例散在两个文件里，备份和回滚都不方便。换成一个 SQLite 文件后：

- **一次改动一个事务**：要么整份生效，要么完全不变，不会出现半截配置
- **备份只拷一个文件**：`cp sqlweb.db sqlweb.db.bak`（权限 `0600`，含口令，别外传）
- **可以直接查**：`sqlite3 sqlweb.db "select key,payload from datasources"`
- 多账号并发不用再靠"串行化一把锁兜住"的写法来保证正确性；查询本身依旧每次独立连接、用完即关

**首次启动的迁移**：库里的 `datasources` / `users` 都还是空的时候，若同目录存在旧的 `config.json` / `auth.json`，服务会自动导入并把旧文件改名成
`config.json.migrated-<时间戳>`、`auth.json.migrated-<时间戳>` 留档（只读旧文件，不删数据）。启动日志会打印：

```
已把旧配置迁入 SQLite：数据源 3 个、Redis 实例 1 个、账号 2 个
  旧文件已留档: /opt/.../config.json.migrated-20261112_192331
状态库: /opt/.../sqlweb.db（SQLite 单文件）
```

迁移只做一次：之后一律以 `sqlweb.db` 为准，再改旧 JSON 不会生效。

**改端口 / 子路径（base_path）**：两条路，都不用记 SQL。

```bash
python3 setconfig.py                      # 看当前生效值（会标出来源是状态库还是 JSON 种子）
python3 setconfig.py --set base_path=/sqlweb --set port=8080
python3 setconfig.py --get base_path      # 脚本里取值
./install.sh --port 8080 --base-path /sqlweb   # 等价写法，装完顺手重启
./stop.sh && ./install.sh                # 改完要重启才生效
```

> **不要再手改 `config.json`**：迁移完成后它已经不被读取（启动日志会打印「访问前缀: /sqlweb」，并对目录里新出现的 `config.json` 提示「该文件不再参与读写」）。这是升级后最容易踩的坑——按老习惯改了 `config.json` 重启，发现子路径 / 端口没变。

**回滚**：想退回旧版（JSON 版）时，把 `*.migrated-*` 改回 `config.json` / `auth.json`，并删掉 `sqlweb.db` 即可——迁移过程没有改动这两个文件的内容。


---

## 7. nginx 反向代理（子路径）

服务本身监听 `8899`，由 nginx 转出去：

```nginx
location /sqlweb/ {
    # 注意：不要写成 http://127.0.0.1:8899/（结尾带斜杠会把 /sqlweb 剥掉，服务端只有 /login，直接 404）
    proxy_pass http://127.0.0.1:8899;
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

同时把 `base_path` 设成 `/sqlweb`：

```bash
python3 setconfig.py --set base_path=/sqlweb
./stop.sh && ./install.sh
```

起来后看启动日志里的 `访问前缀: /sqlweb`——这一行是判断代理配置有没有生效最快的方法；显示「（根路径，无前缀）」就是没读到。

服务侧收到带前缀的路径后：剥掉前缀再路由、把 `window.__BASE__` 注入前端、Cookie 的 `Path` 自动带上前缀。**不设 `base_path` 时一切按根路径工作**，两种部署方式都不用改代码。

两条规则必须成对，配错任何一条都是 404：

| 服务端 `base_path` | nginx `proxy_pass` | 说明 |
| --- | --- | --- |
| `/sqlweb` | `http://127.0.0.1:8899`（**不带结尾斜杠**） | 原样把 `/sqlweb/...` 转给上游，服务端自己剥前缀 |
| 空（根路径） | `http://127.0.0.1:8899/`（带结尾斜杠） | nginx 剥掉 `/sqlweb` 再转，上游只看到 `/login` |

服务端还会拦一道：请求路径没带前缀时返回 404 并在页面上写明当前 `访问前缀`、正确地址以及 `proxy_pass` 该怎么写，不用再去翻 nginx 日志猜。

> 权限提醒：`/var/lib/nginx/proxy` 与 `client_body` 临时目录的属主必须与 nginx worker 用户一致，否则大结果集会 500 / 413 Permission denied。

---

## 8. 与麒麟版 v2.3 的差异

| 项 | 麒麟版 v2.3 | 本版 common |
| --- | --- | --- |
| 数据库 | PG + 达梦（driver 分派写死） | 6 种，独立适配层 |
| 驱动来源 | 只打包 cp37–cp312 的 Linux wheel | 优先纯 Python，跨平台，且随包 vendor |
| 登录 | 无 | 强制登录 + 验证码 + 限流 |
| 数据源 | 改 `config.json` 重启 | 界面增删改 + 测试连接 + 热加载 |
| 验证码 | 无 | 纯标准库 PNG |
| 启动预检 | 会去连默认库，连不上拒绝启动 | **不连默认库**，连接按需建立 |
| Redis 面板 | 有（`/api/redis/*` 5 个接口，前端面板） | 概览 / SCAN 浏览 / key 详情 / 命令执行，加界面增删改实例与按账号授权 |
| 前端 | 单库 + Redis 双面板 | 多数据源 + 对象树 + 数据源管理 + 改密 + 账号管理 |
| 账号 | 无 | 单用户 → 多账号（admin / user）+ 按账号分配数据源 + 并发互不影响 |

### 8.1 升级到 1.3.0（从 1.0.0 / 1.1.0 / 1.2.0 都可以直接升）

1. 停服务（`./stop.sh`，或 systemd `sudo systemctl stop sqlweb-offline`）
2. 备份整目录（程序目录 + `sqlweb.db`，旧版再顺手备份 `auth.json` / `config.json`）。**不要把新包解压成一个新目录直接跑**——包里刻意不放 `sqlweb.db` / `config.json` / `auth.json`，直接跑会显示"数据源: 0 个"并走首次初始化
3. 覆盖代码文件：`sqlweb.py`、`auth.py`、`index.html`、`README.md`（`login.html`、`captcha.py`、`drivers.py`、`redis_panel.py` 未变）
4. **不要删旧的 `auth.json` / `config.json`；也绝不要在已有 `sqlweb.db` 的目录里覆盖它们**：
   - `auth.json`（v1 单用户或 v2 多用户）在首次启动时整批导入 `sqlweb.db` 的 `users` 表，旧管理员口令继续可用；没有 Redis 授权的账号导入后 `allow_redis` 为空（=看不到 Redis 实例）
   - `config.json` 里的老数据源导入 `datasources` 表（缺 `visible_schemas` 的按"全部可见"处理）；原有 `redis` 段导入 `redis` 表，**升级后数据源下拉里的「Redis 缓存」组立即回来**
   - 1.3.0 没有新增配置字段，旧 `config.json` / `auth.json` 直接可用——导入后原文件被改名为 `*.migrated-<时间>`，内容不动，可随时回滚
5. 起服务（`./start.sh`，生产用 `sudo ./kylin.sh service install` 注册 `sqlweb-offline`）→ 用原管理员口令登录
6. 校验升级：右上角出现 **账号** 按钮；顶栏数据源下拉里出现「Redis 缓存」组；**数据源管理 → 新增数据源 → 数据库类型**下拉里出现 **Redis** 一项；对象树标题下出现搜索框
7. 之后在"数据源管理 → 编辑 → 可见模式"填 `MUAPP,MUCONFIG` 之类的白名单；在"账号"里给同事建普通账号并勾选可见数据源与可见 Redis

---

## 9. 目录结构

```
sqlweb_offline_common_common-1.3.0/
├── sqlweb.py              主程序：HTTP 路由、认证中间件、数据源 API、查询执行
├── drivers.py             多数据库适配层（6 个 Driver 类）
├── store.py               状态存储层（SQLite：数据源 / Redis 实例 / 账号都在 sqlweb.db 里）
├── setconfig.py           离线改配置（--set base_path=/sqlweb、--set port=8080、--get/--show）
├── auth.py                AuthStore / SessionManager / RateLimiter / CaptchaStore（多账号 v2）
├── captcha.py             纯标准库 PNG 验证码
├── redis_panel.py         Redis 面板（移植自 v2.3）
├── login.html             登录页（含首次初始化引导）
├── index.html             主界面（对象树 + 表名搜索 / SQL 编辑器 / 结果表 / 数据源管理（含 Redis）/ 改密 / 账号 / Redis 面板）
├── config.example.json    配置模板（databases 为空，首登后界面新增）
├── vendor/                纯 Python 驱动解包版（pg8000/pymysql/dmpy/scramp/…），运行时直接加载
├── wheels/                10 个纯 Python whl，留档与补齐用
├── install.sh             Linux/麒麟/macOS 安装 + 一键部署
├── kylin.sh               麒麟 / 信创入口（一键部署 + systemd 服务管理）
├── install.ps1            Windows 安装脚本
├── start.sh               Linux/macOS 启动
├── start.command          macOS 双击启动
├── start.bat              Windows 启动
├── stop.sh                停止后台服务
├── README.md              本文件
├── sqlweb.db              （运行后生成：数据源 / Redis 实例 / 账号，权限 600；备份拷它就够）
├── config.json            （可选种子：首启导入后改名 config.json.migrated-<时间>）
├── auth.json              （可选种子：首启导入后改名 auth.json.migrated-<时间>）
├── sqlweb.log             （后台启动时生成；上一轮为 sqlweb.log.prev）
└── .sqlweb.pid            （后台启动时的进程号）
```

---

## 10. 已知限制

- **SQL Server / Oracle 需要二进制 wheel**：包内未带，见 4.2；且在 Python 3.7 上不可用
- **`dmpy` 的事务语义不完整**：只读查数不受影响；需要事务的场景请用官方 `dmPython`
- **会话存内存**：重启服务要重新登录（对离线运维工具是有意为之）
- **仅 HTTP**：不做 TLS。对外暴露请走 nginx + HTTPS，或只绑 `127.0.0.1`
- **数据源口令明文存 `sqlweb.db`**（未加密，只靠文件权限）：这是离线内网工具的取舍。库文件权限已设 `0600`，但服务器被拿到 root 就等于丢口令。若有更高要求，可改为启动时用环境变量注入
- **跨库浏览是逐次重连**：PostgreSQL / SQL Server 一个连接只绑一个库，切库靠换 `dbname` 新建连接，切一次多一次建连开销（内网通常 < 0.2 s）。不要在一条 SQL 里跨库 JOIN —— 驱动层不支持，请分成两条查询
- **MySQL 的库就是 schema**：`information_schema.SCHEMATA` 返回的已经是全部库，所以对象树只显示一层，不再套库节点。Oracle / 达梦的"库"即用户，同理单层
- **`/etc/systemd/system` 需 root**：`kylin.sh service` 必须用 `sudo`；没有 sudo 的现场直接用 `./install.sh` 后台跑，功能一样
- **表名搜索是逐模式扫表清单**：命中率高的关键字快（十几毫秒），但数据源里模式特别多、每模式表特别多时会明显变慢。为此加了 8 秒硬上限与 1000 条上限，超时/超量只返回已扫到的部分并在界面标"已截断"——想要全量结果就缩小关键字
- **Redis 不是 SQL 数据源**：它不进对象树、不能用 SQL 编辑器查询。要看 key 就在顶栏数据源下拉里选那个 Redis 实例（1.3.0 起已并入数据源，不再单开按钮）

---

## 11. 排障

| 现象 | 处理 |
| --- | --- |
| 麒麟上 `./install.sh` 报 `需要 Python 3.8 及以上` | 那是旧包。本版门槛是 3.7，重新解压新包即可 |
| 麒麟上 `./install.sh` 或 `./kylin.sh` 报 `Permission denied` | 解包工具丢了执行位：改跑 `sh install.sh` / `sh kylin.sh`，或在解包后 `chmod +x *.sh` |
| 界面里六种库**全部**显示"驱动缺失" | 服务跑到了错误的解释器上（常见于 `.venv` 建了一半、或直接用系统 python3 而 vendor 被漏打）。先看启动日志里的 `Python: x.y.z`，确认 vendor 目录在包内，然后删掉 `.venv` 重跑 `./install.sh` |
| 界面里某库显示"驱动缺失" | vendor 在的话先看解释器版本（3.7 下 SQL Server / Oracle 本就不可用）；否则把对应 whl 放进 `wheels/` 后重跑 `./install.sh` |
| 登录页提示"验证码已失效" | 验证码 5 分钟过期且一次性，刷新图片重试 |
| 登录 429 | 同一 IP 1 分钟内失败 5 次被限速，等 60 秒 |
| 忘记管理员口令 | 停服后删掉 `sqlweb.db` 的 `users` 行（或整库），重启回到初始化模式并打印新令牌 |
| 手改了 SQL / 状态库但界面没变 | 界面与接口一律以 `sqlweb.db` 为准；用 `sqlite3` 直接改库要重启服务才生效。界面里改则不用 |
| nginx 反代 `/sqlweb/` 打不开（404 / 白屏 / 资源全丢） | 先看启动日志有没有 `访问前缀: /sqlweb`；没有就是 `base_path` 没读到——用 `python3 setconfig.py --set base_path=/sqlweb` 写状态库再重启，不要再改 `config.json`。另确认 `proxy_pass` 的斜杠跟模式配套：服务端 `base_path=/sqlweb` 时写 `http://127.0.0.1:8899`（不带斜杠），`base_path` 为空时才写 `http://127.0.0.1:8899/`。配反了必然 404，页面会提示当前前缀 |
| 执行查询只报 `Failed to fetch`，F12 里请求是 `200 (OK)` + `net::ERR_CONTENT_DECODING_FAILED` | 服务端压缩流格式不匹配：声明 `Content-Encoding: gzip` 却发了 zlib 容器（头 `78 9c`）。**只有响应 >2048 字节（触发压缩）时才出现，小查询完全正常**，所以常被误判成"SQL 或网络问题"。1.3.0 已修（改发真 gzip 容器，头 `1f 8b`）。老包热修：`sqlweb.py` 里把 `body = zlib.compress(body, 6)` 换成 `_c = zlib.compressobj(6, zlib.DEFLATED, 31); body = _c.compress(body) + _c.flush()`，重启服务即可；不想压缩就临时把 `len(body) > 2048` 改成 `False` |
| 点执行后状态栏一直是 `执行中…` 且连接被重置 | 查大字段（`text` 里存 XML/JSON，单行数 MB）时内存与传输压力大。先用 `select count(*), pg_size_pretty(pg_total_relation_size('<表>'))` 看体积，再给查询补 `LIMIT`；或调小 `max_rows` |
| `CREATE [OR REPLACE] PROCEDURE` / 触发器报语法错误、像是被截断 | 数据源是只读时先在【数据源管理】改成「可写」；仍报错就看错误里的语句片段——1.3.0 起程序块整段提交，若片段以 `END` 开头说明用了旧包，换新包即可 |
| 改了 `config.json` 但端口 / 子路径没变 | 1.3.0 起配置真源是 `sqlweb.db`，`config.json` 只在首次启动导入一次。用 `python3 setconfig.py --set ...` 或界面改；启动日志会对残留的 `config.json` 打出提示 |
| 达梦在 mac 上报 `ModuleNotFoundError: dmPython` | 装纯 Python 驱动：`pip install dm-pure`，服务会自动降级（3.7 上走 vendor 里的 `dmpy`） |
| Windows 下中文乱码 | 用 `chcp 65001` 或在 PowerShell 里跑；`start.bat` 已内置 |
| 端口被占、启动后立刻退出 | `./install.sh` 会打印日志尾部；先 `./stop.sh`，或换端口 `./install.sh --port 8898` |
| systemd 服务起不来 | `journalctl -u sqlweb-offline -n 50`；多半是端口占用或 `sqlweb.db` 内容有误（可用 `sqlite3 sqlweb.db .schema` 自查） |

---

## 12. 许可

内部运维工具，按现状提供。
