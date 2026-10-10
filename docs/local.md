# 本地部署

以下命令从项目根目录执行。一个实例只有一个账户，不提供默认用户名/密码，不开放匿名记录或模型调用。

## 方案一：直接运行

需要 Python 3.12+，本地端口8080未被占用。无需 pip 安装即可运行本地开发服务。

```sh
python3 manage.py create-owner --username my_owner
PUBLIC_URL=http://127.0.0.1:8080 SECURE_COOKIE=false python3 app.py
```

打开 `http://127.0.0.1:8080`。必须与 `PUBLIC_URL` 完全一致，不要混用 `localhost` 和 `127.0.0.1`。终端按 Ctrl-C 停止。数据持久化于 `data/fitness.sqlite3`，重启不会清空。

可选：为数据指定独立目录，每个命令须使用同一个 `DATA_DIR`：

```sh
DATA_DIR=./data-personal python3 manage.py create-owner --username my_owner
DATA_DIR=./data-personal PUBLIC_URL=http://127.0.0.1:8080 SECURE_COOKIE=false python3 app.py
```

自定义目录同样包含敏感数据，须加到本地 Git 忽略列表，不能打包公开。手机通过添加到主屏幕使用 HTTPS 服务器方案；这个本地服务只监听回环地址，不直接对局域网开放。

## 方案二：Docker Compose

需要已运行的 Docker Engine / Docker Desktop 和 Compose v2。首次构建需要下载 Python 基础镜像与 pip 依赖。

```sh
cp .env.example .env
chmod 600 .env
# 保持 PUBLIC_URL=http://127.0.0.1:8080、SECURE_COOKIE=false
# 先建镜像，再离线创建账户；此时没有启动对外服务

docker compose build
docker compose run --rm app python manage.py create-owner --username my_owner
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/api/health
```

账户命令需要交互终端，输入自己的强密码。数据在命名卷 `fitness_data`；绑定8080到本机回环地址。`docker compose down` 保留卷；不要使用 `down -v`，否则会删除数据。

## 可选虚构演示

只在独立演示实例创建账户后、记录库为空时执行：

```sh
python3 manage.py load-demo
# 或 Docker：
docker compose run --rm app python manage.py load-demo
```

`examples/demo.json` 明确标记为虚构数据，不包含账户或 API 密钥。它按你的开始日期偏移载入三天示例。默认安装不载入演示；已有记录时命令拒绝运行。

## 本地验证

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
docker compose config --quiet
```

测试启动127.0.0.1模拟 API，需要本机允许绑定回环端口。模型付费连接未在自动测试中使用。生产进程可选验证：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PUBLIC_URL=http://127.0.0.1:8080 SECURE_COOKIE=false .venv/bin/gunicorn --bind 127.0.0.1:8080 --workers 2 --threads 2 --timeout 60 --no-control-socket --umask 0077 --access-logfile /dev/null app:application
```

当前交付环境的实际验证范围见 `release-review.md`；Compose 配置校验不等同于容器启动验证。
