# 部署与更新（systemd + Cloudflare Tunnel）

参考部署：一台 Ubuntu 24.04 服务器，独立系统用户 + systemd，公网 HTTPS 由独立的 Cloudflare Tunnel 提供，不对公网开端口。下文以默认用户 `ubuntu`、目录 `/srv/memhub`、域名 `mem.example.com` 为例，按你的环境替换。

## 首次安装前置条件

- 以 `ubuntu` 登录 Ubuntu 24.04，具有免密 `sudo`。服务器备齐 `python3.12`、`python3.12-venv`、Git、curl、cloudflared；安装阶段需要访问 GitHub 和 Python 包仓库，运行服务只使用本地资源。
- 你的代码仓库（fork 本仓库即可）已发布要部署的代码，包括锁定依赖导出的 `requirements.txt`；另建一个**私有**空仓库（如 `YOUR_GITHUB_USER/memhub-memory`）用于备份记忆，不要有任何提交。备份仓库已有历史时，不可用本首次安装脚本覆盖或合并。
- `memhub` 系统用户尚不存在；`/srv/memhub/app`、`memory`、`state` 尚不存在；三个 memhub 系统级 unit 尚不存在。脚本发现已存在即退出，不支持在半完成的安装上重跑。
- 在创建用户前，先由管理员把 SSH 配置放到 `/srv/memhub/.ssh/`。允许 `/srv/memhub` 只为预放 SSH 文件而存在；脚本创建系统用户后把此 `.ssh` 的属主改为 `memhub`。系统账户 shell 为 nologin，脚本通过 `sudo -H -u memhub` 使用它的 home。
- SSH `config` 中预先配置 `github-memhub` 和 `github-memhub-memory` 两个别名，`HostName github.com`、`User git`、各自的 `IdentityFile`、`IdentitiesOnly yes`。代码 deploy key 只需读权限，记忆备份 deploy key 需要写权限。私钥权限 `600`；预先核实并配置 GitHub 的 `known_hosts`。脚本不会生成、打印或接受未知 SSH 主机密钥。
- `ubuntu` 的 cloudflared 已有当前域名的 tunnel 管理授权（例如既有 `/home/ubuntu/.cloudflared/cert.pem`）。不会改动已有的其他 tunnel 配置或服务。
- 检查根目录 `memhub.prod.toml`：目录、监听 `127.0.0.1:8796`、公网地址 `https://mem.example.com`、OAuth 参数、权限路径及 `[deployment]`。随仓库提供的 unit 使用用户指定的固定 `/srv/memhub` 部署布局；修改此布局时需一并修改 unit。

所有部署脚本必须从 `ubuntu` 可读取的管理 checkout 运行，保留这份 checkout 供后续更新使用。`/srv/memhub` 及其应用目录维持 `700` 权限，`ubuntu` 无法直接读取或执行里面的脚本；脚本内部通过免密 sudo 读取生产参数并以 `memhub` 身份操作应用。首次安装脚本从这份管理 checkout 运行，显式接收该 checkout 的生产参数文件；脚本会把这份参数安装进 `/srv/memhub/app/memhub.prod.toml`。不要在目标 `/srv/memhub/app` 预先 clone，否则首次安装会按约定拒绝。

## 执行顺序

在 `ubuntu` 可读取、含要部署代码的管理 checkout 内，以 `ubuntu` 执行：

```bash
bash deploy/install.sh "$PWD/memhub.prod.toml"
sudo -H -u memhub /srv/memhub/app/.venv/bin/memhub --config /srv/memhub/app/memhub.prod.toml set-password
sudo systemctl restart memhub.service
bash deploy/setup-tunnel.sh /srv/memhub/app/memhub.prod.toml
```

`set-password` 交互输入两次 OAuth 口令，argon2 哈希写入 `/srv/memhub/state/.env`。首次 install 创建 `600` 权限的 `.env`，其中 `STATIC_TOKENS` 包含 `claude-code`、`gemini-cli` 两个随机 token。未设置口令前 OAuth 登录拒绝；服务和静态 token 可启动使用。需要配置 CLI 时，本人在服务器私下读取该文件，勿贴到聊天、日志或代码中。

install 创建独立系统用户、三个 `700` 目录、clone 代码、创建 venv、安装依赖和项目、初始化独立记忆 Git 仓库、设置备份 remote，启用 `memhub.service` 与小时备份 timer，最后等待本地 `/healthz` 为 `200`。

setup-tunnel 仅创建 `memhub.yml` 和 `cloudflared-memhub.service`，复用同名 tunnel 或创建它，验证 ingress，启用 ubuntu user unit 和 lingering，检查 `127.0.0.1:20245/ready`，最后执行 `cloudflared tunnel route dns --overwrite-dns` 建立 `mem.example.com` 路由。这一步会实际修改该子域 DNS；已有同名配置/单元时脚本退出。不会改已有其他 tunnel 配置或服务。

## 部署后验收

```bash
curl --fail --silent https://mem.example.com/healthz
curl --fail --silent https://mem.example.com/.well-known/oauth-protected-resource/mcp
curl --fail --silent https://mem.example.com/.well-known/oauth-authorization-server
curl --include --silent https://mem.example.com/mcp
sudo systemctl status memhub.service memhub-backup.timer
systemctl --user status cloudflared-memhub.service
sudo -H -u memhub git -C /srv/memhub/memory remote -v
sudo systemctl start memhub-backup.service
sudo journalctl -u memhub-backup.service --no-pager
```

无 token 的 `/mcp` 请求应为 `401`，`WWW-Authenticate` 指向公网 protected resource metadata。元数据中的 issuer、authorization/token/register/revocation endpoint 与资源地址都应为 `public_url`。按 `CLIENTS.md` 完成真实客户端连接、分别验证来源和权限，再重启 VM 确认两个服务恢复与 backup timer 存在。实际 app 授权、公网连通、VM 重启和 GitHub push 都需要在部署后验收，本地测试不能证明这些结果。

## 更新

发布代码后，先让 `ubuntu` 可读取的管理 checkout 更新到该版本，再在这份 checkout 内以 `ubuntu` 执行：

```bash
bash deploy/update.sh /srv/memhub/app/memhub.prod.toml
```

更新执行 `git pull --ff-only`、安装锁定依赖和项目、刷新三个 systemd unit、重启 memhub，等待 `/healthz` 返回 `200`。生产参数为跟踪文件，若服务器直接编辑它而远端又修改同一文件，Git pull 会明确报错；先自行处理配置差异再执行更新，脚本不覆盖修改。更新不改 tunnel 或 DNS。

查看运行问题：

```bash
sudo journalctl -u memhub.service --no-pager
journalctl --user -u cloudflared-memhub.service --no-pager
sudo journalctl -u memhub-backup.service --no-pager
```

记忆每次工具写入已经产生 Git commit，backup unit 只做 `git push origin main`，每小时执行；不替用户处理远端分叉、SSH 鉴权失败或仓库冲突。访问日志在参数指定的 state 路径，仅含时间、身份、工具名和路径。

## 离线运行边界

memhub 运行时不下载依赖，不使用 CDN、远程字体或外部 API。首次安装和更新按用户要求通过 SSH Git remote 与 pip 获取已经发布的代码/依赖；Cloudflare Tunnel 和 GitHub 小时备份也按明确部署需求需要外网。若部署环境严格禁止全部外网，需预先准备本地代码与 wheel 仓库，并无法执行 Tunnel/GitHub 备份；这与本项目已确认的公网接入及备份方案冲突，不能声称已满足绝对断网部署。
