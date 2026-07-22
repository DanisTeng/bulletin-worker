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

  # 看最近的留言（默认 20 条），支持关键词过滤
  bb_board.py <board_dir> recent [条数] [--grep <关键词>]

  # 按日期查留言
  bb_board.py <board_dir> history <YYYY-MM-DD> [YYYY-MM-DD]

  # 以指定时间为锚点，往前/往后取若干条
  bb_board.py <board_dir> around <YYYY-MM-DDThh:mm> <前N条> <后N条> [--grep <关键词>]

  # 查全局留言 index（从 0 开始，每条留言 +1，到 INT32_MAX 溢出归零）
  bb_board.py <board_dir> index

  # 清空留言板：删除所有留言文件，重置 index 和状态
  bb_board.py <board_dir> clear

示例:
  echo "翻译 /docs/manual.md" | bb_board.py /tmp/board post Danis
  printf "进度更新\n已完成第1章" | bb_board.py /tmp/board post James
  cat status.txt | bb_board.py /tmp/board post James
  bb_board.py /tmp/board recent                    # 最近 20 条留言（含续行）
  bb_board.py /tmp/board recent 50                 # 最近 50 条留言
  bb_board.py /tmp/board recent 50 --grep "翻译"   # 最近 50 条中只显示含"翻译"的
  bb_board.py /tmp/board history 2026-06-08
  bb_board.py /tmp/board history 2026-06-01 2026-06-08
  bb_board.py /tmp/board around 2026-06-15T14:00 10 5               # 14:00 前10后5条
  bb_board.py /tmp/board around 2026-06-15T14:00 10 5 --grep "卡住"  # 同上，只显示含"卡住"的

留言格式:
  2026-06-08 14:30 [Danis] 翻译 /docs/manual.md
  2026-06-08 14:31 [James] 收到，开始翻译第1章
"""

import re
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


# ── 留言文件扫描 ──────────────────────────────────────────────


def _sorted_board_files(board_dir: Path) -> list[Path]:
    """
    返回按文件名升序排列的留言文件列表。
    """
    board_path = Path(board_dir)
    if not board_path.is_dir():
        _err(f"留言板目录不存在: {board_dir}")
    glob_pattern = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"
    return sorted(board_path.glob(glob_pattern))


def _load_timed_lines(fp: Path) -> list[tuple[datetime, str]]:
    """读取单个留言文件，返回带解析时间戳的行列表。跳过时间戳解析失败的行。"""
    result: list[tuple[datetime, str]] = []
    for line in _read_board_file(fp):
        ts = _parse_line_ts(line)
        if ts is not None:
            result.append((ts, line))
    return result


# ── Around 子命令：锚点定位 + 螺旋扩张 ─────────────────────────


def _find_anchor_pivot(lines: list[tuple[datetime, str]],
                       anchor: datetime) -> int:
    """
    二分查找 anchor 在 lines 中的位置，返回第一个 >= anchor 的索引。
    lines 为空时返回 0。如果所有行都早于 anchor，返回 len(lines)。
    """
    if not lines:
        return 0
    target = anchor.timestamp()
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi) // 2
        if lines[mid][0].timestamp() < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


# ── 中间结果包装 ──────────────────────────────────────────────


class _Collected:
    """从锚点收集到的中间结果：行列表 + 已收集前/后计数。"""
    __slots__ = ("all_timed", "collected_before", "collected_after")

    def __init__(self, all_timed: list, collected_before: int,
                 collected_after: int):
        self.all_timed = all_timed
        self.collected_before = collected_before
        self.collected_after = collected_after


def _collect_from_pivot(
    lines: list[tuple[datetime, str]],
    pivot: int,
    before: int,
    after: int,
) -> _Collected | None:
    """
    从锚点行向两侧取 before/after 条。
    锚点行始终包含在结果中。
    pivot 超出 lines 范围时返回 None。
    """
    if pivot >= len(lines):
        return None

    before_start = max(0, pivot - before)
    before_lines = lines[before_start:pivot]
    collected_before = len(before_lines)

    after_end = min(len(lines), pivot + 1 + after)
    after_lines = lines[pivot:after_end]
    collected_after = len(after_lines)

    return _Collected(before_lines + after_lines, collected_before,
                      collected_after)


def _collect_tail(lines: list[tuple[datetime, str]],
                  before: int) -> _Collected:
    """从文件末尾取 before 条（锚点晚于所有行时用）。"""
    take = min(before, len(lines))
    return _Collected(lines[-take:], take, 0)


def _collect_head(lines: list[tuple[datetime, str]],
                  after: int) -> _Collected:
    """从文件开头取 after 条（锚点早于所有行时用）。"""
    take = min(after, len(lines))
    return _Collected(lines[:take], 0, take)


def _collect_lines_around(
    board_dir: Path,
    anchor: datetime,
    before: int,
    after: int,
    grep: str | None,
) -> list[str]:
    """
    以 anchor 为锚点，取前 before 条、后 after 条。
    以锚点日期为中心向外扩文件，只读必要文件，直到凑够想要的条数。
    支持可选 grep 过滤。返回按时间顺序排列的行列表。
    """
    files = _sorted_board_files(board_dir)
    if not files:
        return []

    # 找到锚点日期在文件列表中的位置
    anchor_date_str = anchor.strftime("%Y-%m-%d")
    anchor_idx = 0
    for i, fp in enumerate(files):
        if fp.stem >= anchor_date_str:
            anchor_idx = i
            break
    else:
        anchor_idx = len(files) - 1

    # 读锚点文件，找到锚点行位置
    anchor_lines = _load_timed_lines(files[anchor_idx])
    pivot = _find_anchor_pivot(anchor_lines, anchor)

    # 从锚点文件取初始行
    if anchor_lines and pivot < len(anchor_lines):
        # 正常情况：锚点行存在，从它向两侧取
        collected = _collect_from_pivot(anchor_lines, pivot, before, after)
    elif before > 0:
        # 锚点晚于所有行：从最后一个文件的末尾取 before 条
        collected = _collect_tail(anchor_lines, before)
    else:
        # 锚点早于所有行且 before=0：从第一个文件开头取 after 条
        collected = _collect_head(anchor_lines, after)

    if not collected:
        return []

    # 向前扩文件（不够 before 条时）
    left_idx = anchor_idx - 1
    while collected.collected_before < before and left_idx >= 0:
        lines = _load_timed_lines(files[left_idx])
        take = min(before - collected.collected_before, len(lines))
        collected.all_timed = lines[-take:] + collected.all_timed
        collected.collected_before += take
        left_idx -= 1

    # 向后扩文件（不够 after 条时）
    right_idx = anchor_idx + 1
    while collected.collected_after < after and right_idx < len(files):
        lines = _load_timed_lines(files[right_idx])
        take = min(after - collected.collected_after, len(lines))
        collected.all_timed = collected.all_timed + lines[:take]
        collected.collected_after += take
        right_idx += 1

    selected = [line for _, line in collected.all_timed]
    return _grep_lines(selected, grep)


# ── 全局留言 index ────────────────────────────────────────────────

_INDEX_FILE = "index.json"
_INDEX_MAX = 2147483647  # INT32_MAX
_INDEX_TAG_RE = re.compile(r"\(#(\d+)\)\s*")
_SUBCOMMANDS = {"post", "recent", "history", "around", "index", "get", "clear"}


def _index_path(board_dir: Path) -> Path:
    return board_dir / _INDEX_FILE


def _read_index(board_dir: Path) -> int:
    """读取当前留言 index，文件不存在时返回 0。"""
    fp = _index_path(board_dir)
    if not fp.exists():
        return 0
    try:
        import json
        data = json.loads(fp.read_text())
        return data.get("last_index", 0)
    except (json.JSONDecodeError, OSError, PermissionError):
        return 0


def _write_index(board_dir: Path, value: int):
    """写入留言 index，超过 _INDEX_MAX - 1 后回 0。"""
    if value >= _INDEX_MAX:
        value = 0
    try:
        import json
        fp = _index_path(board_dir)
        fp.write_text(json.dumps({"last_index": value}, ensure_ascii=False, indent=2))
    except (OSError, PermissionError) as e:
        _err(f"写入 index 失败: {e}")


def _next_index(board_dir: Path) -> int:
    """递增留言 index 并写回文件，返回新值。"""
    cur = _read_index(board_dir)
    new_val = cur + 1
    if new_val >= _INDEX_MAX:
        new_val = 0
    _write_index(board_dir, new_val)
    return new_val


def cmd_index(board_dir: Path) -> int:
    """查询当前留言 index。"""
    return _read_index(board_dir)


def cmd_clear(board_dir: Path):
    """清空留言板：删除所有日期留言文件，重置 index。
    status 重置由调用方（wrapper）负责，通过 bb-status 处理。
    """
    # 删除所有 YYYY-MM-DD.md 文件
    removed = 0
    for fp in _sorted_board_files(board_dir):
        try:
            fp.unlink()
            removed += 1
        except (OSError, PermissionError) as e:
            print(f"⚠️  无法删除 {fp}: {e}", file=sys.stderr)

    # 重置 index
    _write_index(board_dir, 0)

    print(f"✅ 已清空留言板，删除 {removed} 个留言文件")
    print(f"💡 提示：如需重置状态，请执行 bb-set-idle")


# ── 核心功能 ───────────────────────────────────────────────────


def post(board_dir: Path, speaker: str, content: str) -> str:
    """
    发一条留言。自动加时间戳、index 标记 (#N) 和发言人，追加到今日文件。
    支持多行内容：续行自动对齐到时间戳位置。
    返回首行文本（含时间戳标记和 index）。
    """
    if not speaker:
        _err("发言人不能为空")
    if not content:
        _err("留言内容不能为空")

    _ensure_dir(board_dir)

    # 先递增 index，获取新留言的编号
    idx = _next_index(board_dir)

    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M")
    tag = f"(#{idx})"
    prefix = f"{ts} [{speaker}] {tag} "
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


def recent(board_dir: Path, n: int = 20) -> list[str]:
    """
    读最近的 N 条留言，跨多个旧文件聚合。
    计数方式：N 指留言条数（有时间戳的行），续行不计入。
    从最新的文件开始倒着读，凑够 N 条留言即止。
    返回按时间顺序排列的行列表（最新的在末尾）。
    如果留言总数不足 N，则返回全部留言。
    """
    if n < 1:
        return []

    files = _sorted_board_files(board_dir)

    # 从最新的文件倒序读，收集全部原始行 + 计数 timed_lines
    all_lines: list[str] = []
    ts_seen = 0
    for fp in reversed(files):
        raw_lines = _read_board_file(fp)
        if not raw_lines:
            continue
        all_lines = raw_lines + all_lines
        for line in raw_lines:
            if _parse_line_ts(line) is not None:
                ts_seen += 1
        if ts_seen >= n:
            break

    if not all_lines:
        return []

    # 从末尾倒着数，确定最后 n 条留言的起始位置
    cut = 0
    ts_found = 0
    for i in range(len(all_lines) - 1, -1, -1):
        if _parse_line_ts(all_lines[i]) is not None:
            ts_found += 1
            if ts_found == n:
                cut = i
                break

    return all_lines[cut:]


def history(
    board_dir: Path, start: Date, end: Date | None = None
) -> list[str]:
    """
    按日期范围查留言。
    返回结果列表，每段区间之间以 "--- YYYY-MM-DD ---" 分隔。
    单次查询最多 730 天（约 2 年），避免意外大范围遍历。
    """
    if end is None:
        end = start

    if start > end:
        _err(f"起始日期 {start} 不能晚于结束日期 {end}")

    delta = (end - start).days
    if delta > 730:
        _err(f"日期范围过大（{delta} 天），单次查询最多 730 天，建议缩小范围")

    results: list[str] = []
    for d in _date_range(start, end):
        fp = _board_file(board_dir, d)
        lines = _read_board_file(fp)
        if not lines:
            continue
        results.append(f"--- {d.isoformat()} ---")
        results.extend(lines)

    return results


# ── 按 index 获取留言 ────────────────────────────────────────────


def _parse_line_index(line: str) -> int | None:
    """
    从留言行中解析 (#N) 标记，返回 N。
    不匹配时返回 None。
    """
    m = _INDEX_TAG_RE.search(line)
    if m:
        return int(m.group(1))
    return None


def _file_index_range(lines: list[str]) -> tuple[int | None, int | None]:
    """
    扫描文件的所有行，返回 (min_index, max_index)。
    如果文件为空或没有任何 (#N) 标记，返回 (None, None)。
    """
    indices = []
    for line in lines:
        idx = _parse_line_index(line)
        if idx is not None:
            indices.append(idx)
    if not indices:
        return None, None
    return min(indices), max(indices)


def _get_message_by_index(board_dir: Path, target: int) -> list[str]:
    """
    按 index 获取完整留言（含续行）。

    搜索策略：
      1. 倒序扫描留言文件列表（最新文件优先）。
      2. 对每个文件，先看首尾 index 范围能快速排除目标不在此文件。
      3. 锁定文件后逐行扫描 (#N) 标记，找到后取该行及后续续行。

    找不到时返回空列表。
    """
    files = _sorted_board_files(board_dir)
    if not files:
        return []

    # 倒序扫文件，最新文件优先
    for fp in reversed(files):
        raw_lines = _read_board_file(fp)
        if not raw_lines:
            continue

        # 快速排除：看文件首尾 index 范围
        # 只扫有 (#N) 标记的行，不再扫全部 timed_lines
        file_min, file_max = _file_index_range(raw_lines)
        if file_min is None:
            continue  # 老文件没有 (#N) 标记，跳过

        if target < file_min or target > file_max:
            continue  # 目标不在此文件内

        # 文件内逐行搜
        found_start = -1
        for i, line in enumerate(raw_lines):
            idx = _parse_line_index(line)
            if idx is not None and idx == target:
                found_start = i
                break

        if found_start < 0:
            continue  # 理论上不应发生，防御

        # 取该行及其后续续行（直到下一个有 (#N) 标记的行或文件尾）
        result_lines = [raw_lines[found_start]]
        for j in range(found_start + 1, len(raw_lines)):
            if _parse_line_index(raw_lines[j]) is not None:
                break
            result_lines.append(raw_lines[j])

        return result_lines

    return []


def cmd_get(board_dir: Path, index: int) -> list[str]:
    """按 index 获取留言。找不到返回空列表。"""
    if index < 0 or index >= _INDEX_MAX:
        return []
    return _get_message_by_index(board_dir, index)


# ── CLI 参数解析 ───────────────────────────────────────────────


def _parse_subcommand(argv: list[str], i: int, name: str):
    """
    解析子命令及其参数。
    返回 (path, subcommand, args)，其中 args 为子命令的剩余参数列表。
    """
    n = len(argv)

    if i >= n:
        return None, name, []

    # 检查 argv[i] 是否是保留字（子命令名）
    if argv[i] in _SUBCOMMANDS:
        _err("缺少 <board_dir> 参数")

    # 这是 board_dir
    path = _validate_board_dir(argv[i])
    i += 1

    if i >= n:
        _err("缺少子命令 (post / recent / history / around / index / get / clear)")

    subcmd = argv[i]
    if subcmd not in _SUBCOMMANDS:
        _err(f"未知子命令: {subcmd}，支持: {', '.join(sorted(_SUBCOMMANDS))}")

    i += 1
    args = argv[i:]
    return path, subcmd, args


# ── 各子命令处理 ───────────────────────────────────────────────


def _cmd_post(path: Path, args: list[str]):
    """处理 post 子命令：发留言。支持 --prefix 在内容前加标记。

    参数格式：
      bb_board.py <board_dir> post [--prefix <标记>] <发言人> [<内容>]
    """
    prefix = None
    remaining = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a == "--prefix":
            if i + 1 < len(args):
                prefix = args[i + 1]
                skip_next = True
            else:
                _err("--prefix 后面需要跟标记文字")
        else:
            remaining.append(a)

    if len(remaining) < 1:
        _err("用法: echo <内容> | bb_board.py <board_dir> post [--prefix <标记>] <发言人>\n"
             "       bb_board.py <board_dir> post [--prefix <标记>] <发言人> <内容>")

    speaker = remaining[0]

    if len(remaining) >= 2:
        content = " ".join(remaining[1:])
        content = content.replace("\\n", "\n")
    else:
        content = _read_stdin()
        if not content:
            _err("内容不能为空，请通过 stdin 或 argv 传入")

    if prefix:
        content = f"{prefix} {content}"

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
            _err(f"条数必须为整数: {args[0]}")
        if n < 1:
            _err("条数必须大于 0")

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
        _err("用法: bb_board.py <board_dir> around "
             "<YYYY-MM-DDThh:mm> <前N条> <后N条> [--grep <关键词>]")

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

    lines = _collect_lines_around(path, anchor, before, after, grep)
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


def _cmd_index(path: Path, args: list[str]):
    """处理 index 子命令：查全局留言 index。"""
    n = cmd_index(path)
    print(n)


def _cmd_get(path: Path, args: list[str]):
    """处理 get 子命令：按 index 获取留言。

    用法: bb_board.py <board_dir> get <index>
    """
    if len(args) < 1:
        _err("用法: bb_board.py <board_dir> get <index>")

    try:
        idx = int(args[0])
    except ValueError:
        _err(f"index 必须为整数: {args[0]}")

    lines = cmd_get(path, idx)
    if not lines:
        return  # 找不到就输出空
    for line in lines:
        print(line)


def _cmd_clear(path: Path, args: list[str]):
    """处理 clear 子命令：清空留言板。"""
    cmd_clear(path)


# ── 主入口 ─────────────────────────────────────────────────────


def main():
    argv = sys.argv

    if len(argv) == 1:
        _help(__doc__)

    if argv[1] == "--help" or argv[1] == "-h":
        _help(__doc__)
    if argv[1] == "--version" or argv[1] == "-V":
        print("bb_board.py v1.0")
        sys.exit(_EXIT_OK)

    path, subcmd, args = _parse_subcommand(argv, 1, "bb_board")

    handlers = {
        "post": _cmd_post,
        "recent": _cmd_recent,
        "history": _cmd_history,
        "around": _cmd_around,
        "index": _cmd_index,
        "get": _cmd_get,
        "clear": _cmd_clear,
    }

    handler = handlers.get(subcmd)
    if handler:
        handler(path, args)
    else:
        _err(f"未知子命令: {subcmd}")


if __name__ == "__main__":
    main()
