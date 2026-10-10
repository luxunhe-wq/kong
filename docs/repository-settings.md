# GitHub 仓库简介与搜索标签

README 标题与正文已包含中英文功能介绍。GitHub 的仓库搜索还使用仓库简介和 Topics；这些信息需要仓库管理员在仓库首页的 About 区域编辑。部署密钥只能推送 Git 内容，不能修改这些管理设置。关键词可以提升相关性，但不能保证搜索排名或立即收录。

仓库：https://github.com/luxunhe-wq/MoniLite

点击仓库首页右侧 **About → 齿轮图标**，填写以下内容并保存。

**Description（简介）**

```text
MoniLite · 轻量 Linux 服务器监控与 Docker 容器监控面板 | Lightweight Linux server monitoring dashboard: CPU, memory, disk, network, ports, disk usage analysis. Docker Compose, Python, dark mode, mobile UI.
```

**Website（项目主页）**

```text
https://github.com/luxunhe-wq/MoniLite#readme
```

**Topics（逐个添加）**

```text
monilite
linux
server-monitoring
system-monitor
monitoring
dashboard
docker
docker-compose
container-monitoring
cpu-monitoring
memory-monitoring
disk-usage
network-monitoring
port-monitoring
python
psutil
self-hosted
homelab
responsive-design
dark-mode
```

项目和仓库已改名为 `MoniLite`。GitHub 为旧版 `kong` 仓库的克隆地址提供重定向；建议使用新地址。README 的展示标题为「MoniLite · Linux 服务器监控与 Docker 容器监控面板」。可以用 `user:luxunhe-wq server-monitoring` 或 `repo:luxunhe-wq/MoniLite` 定位项目；搜索索引更新需要时间。

## 使用管理员令牌填写

相同内容保存在 `.github/repository-metadata.json`。也可以在本机运行下面的脚本，按提示输入令牌（输入不回显）：

```bash
python3 deploy/update_github_metadata.py
```

创建 GitHub fine-grained personal access token 时，只选择 `luxunhe-wq/MoniLite` 仓库并授予 **Administration: Read and write**。令牌不需要源码写入或其他仓库权限。若由服务器代为执行，可以通过安全终端将令牌保存到仓库外、权限为 `600` 的文件，再运行：

```bash
python3 deploy/update_github_metadata.py --token-file /root/.config/monilite/github-token
```

不要在聊天、Git 提交、截图或命令参数中公开令牌。脚本会验证管理员身份，再更新简介、项目主页和 Topics；配置文件本身不会自动修改 GitHub 的 About。

仓库改名后的 README、界面入口和 `.github/repository-metadata.json` 已使用新地址。原链接在 GitHub 重定向期间仍可使用。
