# kong

轻量、实时的服务器监控系统。深色侧栏搭配浅色仪表盘，支持深色主题和手机布局。全部指标来自当前服务器，没有模拟数据，也不依赖外部 CDN。

**为什么选择 kong？** 如果你维护一台个人服务器或几台小型服务器，希望用手机随时查看系统和 Docker 的运行状态，kong 提供一个安装简单、界面清晰、自带账户管理的本机监控面板。运行依赖只有 psutil，无需 Node.js、外部数据库或单独部署指标服务。

目前专注单机实时监控，历史保留 1 小时；多服务器汇总、长期时序存储和外部告警推送尚未实现。

## 快速安装

推荐在 Linux 服务器上使用 Docker Engine 和 Docker Compose 插件。项目仓库：https://github.com/luxunhe-wq/kong 。拉取源码后执行以下命令：

```bash
git clone https://github.com/luxunhe-wq/kong.git
cd kong
docker compose up -d --build
```

启动后打开 **`http://服务器IP:8080`**，首次访问自行创建管理员账号。默认监听 `0.0.0.0:8080`，请在防火墙和云服务器安全组放行 **TCP 8080**。没有默认账号或密码，也不需要安装 Python 或外部数据库。

8080 被占用时，将启动命令改为：

```bash
KONG_PORT=9090 docker compose up -d --build
```

此时放行 TCP 9090，访问 `http://服务器IP:9090`。常用命令：

```bash
docker compose ps                  # 检查运行状态
docker compose logs -f --tail 100  # 查看日志
git pull --ff-only                # 拉取最新代码
docker compose up -d --build      # 更新容器，保留账号数据
docker compose down               # 停止，保留账号数据
```

| 环境 | 使用方式与验证状态 |
| --- | --- |
| Linux x86-64 / amd64 | 源码构建及 Docker 部署已验证 |
| Linux ARM64 / aarch64 | Dockerfile 支持本机构建；尚未在 ARM64 服务器实测 |
| ARM32、x86 32 位 | 暂不提供预构建镜像 |

账号存储在 Docker 数据卷中；请勿执行 `docker compose down -v`，以免删除账号。详细部署和镜像拉取方式见下文。

## 启动

### Docker Compose（推荐用于分发）

在 Linux 服务器上安装 Docker Engine 和 Docker Compose 插件，拉取项目后，在项目目录执行：

```bash
docker compose up -d --build
```

访问 `http://服务器IP:8080`，首次访问创建管理员账号。无需安装 Python、Node.js 或数据库，镜像包含 Python、psutil 和 Docker CLI。首次构建需要联网下载基础镜像和依赖。服务器防火墙及云安全组需要放行 TCP 8080。

如果默认端口已被占用：

```bash
KONG_PORT=9090 docker compose up -d --build
```

此时访问 `http://服务器IP:9090`。使用宿主机网络模式，端口由 `KONG_PORT` 控制，无需添加 `ports` 映射。长期自定义配置可在项目目录创建 `.env`，例如 `KONG_PORT=9090`；未配置时直接使用默认值。已有原生服务占用 8080 时，请使用其他端口，不要同时启动两个服务监听同一端口。

账号数据库保存在命名数据卷 `kong_kong-data`，容器重启、更新及普通 `docker compose down` 均会保留。配置自动重启和健康检查：

```bash
docker compose ps
docker compose logs -f --tail 100
# 拉取最新代码后更新镜像及容器，保留账号数据
docker compose up -d --build
# 停止并移除容器，保留账号数据
docker compose down
```

不要执行 `docker compose down -v`，它会删除账号数据卷。备份前停止服务，再复制数据卷中的完整数据库目录。容器数据与原生启动的 `data/` 目录相互独立，不会自动迁移已有账号。

Compose 已设置宿主机 PID / 网络 / UTS 命名空间、只读宿主机根目录 `/host`、Docker Socket 和进程读取权限。CPU、内存、网络、监听端口、进程归属、系统名称与磁盘目录均读取宿主机，界面保留宿主机原始路径。容器根文件系统只读，仅数据库卷及临时目录可写。为读取宿主机其他用户的进程归属，使用 `SYS_PTRACE` / `DAC_READ_SEARCH`，并设置 `apparmor:unconfined`，避免 Ubuntu 等系统上的 Docker 默认 AppArmor 策略阻止读取宿主机进程。

该部署方式面向 Linux 上的 rootful Docker Engine；Docker Desktop 监控的是其 Linux 虚拟机，rootless Docker 和用户命名空间重映射不作为完整宿主机监控的支持环境。默认 Socket 路径为 `/var/run/docker.sock`。启用 SELinux 的服务器需按本机策略允许所需读取；不要对 `/` 添加 `:z` 或 `:Z` 重标记。

应用仅执行查询命令，但挂载 Docker Socket 本身提供 Docker API 权限，`read_only` 挂载不会把 API 限制为只读。部署在你信任的服务器上，首次初始化在可信网络完成。通过 HTTPS 反向代理访问时设置 `KONG_SECURE_COOKIE=1`；仅 HTTP 时保持默认 `0`。可用 `KONG_HOST=127.0.0.1` 限制为本机访问。

### 分发已构建镜像

仓库提供镜像构建文件，默认构建本地镜像 `kong:local`。发布者可以给镜像添加自己的仓库地址并推送（下方 `YOUR_ACCOUNT` 为示例，需要替换）：

```bash
docker build -t YOUR_ACCOUNT/kong:1.0.0 .
docker push YOUR_ACCOUNT/kong:1.0.0
```

镜像发布后，使用者只需下载本项目的 `compose.yaml`，无需下载源码。在该文件所在目录执行：

```bash
KONG_IMAGE=YOUR_ACCOUNT/kong:1.0.0 docker compose up -d --no-build --pull always
```

也可将 `KONG_IMAGE=YOUR_ACCOUNT/kong:1.0.0` 写入同目录的 `.env`，以后执行相同命令即可拉取更新并重建容器。私有镜像需先 `docker login`。本项目的 GHCR 发布地址和操作见下文；镜像发布前请使用源码构建方式。

也可通过离线镜像包分发，无需镜像仓库。发布者导出镜像：

```bash
docker save kong:local | gzip > kong-image-linux-amd64.tar.gz
```

使用者将镜像包和 `compose.yaml` 放在同一目录，执行：

```bash
docker load -i kong-image-linux-amd64.tar.gz
docker compose up -d --no-build --pull never
```

离线包仅支持构建时的 CPU 架构，文件名应与实际架构一致；上面的文件名适用于 Linux amd64 构建。

Dockerfile 支持通过 Buildx 构建 Linux amd64 / arm64 镜像；多架构发布示例：

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t YOUR_ACCOUNT/kong:1.0.0 --push .
```

### GitHub Actions 发布镜像

项目包含自动测试工作流，以及发布到 GitHub Container Registry（GHCR）的多架构工作流。仓库维护者可在 GitHub 的 **Actions → Publish Docker image → Run workflow** 手动构建发布，或推送 `v1.0.0` 这样的版本标签触发发布。

工作流使用 GitHub 自动提供的 `GITHUB_TOKEN`，不需要在项目中保存密码或个人令牌。发布目标为 `ghcr.io/luxunhe-wq/kong`，包含 `linux/amd64` 和 `linux/arm64`。首次发布后，在 GitHub 的 Packages 设置中将镜像包设为公开，其他人才能免登录拉取。源码仓库公开不代表镜像包自动公开。

**完成发布并设为公开后**，用户可以只下载 Compose 文件并启动，无需本机构建：

```bash
mkdir -p kong
cd kong
curl -fL https://raw.githubusercontent.com/luxunhe-wq/kong/main/compose.yaml -o compose.yaml
KONG_IMAGE=ghcr.io/luxunhe-wq/kong:latest docker compose up -d --no-build --pull always
```

默认访问 `http://服务器IP:8080`。如需更改端口，可在启动命令前再加 `KONG_PORT=9090`。建议将实际 `KONG_IMAGE` 和 `KONG_PORT` 保存到同目录 `.env`，以后更新执行 `docker compose up -d --no-build --pull always` 即可。镜像发布前请使用源码构建方式；ARM64 构建成功并不等同于已经在 ARM64 实机上验证全部监控功能。

### Python 直接启动

需要 Python 3.9+，支持 Linux。Docker 监控需要本机 Docker CLI 和读取 Docker 的权限。

```bash
cd kong
python3 -m pip install -r requirements.txt
python3 server.py --host 0.0.0.0 --port 8080
```

访问 `http://服务器IP:8080`，首次访问按照引导自行设置管理员账号和密码。没有预置账户。可通过 `--host`、`--port` 或 `KONG_HOST`、`KONG_PORT` 更改监听地址。

## 账户与权限

- **首次初始化**：仅允许创建一次首位管理员，并自动登录；并发初始化也只会有一个请求成功。
- **登录与注册**：账号为 3–32 位字母、数字、下划线、点或短横线，首位不能是点或短横线；密码为 10–128 个字符。注册账号默认是待审核成员，管理员通过审核后才可登录。
- **成员**：登录后查看监控、导出报告、调整自己的浏览器偏好和修改自己的密码。
- **管理员**：在「设置 → 用户管理」审核申请、创建用户、修改权限、停用账号、重置密码、删除用户，以及开启或关闭注册。
- **会话**：登录有效期为 12 小时。退出登录、账号停用或删除、角色变化、密码重置均会撤销相应会话。修改自己的密码后当前设备保持登录，其他设备需重新登录。
- **管理员保护**：禁止删除、停用或降级当前登录管理员，并确保至少保留一名可登录管理员。

密码使用随机盐及 PBKDF2-SHA256（600,000 次）存储；数据库仅存储会话令牌的哈希。Cookie 为 HttpOnly、SameSite=Lax；修改接口验证 CSRF 与来源，登录接口限制尝试频率。监控 API 需要有效登录，用户管理接口额外验证管理员权限。

账户与设置持久化在 SQLite 中。直接启动时默认保存在项目 `data/` 目录，可用 `KONG_DATA_DIR` 自定义；systemd 服务保存在 `/var/lib/kong/`。备份时停止服务后复制完整数据目录，恢复时保持目录权限。不要删除数据库，否则账户与初始化状态会丢失。数据目录已加入 `.gitignore`。

## 功能

- **概览**：CPU、内存、磁盘、网络、服务器运行时间、容器状态。
- **资源详情**：概览中的 CPU、内存、磁盘、网络卡片可直接点击，进入对应详情。详情按资源分类，不再重复概览的四张指标卡。
- **CPU / 内存**：核心用量、负载、Swap 和占用较高的进程；内存排行独立按全量进程的内存用量计算。
- **磁盘分析**：管理员可按目录分析实际磁盘占用，逐级进入子目录，查看大文件排行、文件属性与修改时间，复制完整路径、筛选或导出分析快照。
- **网络**：吞吐量趋势、各网卡状态、IP、实时速率和累计收发。
- **Docker**：容器搜索、状态筛选、CPU / 内存 / 网络 / 磁盘消耗、端口和详情。
- **端口管理**：查看 TCP 监听、UDP 未连接的绑定端口和 Docker 发布映射，按协议、绑定范围、端口或服务搜索；点击查看 PID、用户、可执行文件、systemd 服务及容器归属。CPU / 内存资源详情仍保留进程占用排行。
- **设置**：浅色 / 深色主题、刷新间隔、暂停刷新和资源提醒阈值。偏好保存在当前浏览器。
- **账户管理**：管理员初始化、登录、注册审核、用户权限及密码管理。
- **报告**：导出包含当前指标、Docker 和历史趋势的 JSON 文件。

系统每 2 秒采集一次，历史趋势在内存中保留 1 小时；重启服务后重新积累。Docker 独立每 10 秒采集，端口独立每 5 秒采集，故障不影响系统指标。界面支持最近 5 分钟、15 分钟、1 小时趋势，并可悬停查看具体采样。

切换标签页、锁屏或浏览器休眠后，返回页面会自动获取最新数据并恢复定时刷新；网络中断后也会自动重试。超时请求不会阻塞后续刷新，过期响应不会覆盖新数据。手动暂停自动刷新时保持暂停。

CPU 总使用率为所有逻辑核心的平均值；进程和 Docker CPU 按单核统计，可能超过 100%。内存使用量为总量减去可用内存。网络汇总排除桥接、容器接口和常见隧道接口以减少重复统计，各接口仍单独展示。文件系统按设备去重，不重复列出绑定挂载。Docker 内存比例基于容器限制。磁盘 I/O 使用 psutil 的块设备累计统计，包含底层设备与逻辑设备时可能重复。

端口清单以宿主机网络命名空间为范围，按地址、端口和协议区分，TCP 只收集监听状态，UDP 只收集未连接的绑定端点（可能包含临时使用 UDP 的程序）。Docker 通过实际发布配置识别容器映射，未发布的 `EXPOSE` 端口不列入。桥接容器内部端口不单独扫描；宿主机网络模式的容器通常显示为进程。读取权限不足或进程退出可能导致归属信息缺失；UDP 已绑定和 Docker 已发布不保证应用正在接收请求。绑定范围不等同于公网可达性，仍需结合防火墙、路由与安全组判断。所有操作均只读，不关闭端口或终止服务。

目录分析只读取元数据，不读取文件内容或执行删除。后台按需扫描，结果缓存 5 分钟，可点击「重新扫描」刷新。按实际分配的磁盘块统计，与文件逻辑大小可能不同；不跟随符号链接、跳过虚拟目录和其他文件系统，硬链接只计一次。单次扫描设有 25 秒及 800,000 条目的预算，深度与当前目录条目数量也有限制；达到上限或遇到无法读取的内容时明确标记为部分结果，可以进入具体子目录继续分析。最多展示当前目录占用最高的 200 项和已扫描范围内最大的 50 个文件。

目录统计不包含已删除但仍被进程打开的文件、文件系统元数据等，因此可能与 `df` 的已用空间不同。目录路径和文件信息只对管理员开放，普通成员仍可查看资源指标。

## 后台服务

本仓库提供以 `/opt/kong` 为安装目录的 systemd 示例。安装在其他目录时，先修改 `deploy/kong.service` 中的 `WorkingDirectory` 和 `ExecStart`；使用虚拟环境时将 Python 路径改成虚拟环境解释器。服务通过 `StateDirectory` 为账号数据库提供独立、可写的持久化目录。

```bash
sudo cp deploy/kong.service /etc/systemd/system/kong.service
sudo systemctl daemon-reload
sudo systemctl enable --now kong
systemctl status kong
journalctl -u kong -f
```

需要本机和 Docker 读取权限。首次初始化在可信网络完成。公网部署使用 HTTPS 反向代理，保持原始 Host 头，并设置 `KONG_SECURE_COOKIE=1` 以启用 Secure Cookie；仅使用 HTTP 时不要开启此项。也可以仅监听 `127.0.0.1` 并通过 SSH 隧道访问。按部署需要放行 TCP 8080。

## API 与验证

`GET /api/metrics?seconds=300` 返回当前指标和指定时段的历史；范围最大 3600 秒，需要登录 Cookie。

`GET /api/auth/status` 返回是否已经初始化、是否开放注册和当前登录状态。

`POST /api/auth/setup`、`POST /api/auth/login`、`POST /api/auth/register` 接收 JSON `username` 与 `password`。初始化和登录返回会话 Cookie 与 CSRF 令牌；注册仅提交待审核申请。

`POST /api/auth/logout`、`POST /api/auth/password`、`GET /api/admin/users`、`POST /api/admin/users`、`PATCH /api/admin/users/{id}`、`DELETE /api/admin/users/{id}`、`PATCH /api/admin/settings` 提供账户及管理功能。除公开的初始化、登录与注册接口外，修改请求需要登录 Cookie 和 `X-CSRF-Token`。

`POST /api/storage/scan` 接收 JSON `path`（绝对目录路径）和可选的 `force`（布尔值），异步创建或复用目录分析任务；`GET /api/storage/scan?id=任务ID` 获取进度及结果。两者均要求管理员权限，创建任务同时验证 CSRF；任务按登录用户隔离。

`GET /api/health` 返回采集健康状态，数据超过 10 秒未更新时返回 HTTP 503。

```bash
python3 -m unittest discover -s tests -v
```

可选的浏览器端检查：

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
python3 tests/browser_smoke.py
python3 tests/browser_refresh.py
```

可通过 `KONG_TEST_BROWSER` 指定已有 Chromium 路径。浏览器检查启动独立的临时服务器和数据库，不创建或修改实际网站账号。覆盖管理员初始化、注册审核、权限、密码和用户管理，以及监控交互、报告导出、断线恢复、手机布局与旧版浏览器兼容；截图保存在 `.runtime/`。

`browser_refresh.py` 额外验证请求卡住、迟到响应、页面恢复与断网重连，并实际冻结浏览器页面 125 秒，确认无需重新加载即可继续更新。

Docker 部署检查（需要本机 Docker、Compose 和 psutil）：

```bash
docker compose build
python3 tests/docker_smoke.py
```

该检查使用独立的 Compose 项目、临时端口及账号数据卷，验证宿主机指标、监听端口与进程归属、Docker 发现、磁盘目录分析、只读挂载及容器重建后的登录保留，结束后仅清理测试容器和测试数据卷。

后端使用 Python 标准库 HTTP 服务与 psutil；前端为原生 HTML、CSS、JavaScript 和 SVG 图表，无需 Node.js 或前端构建。
