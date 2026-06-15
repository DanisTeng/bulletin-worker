#!/usr/bin/env python3
"""
bb_board.py — 留言板读写工具

简洁的纯文本留言板，按日期分文件，只追加不修改。
支持跨日期聚合查询。

路径无关：board_dir 作为唯一必需参数传入。
环境变量无关：不读取任何环境变量。

用法:
  # 发一条留言（内容从 stdin 读取，支持任意多行）
  echo "翻译 /docs/manual.md" | bb_board.py <board_dir> post <发言人>
  cat report.md | bb_board.py <board_dir> post <发言人>
  printf "第一行\n第二行" | bb_board.py <board_dir> post <发言人>

  # 看最近的留言（默认 20 行），支持关键词过滤
  bb_board.py <board_dir> recent [行数] [--grep <关键词>]

  # 按日期查留言
  bb_board.py <board_dir> history <YYYY-MM-DD> [YYYY-MM-DD]

  # 以指定时间为锚点，往前/往后取若干条
  bb_board.py <board_dir> around <YYYY-MM-DDThh:mm> <前N条> <后N条> [--grep <关键词>]

示例:
  echo "翻译 /docs/manual.md" | bb_board.py /tmp/board post Danis
  printf "进度更新\n已完成第1章" | bb_board.py /tmp/board post James
  cat status.txt | bb_board.py /tmp/board post James
  bb_board.py /tmp/board recent                    # 最近 20 行
  bb_board.py /tmp/board recent 50                 # 最近 50 行
  bb_board.py /tmp/board recent 50 --grep "翻译"   # 最近 50 行中只显示含"翻译"的
  bb_board.py /tmp/board history 2026-06-08
  bb_board.py /tmp/board history 2026-06-01 2026-06-08
  bb_board.py /tmp/board around 2026-06-15T14:00 10 5               # 14:00 前10后5条
  bb_board.py /tmp/board around 2026-06-15T14:00 10 5 --grep "卡住"  # 同上，只显示含"卡住"的

留言格式:
  2026-06-08 14:30 [Danis] 翻译 /docs/manual.md
  2026-06-08 14:31 [James] 收到，开始翻译第1章
"""

import json
import sys
from datetime import datetime, timedelta, date as Date
from pathlib import Path

# ── 常量 ───────────────────────────────────────────────────────

_EXIT_OK = 0
_EXIT_ERR = 1


# ── 错误处理 ───────────────────────────────────────────────────


def _help(doc: str):
    """打印帮助文档并退出。"""
    print(doc.strip())
    sys.exit(_EXIT_OK)


def _err(msg: str):
    """打印错误信息并退出。"""
    print(f"❌ {msg}", file=sys.stderr)
    print(f"💡 执行 bb_board.py --help 查看用法", file=sys.stderr)
    sys.exit(_EXIT_ERR)


# ── 行过滤 ───────────────────────────────────────────────────


def _grep_lines(lines: list[str], keyword: str | None) -> list[str]:
    """
    按关键词过滤行。keyword 为 None 或空字符串时不过滤。
    大小写不敏感。
    """
    if not keyword:
        return lines
    kw = keyword.lower()
    return [l for l in lines if kw in l.lower()]


# ── 日期工具 ───────────────────────────────────────────────────


def _parse_date(s: str) -> Date:
    """解析 YYYY-MM-DD 格式日期，非法输入报错退出。"""
    parts = s.split("-")
    if len(parts) != 3:
        _err(f"日期格式错误，应为 YYYY-MM-DD: {s}")
    try:
        return Date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        _err(f"非法日期: {s}")


def _today() -> Date:
    """返回今天日期。"""
    return Date.today()


def _date_range(start: Date, end: Date):
    """生成 [start, end] 闭区间内的日期序列。"""
    delta = end - start
    for i in range(delta.days + 1):
        yield start + timedelta(days=i)


def _validate_board_dir(raw: str) -> Path:
    """校验并返回留言板目录路径。"""
    if not raw:
        _err("留言板路径不能为空")
    return Path(raw)


# ── 文件操作 ───────────────────────────────────────────────────


def _board_file(board_dir: Path, d: Date) -> Path:
    """返回指定日期对应的留言文件路径。"""
    return board_dir / f"{d.isoformat()}.md"


def _ensure_dir(d: Path):
    """确保目录存在，不存在则创建。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        _err(f"无法创建目录 {d}: {e}")


def _read_board_file(fp: Path) -> list[str]:
    """读取留言文件，返回去除尾部空行的行列表。跳过 # 开头的行。"""
    if not fp.exists():
        return []
    try:
        text = fp.read_text()
    except (OSError, PermissionError) as e:
        _err(f"无法读取 {fp}: {e}")

    lines = text.splitlines(keepends=False)
    # 去掉尾部空行
    while lines and lines[-1] == "":
        lines.pop()
    # 过滤 # 开头的元数据行
    return [l for l in lines if not l.startswith("# ")]


# ── 时间戳解析 ───────────────────────────────────────────────


def _parse_datetime(s: str) -> datetime:
    """
    解析 YYYY-MM-DDThh:mm 格式时间戳。
    未指定分钟时默认 :00，未指定日期时默认今天。
    """
    # 支持 YYYY-MM-DDThh:mm 和 YYYY-MM-DD hh:mm
    s = s.replace(" ", "T")
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M")
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H")
    except ValueError:
        pass
    _err(f"时间格式错误，应为 YYYY-MM-DDThh:mm，收到: {s}")


def _parse_line_ts(line: str) -> datetime | None:
    """
    尝试从留言行首解析时间戳。
    格式: YYYY-MM-DD HH:MM [Speaker] ...
    解析失败返回 None。
    """
    if len(line) < 16:
        return None
    try:
        return datetime.strptime(line[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _pick_lines_around(
    accumulated: list[tuple[str, list[str]]],
    anchor: datetime,
    before: int,
    after: int,
    grep: str | None,
) -> list[str]:
    """
    在聚合留言中，以 anchor 为锚点，取前 before 条、后 after 条。
    支持可选 grep 过滤（先过滤再取范围）。
    返回按时间顺序排列的行列表。
    """
    # 先展平成带解析时间戳的行
    all_timed: list[tuple[datetime, str]] = []
    for _, lines in accumulated:
        for line in lines:
            ts = _parse_line_ts(line)
            if ts is not None:
                all_timed.append((ts, line))

    if not all_timed:
        return []

    # 二分查找第一个 >= anchor 的位置
    target = anchor.timestamp()
    lo, hi = 0, len(all_timed)
    while lo < hi:
        mid = (lo + hi) // 2
        if all_timed[mid][0].timestamp() < target:
            lo = mid + 1
        else:
            hi = mid

    idx = lo
    start = max(0, idx - before)
    end = min(len(all_timed), idx + after)

    selected = [line for _, line in all_timed[start:end]]
    return _grep_lines(selected, grep)


# ── 核心功能 ───────────────────────────────────────────────────


def post(board_dir: Path, speaker: str, content: str) -> str:
    """
    发一条留言。自动加时间戳和发言人，追加到今日文件。
    支持多行内容：续行自动对齐到时间戳位置。
    返回首行文本（含时间戳标记）。
    """
    if not speaker:
        _err("发言人不能为空")
    if not content:
        _err("留言内容不能为空")

    _ensure_dir(board_dir)

    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M")
    prefix = f"{ts} [{speaker}] "
    indent = " " * len(prefix)

    # 支持 \\n 字面量转真实换行（常见于 shell 字符串传参）
    content = content.replace("\\n", "\n")
    lines = content.split("\n")

    fp = _board_file(board_dir, now.date())
    try:
        with open(fp, "a") as f:
            for i, line in enumerate(lines):
                if i == 0:
                    f.write(f"{prefix}{line}\n")
                else:
                    f.write(f"{indent}{line}\n")
    except (OSError, PermissionError) as e:
        _err(f"写入留言失败 {fp}: {e}")

    return prefix + lines[0]


def _read_stdin() -> str:
    """
    从 stdin 读取全部内容，去掉末尾一个换行（如有）。
    如果 stdin 是空（无数据），返回空字符串。
    """
    try:
        content = sys.stdin.read()
    except (OSError, EOFError):
        return ""
    # 去掉末尾一个换行（trailing newline），保留中间的
    if content.endswith("\n"):
        content = content[:-1]
    return content


def _accumulate(board_dir: Path) -> list[tuple[str, list[str]]]:
    """
    跨日期聚合留言。
    返回 [(date_str, [line1, line2, ...]), ...]，按日期升序排列。
    """
    results: list[tuple[str, list[str]]] = []
    board_path = Path(board_dir)

    if not board_path.is_dir():
        _err(f"留言板目录不存在: {board_dir}")

    # 收集所有 YYYY-MM-DD.md 文件
    glob_pattern = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"
    files = sorted(board_path.glob(glob_pattern))

    for fp in files:
        lines = _read_board_file(fp)
        if not lines:
            continue
        date_str = fp.stem  # "2026-06-08"
        results.append((date_str, lines))

    return results


def recent(board_dir: Path, n: int = 20) -> list[str]:
    """
    读最近的 N 条留言，跨多个旧文件聚合。
    返回按时间顺序排列的行列表（最新的在末尾）。
    """
    if n < 1:
        return []

    accumulated = _accumulate(board_dir)
    # accumulated 已按日期升序排列

    # 从后往前收集 N 行
    all_lines: list[str] = []
    for _, lines in reversed(accumulated):
        all_lines = lines + all_lines
        if len(all_lines) >= n:
            break

    return all_lines[-n:]


def history(
    board_dir: Path, start: Date, end: Date | None = None
) -> list[str]:
    """
    按日期范围查留言。
    返回结果列表，每段区间之间以 "--- YYYY-MM-DD ---" 分隔。
    """
    if end is None:
        end = start

    if start > end:
        _err(f"起始日期 {start} 不能晚于结束日期 {end}")

    results: list[str] = []
    for d in _date_range(start, end):
        fp = _board_file(board_dir, d)
        lines = _read_board_file(fp)
        if not lines:
            continue
        results.append(f"--- {d.isoformat()} ---")
        results.extend(lines)

    return results


# ── CLI 参数解析 ───────────────────────────────────────────────


def _parse_subcommand(argv: list[str], i: int, name: str):
    """
    解析子命令及其参数。
    返回 (path, subcommand, args)，其中 args 为子命令的剩余参数列表。
    若 path 为空（命令行只有子命令），返回 None。
    """
    n = len(argv)
    # argv[0] 是脚本名
    # 期望: [script] <board_dir> <subcmd> [args...]

    if i >= n:
        return None, name, []

    # 检查 argv[i] 是否是保留字（子命令名）
    subcommands = {"post", "recent", "history", "around"}
    if argv[i] in subcommands:
        # 没有 board_dir
        _err("缺少 <board_dir> 参数")

    # 这是 board_dir
    path = _validate_board_dir(argv[i])
    i += 1

    if i >= n:
        _err("缺少子命令 (post / recent / history / around)")

    subcmd = argv[i]
    if subcmd not in subcommands:
        _err(f"未知子命令: {subcmd}，支持: {', '.join(sorted(subcommands))}")

    i += 1
    args = argv[i:]
    return path, subcmd, args


# ── 各子命令处理 ───────────────────────────────────────────────


def _cmd_post(path: Path, args: list[str]):
    """处理 post 子命令：发留言。内容优先从 argv 取，否则从 stdin 读。"""
    if len(args) < 1:
        _err("用法: echo <内容> | bb_board.py <board_dir> post <发言人>\n       bb_board.py <board_dir> post <发言人> <内容>")

    speaker = args[0]

    if len(args) >= 2:
        # argv 传参模式：后续所有参数拼接为一个字符串，支持 \n 转义
        content = " ".join(args[1:])
        content = content.replace("\\n", "\n")
    else:
        # stdin 管道模式
        content = _read_stdin()
        if not content:
            _err("内容不能为空，请通过 stdin 或 argv 传入")

    first_line = post(path, speaker, content)
    print(first_line)


def _pop_grep(args: list[str]) -> tuple[list[str], str | None]:
    """
    从参数列表中提取 --grep <关键词>，返回 (剩余参数, 关键词)。
    """
    grep = None
    remaining = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a == "--grep":
            if i + 1 < len(args):
                grep = args[i + 1]
                skip_next = True
            else:
                _err("--grep 后面需要跟关键词")
        else:
            remaining.append(a)
    return remaining, grep


def _cmd_recent(path: Path, args: list[str]):
    """处理 recent 子命令：看最近留言。"""
    args, grep = _pop_grep(args)

    n = 20
    if args:
        try:
            n = int(args[0])
        except ValueError:
            _err(f"行数必须为整数: {args[0]}")
        if n < 1:
            _err("行数必须大于 0")

    lines = recent(path, n)
    lines = _grep_lines(lines, grep)
    if not lines:
        return
    for line in lines:
        print(line)


def _cmd_around(path: Path, args: list[str]):
    """处理 around 子命令：以指定时间为锚点查留言。"""
    args, grep = _pop_grep(args)

    if len(args) < 3:
        _err("用法: bb_board.py <board_dir> around <YYYY-MM-DDThh:mm> <前N条> <后N条> [--grep <关键词>]")

    anchor = _parse_datetime(args[0])

    try:
        before = int(args[1])
    except ValueError:
        _err(f"向前条数必须为整数: {args[1]}")
    if before < 0:
        _err("向前条数不能为负数")

    try:
        after = int(args[2])
    except ValueError:
        _err(f"向后条数必须为整数: {args[2]}")
    if after < 0:
        _err("向后条数不能为负数")

    accumulated = _accumulate(path)
    lines = _pick_lines_around(accumulated, anchor, before, after, grep)
    if not lines:
        return
    for line in lines:
        print(line)


def _cmd_history(path: Path, args: list[str]):
    """处理 history 子命令：按日期查留言。"""
    if len(args) < 1:
        _err("用法: bb_board.py <board_dir> history <YYYY-MM-DD> [YYYY-MM-DD]")

    start = _parse_date(args[0])
    end = _parse_date(args[1]) if len(args) > 1 else None

    lines = history(path, start, end)
    if not lines:
        return
    for line in lines:
        print(line)


# ── 主入口 ─────────────────────────────────────────────────────


def main():
    argv = sys.argv

    if len(argv) == 1:
        _help(__doc__)

    # 支持 --help 和 --version
    if argv[1] == "--help" or argv[1] == "-h":
        _help(__doc__)
    if argv[1] == "--version" or argv[1] == "-V":
        print("bb_board.py v1.0")
        sys.exit(_EXIT_OK)

    path, subcmd, args = _parse_subcommand(argv, 1, "bb_board")
    # path 一定非 None，因为 _parse_subcommand 遇到无 path 会 _err

    handlers = {
        "post": _cmd_post,
        "recent": _cmd_recent,
        "history": _cmd_history,
        "around": _cmd_around,
    }

    handler = handlers.get(subcmd)
    if handler:
        handler(path, args)
    else:
        _err(f"未知子命令: {subcmd}")


if __name__ == "__main__":
    main()
