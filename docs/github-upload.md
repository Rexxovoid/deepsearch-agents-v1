# 首次上传 GitHub：deepsearch-agents-v1

## 1. 准备上传内容

上传源码、测试、文档、示例和依赖锁文件。`.gitignore` 排除 `.env`、`.envexamples`、本地环境配置、虚拟环境、依赖目录、构建目录、数据库、任务附件和产物、日志、工作区和缓存。`.env.example` 只提供占位值。

`docs/knowledge_base/` 是个人学习资料目录，只保留在本地，不加入 Git 提交，也不上传 GitHub。

README 已在开头注明基于 [didilili/deepsearch-agents](https://github.com/didilili/deepsearch-agents) 二次开发，说明原作者与本仓库扩展功能。原项目介绍和教程链接继续保留并注明来源。

不要直接把项目文件夹拖入网页上传：浏览器上传不能替代本地 Git 的忽略规则核验。

## 2. 在项目根目录初始化本地仓库

```powershell
git init -b main
git status
```

`git init` 创建本地版本库，`main` 是初始分支。`git status` 查看待提交文件，此时还没有上传到 GitHub。

## 3. 将文件加入暂存区，检查并提交

如果出现 `detected dubious ownership`，且明确显示此项目的 `.git` 归 `CodexSandboxOffline` 所有、当前用户为 `Administrator`，原因是工具初始化仓库时使用了沙箱账号。确认是本项目后，在自己的 PowerShell 中仅信任此目录：

```powershell
git config --global --add safe.directory C:/Users/Administrator/Desktop/deepsearch-agents-main
git status
```

该设置只为这个已确认的项目添加例外，不使用 `safe.directory '*'`。随后重新执行下面的暂存及提交步骤；此前跟随所有权报错出现的 `git diff --cached` 报错不代表参数需要删除。

```powershell
git add .
git diff --cached --stat
git diff --cached --name-only
git commit -m "Initial import: extend deepsearch-agents with persistence and account isolation"
```

`git add` 根据忽略规则收集文件，暂存区是本次提交清单。检查清单中没有真实配置、数据库或私人附件后再提交。`git commit` 只创建本地版本记录，仍未上传。

如果提示未配置提交者，设置你自己的姓名和 GitHub 邮箱，或使用 GitHub 提供的 noreply 邮箱；配置前请确认信息是否愿意出现在公开提交历史中。

## 4. 在 Edge 中创建空仓库

1. 保持登录 GitHub，打开 `https://github.com/new`。
2. Owner 选择你自己的账号；Repository name 填 `deepsearch-agents-v1`。
3. Description 可填：`基于 didilili/deepsearch-agents 扩展，增加任务持久化、历史会话管理与账号权限隔离。`
4. 可见性选择 **Public**。
5. 不添加 README、`.gitignore` 或 License 初始化文件，因为本地已准备项目内容；不凭空指定上游未确认的许可证。
6. 点击 **Create repository**，复制新仓库的 HTTPS 地址。

本步骤只创建 GitHub 上的空仓库，上传在下一步完成。

## 5. 关联远程仓库并推送

本项目的远程地址为 `https://github.com/Rexxovoid/deepsearch-agents-v1.git`：

```powershell
git remote add origin https://github.com/Rexxovoid/deepsearch-agents-v1.git
git remote -v
git push -u origin main
```

`origin` 是远程仓库别名。`git push` 才会把本地提交上传到 GitHub；`-u` 记住分支关系，以后通常执行 `git push` 即可。如果 Git Credential Manager 打开登录/授权界面，请由账号本人完成，不需要把密码或令牌发给协作者。

若提示 `remote origin already exists`，先用 `git remote -v` 确认现有地址，不要盲目覆盖。推送被拒绝时先查看报错，不使用强制推送。

## 6. 验证上传

```powershell
git status
git log -1 --oneline
git ls-remote --heads origin main
```

刷新 GitHub 仓库主页，确认 README 来源说明、源码、测试和更新记录可见，并确认 `.env`、本地数据库和任务文件没有出现。远程 `main` 的提交 ID 应与本地一致。

## 7. 后续更新

每次修改先维护 `docs/changes/update.md` 和当天主题日志，再检查并提交：

```powershell
git status
git diff
git add .
git diff --cached --stat
git commit -m "Describe this change"
git push
```

`.gitignore` 不会自动取消跟踪已经提交的文件；本次是全新本地仓库，忽略规则在首次提交前设置。
