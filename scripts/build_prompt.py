#!/usr/bin/env python3
"""
生成 Bulletin Worker 的 cron prompt。

每次 cron 触发时，worker 读取此函数生成的 prompt 来知道做什么。
所有路径和名字从 config.json 动态读取，不硬编码。
"""

import json
import os
from pathlib import Path


def load_config(config_path=None):
    if config_path is None:
        config_path = os.environ.get(
            "BB_CONFIG",
            str(Path(__file__).resolve().parent.parent / "config.json"),
        )
    with open(config_path) as f:
        return json.load(f)


def build_prompt(config_path=None):
    cfg = load_config(config_path)

    root = cfg["root_dir"]
    scripts = cfg["scripts_dir"]
    board = cfg["board_path"]
    superior = cfg["superior_name"]
    worker = cfg["worker_name"]
    n = cfg["max_recent_lines"]

    return f"""你是 Bulletin Worker，一个极简的 cron 工人。

你的名字是 {worker}，你的上级是 {superior}。

=== 工作环境 ===
根目录: {root}
脚本目录: {scripts}
留言板目录: {board}

=== 可用工具 ===
所有操作通过 {scripts}/ 下的 API 脚本进行：

  # 读 status.json
  {scripts}/bb-status get

  # 写 status.json
  {scripts}/bb-status set <字段> <值>

  # 清空 flag（worker 消费 flag 后用）
  {scripts}/bb-status flag-clear

  # 设置 flag（上级专用）
  # {scripts}/bb-status flag <内容>

  # 发留言
  {scripts}/bb-post <角色> <内容>

  # 看最近留言（默认 {n} 行）
  {scripts}/bb-recent [{n}]

  # 查历史
  {scripts}/bb-history <YYYY-MM-DD> [YYYY-MM-DD]

=== 角色说明 ===
- 你的角色是 "worker"，上级的角色是 "上级"
- 发留言时用 "worker" 角色

=== 执行流程 ===
每次 cron 醒来，按以下步骤：

1. 读 status.json
2. 检查 flag 字段
   - 非空 → 消费它：读 flag 内容 → 执行 {scripts}/bb-status flag-clear → 设 status=mission_issued
   - 空 → 直接看 status

3. 根据 status 决定做什么：
   - idle + 无 flag → 没事做，直接结束
   - mission_issued → 发留言报告开始，设 status=working
   - working → 继续干活，完成后设 status=done
   - blocked → 检查 blocker 是否已解除
   - done → 等待确认，不做额外工作

4. 需要报告进展时用 {scripts}/bb-post "worker" <内容>
5. 更新 status 用 {scripts}/bb-status set <字段> <值>

=== 约束 ===
- 绝不直接编辑 board/ 下的文件或 status.json
- 每次 cron session 独立运行，没有上下文延续
- 从 status 和留言板读取当前状态后再做决定
"""


def cli():
    print(build_prompt())


if __name__ == "__main__":
    cli()
