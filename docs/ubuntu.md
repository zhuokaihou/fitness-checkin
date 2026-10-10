# 自有 Ubuntu 服务器部署（Docker + Caddy HTTPS）

适用于 Ubuntu 24.04 LTS / 26.04 LTS，建议至少1GB RAM、2GB可用磁盘。需要一个自己控制的域名，DNS A/AAAA 指向服务器；没有IPv6时不要保留错误的AAAA记录。以下操作用于新实例，与任何现有私人服务无关。

## 1. 安装 Docker Engine 与 Compose

按 [Docker 官方 Ubuntu 安装步骤](https://docs.docker.com/engine/install/ubuntu/) 安装。官方 apt 仓库方式示例（有冲突包时先按官方文档处理）：

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF_DOCKER
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF_DOCKER
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker version
sudo docker compose version
```

本文使用 `sudo docker`；可按 Docker 文档配置免 sudo 用户，但 docker 组具有接近 root 的权限。未安装 Docker Desktop 的 Ubuntu 不需要 Desktop。

## 2. 上传公开源代码并配置

把公开发布包解压到自己选定的目录（例如 `/opt/fitness-checkin`），进入该目录。不要上传私人数据库或任何本机工作目录。仓库发布后也可从已确认的公开仓库克隆。

```sh
cp .env.example .env
chmod 600 .env
```

编辑 `.env`：

```dotenv
SITE_DOMAIN=fitness.example.com
ACME_EMAIL=admin@example.com
PUBLIC_URL=https://fitness.example.com
SECURE_COOKIE=true
LLM_ALLOWED_HOSTS=api.openai.com
```

以上是保留域名假值，必须替换为自己的实际域名和邮箱。API 密钥之后从已登录的设置页填写，不能放入 Compose 或 `.env.example`。如果使用其他兼容服务，把其精确主机名加入 `LLM_ALLOWED_HOSTS`，重建服务。

## 3. 创建账户并启动

```sh
sudo docker compose -f compose.yaml -f compose.https.yaml config --quiet
sudo docker compose -f compose.yaml -f compose.https.yaml build
sudo docker compose -f compose.yaml -f compose.https.yaml run --rm app python manage.py create-owner --username my_owner
sudo docker compose -f compose.yaml -f compose.https.yaml up -d
sudo docker compose -f compose.yaml -f compose.https.yaml ps
sudo docker compose -f compose.yaml -f compose.https.yaml logs --tail=50 caddy
```

云防火墙/安全组允许 TCP80、443；不要公开8080。SSH端口按你的管理方式保留。Compose 会把8080限制在127.0.0.1，Caddy通过内部Docker网络代理。HTTP自动转HTTPS，证书自动申请续期；证书卷持久化。

所需外联：构建时访问镜像仓库和PyPI；运行时 DNS、ACME HTTPS，以及你允许的模型提供商 TCP443。无需任何云厂商账户集成。Docker可能绕过部分 UFW 规则，端口绑定本身已做回环限制；外层防火墙只开放所需端口。

## 4. 验证

```sh
curl -fsS http://127.0.0.1:8080/api/health
curl -I https://fitness.example.com/
curl -I http://fitness.example.com/
```

将命令中的假域名换成实际域名。浏览器应看到有效HTTPS、HTTP跳转，匿名只能看到登录页面；未登录导出返回401。登录后记录一项，再刷新确认持久化。模型配置是可选，不影响基础功能。

数据卷 `fitness_data` 包含数据库及模型密钥配置，不能公开；Caddy的两个卷保存证书/配置。每次维护继续带 `-f compose.yaml -f compose.https.yaml`，避免误用HTTP环境。不要对已有应用/数据库直接覆盖部署。

升级、备份和排障见 [运行维护](operations.md)。没有真实服务器权限的交付检查不能代替上述步骤的服务器验收。
