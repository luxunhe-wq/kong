# 从 kong 升级到 MoniLite

MoniLite 默认仍监听 **8080**。新版界面和部署文件采用 MoniLite 名称；既有账号、会话及设置可以保留。

## Docker Compose

新版 Compose 项目和服务名是 `monilite`，默认数据卷是 `monilite_monilite-data`。旧版默认数据卷是 `kong_kong-data`。升级时必须指定旧数据卷，否则新版会创建空卷并显示首次初始化页面。

1. 在旧项目目录、更新文件之前停止旧容器。普通 `down` 会保留账号卷，**不要加 `--volumes` 或 `-v`**：

   ```bash
   docker compose down
   docker volume inspect kong_kong-data
   ```

   如果曾用 `-p` 或 `COMPOSE_PROJECT_NAME` 自定义项目名，请使用实际旧卷名；可在停止前通过 `docker inspect` 查看容器的 Mounts。

2. 更新源码与 Git 远程地址（项目本地文件夹名称可以保持原样）：

   ```bash
   git remote set-url origin https://github.com/luxunhe-wq/kong.git
   git pull --ff-only
   ```

3. 在项目 `.env` 中添加以下两行，沿用旧账号卷，并且在今后更新时保留它。`external=true` 使用已经存在的卷，也避免新旧 Compose 标签不一致：

   ```dotenv
   MONILITE_DATA_VOLUME=kong_kong-data
   MONILITE_DATA_VOLUME_EXTERNAL=true
   ```

4. 启动新版：

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

   若使用已发布镜像，在 `.env` 中设置实际可拉取的 `MONILITE_IMAGE`，再执行 `docker compose up -d --no-build --pull always`。新 GHCR 镜像地址是 `ghcr.io/luxunhe-wq/monilite`，需要发布者先完成发布并将包设为公开。

如果已经更新了 Compose 却还没停止旧容器，可在项目目录执行 `git show 59b67c0:compose.yaml > compose.legacy.yaml` 取出改名前的部署文件，然后用 `docker compose -f compose.legacy.yaml down` 停止旧项目；自定义项目名时仍需带上原来的 `-p`。删除临时文件后，再启动新项目。不要同时运行两个服务争用同一端口。

## Python 与 systemd

直接运行 Python 的用户可继续使用原来的 `data/` 或自定义数据目录。新安装默认数据库文件名是 `monilite.sqlite3`；如果目录中已有 `kong.sqlite3`，MoniLite 会继续使用它及其配套 SQLite 文件，不会新建空账号库。**不需要手动重命名数据库**。

systemd 示例改为 `deploy/monilite.service`，默认安装目录 `/opt/monilite`、数据目录 `/var/lib/monilite`。旧用户可以继续使用已安装的 `kong.service`，只更新其指向的源码；显示名称可以自行改为 MoniLite。

若要切换到新服务文件，请先停止并禁用旧服务，然后把 `WorkingDirectory` 和 `ExecStart` 改为实际源码目录，并将 `Environment=MONILITE_DATA_DIR` 和 `StateDirectory` 改为原数据目录（例如 `/var/lib/kong` 和 `kong`），再按 README 安装新服务。备份数据库时，停止所有使用该目录的服务后复制完整目录。

## 配置与浏览器

新环境变量采用 `MONILITE_*`。原生服务兼容旧版 `KONG_HOST`、`KONG_PORT`、`KONG_DATA_DIR`、`KONG_HOST_ROOT`、`KONG_SECURE_COOKIE`；同时设置时，新名称优先。Compose 兼容旧 `.env` 中的 `KONG_IMAGE`、`KONG_HOST`、`KONG_PORT`、`KONG_SECURE_COOKIE`，但旧数据卷仍需明确配置 `MONILITE_DATA_VOLUME`。直接 `docker run` 时请使用新变量名称，因为镜像内的新变量默认值优先于旧名称。

在相同浏览器和访问地址下，旧版主题、刷新频率、告警阈值与有效登录状态会继续识别。重新登录或退出时会清理旧会话 Cookie。改变域名或访问端口后，浏览器偏好通常需要重新设置。
