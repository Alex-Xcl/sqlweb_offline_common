# wheels —— 离线驱动包

本目录存放纯 Python 驱动 wheel，供 `install.sh --install-deps` / `install.ps1 -InstallDeps` 离线安装。

**Git 仓库里不包含这些 `.whl` 文件**（二进制不入库），克隆代码后需按下列任一方式补齐：

## 方式一：从 PyPI 下载（有外网的机器）

```bash
pip download -d wheels PyMySQL pg8000 dm-pure
```

> 注意：**不要加 `--no-deps`**。`pg8000` 并非零依赖，它需要 `python-dateutil`、`scramp`，
> `scramp` 又需要 `asn1crypto`，`python-dateutil` 需要 `tzdata`。少一个都会在离线安装时报
> `Could not find a version that satisfies the requirement python-dateutil>=2.8.2`。

## 方式二：从离线包拷贝

官方离线发行包 `sqlweb_offline_common_*.tar.gz` 里已内置下列 7 个文件，直接解压进本目录即可。

## 清单

| 文件 | 体积 | 用途 |
| --- | --- | --- |
| `pymysql-1.2.4-py3-none-any.whl` | 46 KB | MySQL / MariaDB 驱动 |
| `pg8000-1.31.6-py3-none-any.whl` | 57 KB | PostgreSQL 纯 Python 驱动 |
| `dm_pure-1.3.5-py3-none-any.whl` | 42 KB | 达梦 DM8 纯 Python 驱动（导入名 `dmpy`） |
| `scramp-1.5.0-py3-none-any.whl` | 20 KB | pg8000 的 SCRAM-SHA-256 依赖 |
| `asn1crypto-1.5.1-py2.py3-none-any.whl` | 105 KB | scramp 依赖 |
| `python_dateutil-4.0.0-py3-none-any.whl` | 93 KB | pg8000 依赖 |
| `tzdata-2026.6-py2.py3-none-any.whl` | 348 KB | dateutil 依赖 |

合计约 715 KB，全部 `py3-none-any`，**任何平台、任何 Python 3.8+ 通用**。

## 可选补充（需自行下载，含二进制，按平台）

| 数据库 | 包 | 说明 |
| --- | --- | --- |
| SQL Server | `pymssql` | 发 cp39+ 各平台 wheel |
| Oracle | `oracledb` | 发 cp310+ 各平台 wheel，thin 模式不需要 Oracle 客户端 |
| PostgreSQL | `psycopg2-binary` | 性能优于 pg8000，可选 |
| 达梦 | `dmPython` | 达梦安装介质里自带，仅 Linux/Windows |

把对应 wheel 丢进本目录，重跑一次安装脚本即可被自动识别。
