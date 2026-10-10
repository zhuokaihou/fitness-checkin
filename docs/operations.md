# 运行维护：升级、备份、恢复与排障

所有命令从项目根目录执行。Ubuntu示例可加 `sudo`。HTTPS实例的命令始终带两个Compose文件。

## 持久化与升级

数据目录内有 `fitness.sqlite3`、运行时的 WAL/SHM 文件，以及可选模型凭据 `llm-config.json`。这些都属于私人数据，不加入仓库。Docker使用命名卷，普通重建镜像和 `down` 不删除数据。

升级前先备份数据库与模型配置，并记录当前代码版本；阅读发布说明。如果定制过 `plan.json`，单独保存通用模板修改，不把个人描述混入公开仓库。

```sh
# 按下一节备份后，取得已审查的新版本源代码
# 如使用已发布的Git仓库，可切换已确认的发行标签

docker compose -f compose.yaml -f compose.https.yaml build --pull
docker compose -f compose.yaml -f compose.https.yaml up -d
docker compose -f compose.yaml -f compose.https.yaml ps
```

应用使用 `CREATE TABLE IF NOT EXISTS` 创建新表，不清空已有记录。首次安装仅为本开源版本测试；不要把未知版本的私人生产数据库原地覆盖。未来破坏性数据库变更必须先提供迁移说明。更改开始日期保留原日期的记录，部分旧记录可能不在当前90天视图，但仍能全部导出。

## 数据库一致性备份

`manage.py backup` 使用SQLite backup API，运行中可做一致性快照；不能只复制活跃数据库主文件而忽略WAL。备份移除登录会话、预览令牌与登录失败计数，仍包含账户密码哈希、健康记录和分析，因此应放在私有加密位置。

直接运行：

```sh
mkdir -p backups
chmod 700 backups
python3 manage.py backup backups/checkin-before-upgrade.sqlite3
```

目标文件存在时拒绝覆盖，换一个文件名。Docker：

```sh
docker compose -f compose.yaml -f compose.https.yaml exec app python manage.py backup /data/checkin-before-upgrade.sqlite3
mkdir -p backups
chmod 700 backups
docker compose -f compose.yaml -f compose.https.yaml cp app:/data/checkin-before-upgrade.sqlite3 backups/checkin-before-upgrade.sqlite3
chmod 600 backups/checkin-before-upgrade.sqlite3
# 复制成功且确认备份可读后删除卷中的临时备份

docker compose -f compose.yaml -f compose.https.yaml exec app rm /data/checkin-before-upgrade.sqlite3
```

数据库备份 **不含 API密钥配置**，最简单的做法是恢复后重新从设置页填写。需要保留密钥时可单独私下复制 `llm-config.json`，权限600、加密保管；不要贴在问题反馈中。CSV/JSON导出适合查看记录，但当前没有JSON导入工具，不能代替完整数据库恢复。

## 恢复

先停止服务。不要向一个已有账户/记录的目录覆盖恢复；命令会拒绝。保留旧目录/卷以便回退。

直接运行示例（`data-empty` 是新的私有目录）：

```sh
DATA_DIR=./data-empty python3 manage.py restore backups/checkin-before-upgrade.sqlite3
DATA_DIR=./data-empty PUBLIC_URL=http://127.0.0.1:8080 SECURE_COOKIE=false python3 app.py
```

Docker恢复可在新Compose项目的新命名卷中完成（先停止旧应用占用的端口）：

```sh
docker compose -f compose.yaml -f compose.https.yaml stop app
# 从空卷启动一个临时容器，将宿主备份目录只读挂载，备份文件名须对应实际文件

docker compose -p fitness-restored -f compose.yaml -f compose.https.yaml run --rm -v "$PWD/backups:/restore:ro" app python manage.py restore /restore/checkin-before-upgrade.sqlite3
docker compose -p fitness-restored -f compose.yaml -f compose.https.yaml up -d app
```

如原来的Caddy仍在运行，通过Docker新项目网络不能直接代理新app。完整切换前停止旧Compose项目（`down`，不加`-v`），再启动新项目全部服务：

```sh
docker compose -p fitness-restored -f compose.yaml -f compose.https.yaml stop app
docker compose -f compose.yaml -f compose.https.yaml down
docker compose -p fitness-restored -f compose.yaml -f compose.https.yaml up -d
```

新项目会用新的Caddy证书卷申请证书；频繁恢复时注意ACME频率限制。验证登录、记录、导出和重启后持久化，再决定是否删除旧卷；不要先删除旧卷。新旧项目共用同目录的 `.env`，但数据卷不同。

恢复后旧会话全部失效，使用备份中的账户密码登录，重新设置API。需要重置密码：

```sh
python3 manage.py set-password
# Docker:
docker compose -f compose.yaml -f compose.https.yaml exec app python manage.py set-password
```

密码交互输入，不使用命令行明文参数；重置后全部会话失效。

## 排障

| 现象 | 检查 |
| --- | --- |
| 创建账户提示已存在 | 一个部署仅一个账户，用 `set-password` 重置；不要删除数据库 |
| 登录后仍跳回登录 | HTTP本地须 `SECURE_COOKIE=false`，生产须有效HTTPS；不要混用主机名 |
| 来源不匹配 / 403 | 地址栏Origin须与 `PUBLIC_URL` 一致（协议、主机、端口）；HTTPS使用两个Compose文件 |
| 保存409冲突 | 另一个页面已保存，当前页面读取最新记录；重新提交你的修改 |
| 模型地址被拒绝 | 精确主机须在 `LLM_ALLOWED_HOSTS`；只允许公网HTTPS443、空路径或 `/v1` |
| 模型失败 | 检查密钥、模型ID、余额和接口字段；错误响应体不会回显；重新调用仍可能收费 |
| 调用限额 | 等待30秒；每24小时最多20次，失败和测试也计数 |
| Caddy证书失败 | DNS/AAAA是否正确，80/443外部可达，系统时间正确，邮箱/域名非假值 |
| Docker不可用 | 确认引擎运行、Docker上下文和权限；`docker compose config` 成功不代表引擎运行 |
| 卷权限错误 | app以UID10001运行；命名卷应继承镜像目录所有权；自定义绑定挂载须由部署者设置所有者 |
| 忘记部署参数 | `.env`不被Python自动加载，直接运行需设置环境变量；所有维护命令要用同一数据路径 |

生产关闭HTTP访问日志，异常只记录错误类型，不记录请求体、Cookie或模型响应。分享问题时仅提供去除敏感数据的错误类型与版本。
