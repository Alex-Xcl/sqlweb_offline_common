# wheels —— 离线驱动包

本目录存放纯 Python 驱动 wheel，供 `install.sh --install-deps` / `install.ps1 -InstallDeps` 离线安装。

**Git 仓库里不包含这些 `.whl` 文件**（二进制不入库），克隆代码后需按下列任一方式补齐：

## 方式一：从 PyPI 下载（有外网的机器）

```bash
pip download -d wheels PyMySQL==1.1.1 pg8000==1.30.5 dm-pure==1.3.5 \
    python-dateutil==2.9.0.post0 scramp==1.4.6 six==1.17.0 asn1crypto==1.5.1
```

> **必须锁版本。** `pg8000` 从 1.31 起把依赖钉成 `scramp==1.5.0`，而 `scramp 1.5.0` 要求 Python ≥ 3.11；
> `python-dateutil 4.0.0` 也要求 ≥ 3.11。直接 `pip download pg8000` 拿到的是这两版，装到 3.8~3.10 会报
> `ERROR: Package 'scramp' requires a different Python`。上面这组的最低要求是 Python 3.8。
>
> 也不要加 `--no-deps`：`pg8000` 依赖 `python-dateutil` 与 `scramp`，`scramp` 依赖 `asn1crypto`，
> `python-dateutil` 依赖 `six`。少一个离线安装就会报 `Could not find a version ...`。

## 方式二：从离线包拷贝

官方离线发行包 `sqlweb_offline_common_*.tar.gz` / `.zip` 里已内置下列 7 个文件，直接解压进本目录即可。

## 清单

| 文件 | 体积 | 用途 |
| --- | --- | --- |
| `PyMySQL-1.1.1-py3-none-any.whl` | 44 KB | MySQL / MariaDB 驱动 |
| `pg8000-1.30.5-py3-none-any.whl` | 45 KB | PostgreSQL 纯 Python 驱动 |
| `dm_pure-1.3.5-py3-none-any.whl` | 42 KB | 达梦 DM8 纯 Python 驱动（导入名 `dmpy`） |
| `scramp-1.4.6-py3-none-any.whl` | 13 KB | pg8000 的 SCRAM-SHA-256 依赖 |
| `asn1crypto-1.5.1-py2.py3-none-any.whl` | 103 KB | scramp 依赖 |
| `python_dateutil-2.9.0.post0-py2.py3-none-any.whl` | 224 KB | pg8000 依赖 |
| `six-1.17.0-py2.py3-none-any.whl` | 11 KB | dateutil 依赖 |

合计约 470 KB，全部 `py3-none-any` / `py2.py3-none-any`，**Linux / Windows / macOS 通用，Python 3.8 ~ 3.13 可装**。

## 可选补充（需自行下载，含二进制，按平台）

| 数据库 | 包 | 说明 |
| --- | --- | --- |
| SQL Server | `pymssql` | 只发 `cp39`~`cp315` 的平台 wheel（win / manylinux / musllinux / macosx），没有 `any` |
| Oracle | `oracledb` | 只发 `cp310`~`cp315` 的平台 wheel，没有 `any`；thin 模式不需要 Oracle 客户端 |
| PostgreSQL | `psycopg2-binary` | 性能优于 pg8000，C 扩展，按平台发 |
| 达梦 | `dmPython` | 达梦安装介质里自带，仅 Linux / Windows，无 macOS 版 |

补充方法：在**与目标机相同 Python 版本**的有网机器上执行 `pip download -d wheels <包名>`，把产出的 whl 丢进本目录，重跑一次安装脚本即可被自动识别。
