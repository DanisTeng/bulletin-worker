# Bulletin Worker 安装指南

## 前置条件

- Python 3.8+
- PyInstaller（`pip install pyinstaller`）
- OpenClaw（cron 功能）

## 安装步骤

### 1. 编辑配置

打开 `config.json`，填写你的环境信息：

```json
{
  "superior_name": "Danis",             // 上级（领导）姓名
  "worker_name": "James",               // worker 名称
  "worker_workspace": ".../workspace",  // worker session 的工作区目录
  "board_path": ".../board",            // 留言板目录
  "identity_files": [
    "..."                               // 需加载的身份文件
  ],
  "max_recent_lines": 20
}
```

### 2. 一键安装

```bash
cd bulletin-worker/
./setup.sh
```

安装脚本自动完成：

| 步骤 | 产出 | 说明 |
|------|------|------|
| 1️⃣ 打包 agent 工具 | `$worker_workspace/tools/` | pyinstaller 将 agent_tools/*.py 打成独立 ELF，并渲染 shell wrapper |
| 2️⃣ 渲染 cron prompt | stdout + `$worker_workspace/PROMPT.md` | 以替换 config 变量后的 prompt 文本，供创建 cron 使用 |
| 3️⃣ 渲染 SKILL.md | `$worker_workspace/SKILL.md` | worker session 内的执行流程说明书 |

### 3. 创建 cron 任务

复制上一步输出的 cron prompt，创建 OpenClaw cron job：

```bash
openclaw cron add \
  --name "bulletin-worker" \
  --cron "*/10 * * * *" \
  --session isolated \
  --message "<粘贴上一步的 cron prompt>" \
  --timeout-seconds 180
```

> 提示：cron prompt 也在 `$worker_workspace/PROMPT.md` 文件中。

### 4. 测试

上级留言 → 设置状态为 ACTIVE：

```bash
echo "帮我查一下这个数据" | scripts/bb-leader-post
scripts/bb-set-active
```

等待下一个 cron 周期（或手动触发），然后查看留言板确认：

```bash
scripts/bb-recent
```

## 工作区结构

安装完成后，`$worker_workspace/` 内容如下：

```
worker_workspace/
├── tools/               ← agent 可直接调用的工具
│   ├── bb-get-status    → 读状态
│   ├── bb-set-active    → 设为 ACTIVE
│   ├── bb-set-busy      → 设为 BUSY
│   ├── bb-set-idle      → 设为 IDLE
│   ├── bb-set-mission   → 写任务描述
│   ├── bb-get-mission   → 读当前任务
│   ├── bb-worker-post   → worker 发留言
│   ├── bb-leader-post   → 上级发留言
│   ├── bb-recent        → 最近留言
│   └── bb-history       → 按日期查留言
├── SKILL.md             ← 工作流程说明书
└── PROMPT.md            ← cron prompt
```

## 更新

重新运行 `./setup.sh` 即可增量更新，已有文件不会被删除（pyinstaller 会重新打包）。

## 文件清单

| 路径 | 说明 |
|------|------|
| `config.json` | 用户配置（安装前编辑） |
| `setup.sh` | 一键安装脚本（本指南） |
| `core/` | 渲染模板和脚本 |
| `agent_tools/` | 独立工具源码（.py） |
| `output/` | 渲染中间产物 |
| `scripts/` | 备用 shell 脚本（供手动调试） |
| `tmp/` | 运行时数据（留言板、状态文件） |
