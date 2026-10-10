# 来源、参考与依赖审查

本版本从使用者提供的原有个人WSGI应用程序文件演化而来。仅复用已检查的后端程序、前端程序、CSS与简单原创SVG图标；没有复制私人计划JSON、Excel、数据库、环境文件、原部署文档、日志、备份或工作记忆。公开计划内容重新编写为通用模板。原私人部署不受本版本影响。

原有程序文件未带第三方版权头或来源声明。代码提供者已确认以MIT许可发布这些自有程序与新增实现。公开版权名采用项目贡献者。

## 聚焦参考仓库（只比较功能，不复制代码/数据）

| 项目 | 核对的许可证 | 借鉴点 | 本项目范围 |
| --- | --- | --- | --- |
| [wger](https://github.com/wger-project/wger) / [LICENSE](https://github.com/wger-project/wger/blob/master/LICENSE.txt) | 应用AGPL-3.0-or-later；运动/食材数据另有CC许可 | 将计划、实际训练和体重记录分开；自托管部署文档 | 维持轻量单实例，不引入运动/食物数据库，不复用AGPL代码或CC数据 |
| [Ryot](https://github.com/IgnisDa/ryot) / [LICENSE](https://github.com/IgnisDa/ryot/blob/main/LICENSE) | GPL-3.0 | 身体测量趋势、个人数据自有、移动使用 | 不引入媒体追踪、OIDC、多人账户或商业功能，不复用其代码和数据 |

上述项目没有成为本代码的依赖；参考不意味着其维护者背书本项目，也不意味着可以把它们代码改为MIT。

## 运行依赖

| 组件 | 版本策略 | 许可证 / 来源 |
| --- | --- | --- |
| Python | 3.12+，Docker使用3.12-slim基础镜像 | PSF许可，[Python许可](https://docs.python.org/3/license.html) |
| Gunicorn | 固定26.2.0 | MIT，[PyPI](https://pypi.org/project/gunicorn/) / [LICENSE](https://github.com/benoitc/gunicorn/blob/master/LICENSE) |
| packaging | Gunicorn传递依赖，固定26.3 | Apache-2.0 OR BSD-2-Clause，[PyPI](https://pypi.org/project/packaging/) |
| Caddy | 服务器Compose固定2.11.7-alpine | Apache-2.0，[项目与许可](https://github.com/caddyserver/caddy) / [发布记录](https://github.com/caddyserver/caddy/releases) |

没有vendored第三方源代码，没有外部字体、图片、图标或前端CDN依赖。Python标准库包含SQLite、TLS、HTTP客户端及Excel XML/ZIP生成；镜像中系统组件保留其自有许可证。镜像标签固定主/补丁版本但没有digest锁定，升级时仍需检查镜像与依赖安全公告。

GitHub Actions配置引用MIT许可的 [checkout](https://github.com/actions/checkout)、[setup-python](https://github.com/actions/setup-python)、[setup-node](https://github.com/actions/setup-node) v7，权限只读，关闭凭据持久化和不需要的缓存。远程CI结果以仓库Actions记录为准。

Playwright CLI只用于本地交付检查，不是运行依赖，不进入发布包。测试本身使用标准库unittest，无真实付费模型调用。

## 官方接口与部署依据

- [OpenAI Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)：兼容请求字段及响应结构。
- [Docker Ubuntu 安装](https://docs.docker.com/engine/install/ubuntu/)：官方apt安装方式、系统支持与防火墙边界。
- [Caddy 自动HTTPS](https://caddyserver.com/docs/automatic-https)：域名、自动证书和HTTP转HTTPS。
- [MIT 标准许可](https://opensource.org/license/mit)：许可文本。

版本与页面在本次交付检查时核对；公开发布前及之后的版本升级应重新核对，项目不承诺自动跟随最新版本。
