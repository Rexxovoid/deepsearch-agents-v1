# Docker Compose 启动命令路径修正

## 问题现象

执行 `docker compose --env-file .env -f docs/docker-compose.yaml up -d`，提示找不到文件。

## 定位证据

仓库文件搜索确认 Compose 文件实际位于 `docker/docker-compose.yaml`；README 启动命令仍引用不存在的 `docs/docker-compose.yaml`。本次确认是文档路径错误，无法据此判断文件何时移动，也不能证明此前容器端口变化的操作过程。

## 解决方法与修改位置

- `README.md`：修正 `-f` 路径，明确在项目根目录执行，显式使用根目录 `.env`，补充查看容器状态和端口的命令。
- `docker/docker-compose.yaml`：同步顶部启动说明，避免在 docker 目录未指定根目录环境文件而使用默认值。

## 验证结果与遗留问题

已核对实际文件路径和文档命令。仅文档及注释修改，无需运行应用测试。本次没有执行 `up -d`、重建容器或修改数据库数据；Docker 实际启动状态仍需通过命令检查。
