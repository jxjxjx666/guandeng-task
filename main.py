"""装备仿真数据核对工具。

比对本机解算输出与基准规则库中的部件数值，自动识别混用的米/毫米单位，
输出差异报告并持久化到本地文件，同时在终端给出彩色打靶进度条与 ASCII 靶盘。
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import sys
import time
from dataclasses import dataclass

# --------------------------------------------------------------------------
# 一、单位归一化（需求 4）
# --------------------------------------------------------------------------

MM_PER_M = 1000.0

_MM_ALIASES = {"mm", "毫米", "millimeter", "millimeters"}
_M_ALIASES = {"m", "米", "meter", "meters"}


def to_meters(value: float, unit: str) -> float:
    """把带单位的长度换算成米。

    只认米和毫米两种；认不出的单位直接报错，不猜。
    换算基准：1 m = 1000 mm。
    """
    key = (unit or "").strip().lower()
    if key in _MM_ALIASES:
        return value / MM_PER_M
    if key in _M_ALIASES:
        return value
    raise ValueError(f"无法识别的单位：{unit!r}（只支持 m / mm 及其中英文写法）")


# --------------------------------------------------------------------------
# 二、数据读取
# --------------------------------------------------------------------------


@dataclass
class Baseline:
    """基准规则库的一条记录。"""

    part_id: str
    x_m: float          # 归一化到米的坐标
    y_m: float
    ref_value: float    # 基准数值
    limit: float        # 允许的超差阈值
    unit_in: str        # 原始单位，保留用于报告


@dataclass
class Sample:
    """本机解算输出的一条记录。"""

    part_id: str
    x_m: float
    y_m: float
    value: float        # 实测数值
    unit_in: str


def _read_rows(path: str) -> list[dict]:
    """读 CSV，返回 dict 列表。表头大小写与首尾空格不敏感。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"{path} 是空文件或缺少表头")
        rows = []
        for raw in reader:
            rows.append({(k or "").strip().lower(): (v or "").strip()
                         for k, v in raw.items()})
        return rows


def _require(row: dict, cols: list[str], path: str) -> None:
    missing = [c for c in cols if c not in row]
    if missing:
        raise ValueError(f"{path} 缺少必需列：{missing}")


def load_baseline(path: str) -> dict[str, Baseline]:
    """读基准规则库，按 part_id 建立索引。"""
    out: dict[str, Baseline] = {}
    for row in _read_rows(path):
        _require(row, ["part_id", "x", "y", "unit", "ref_value", "limit"], path)
        unit = row["unit"]
        pid = row["part_id"]
        if pid in out:
            raise ValueError(f"基准库中 part_id 重复：{pid}")
        out[pid] = Baseline(
            part_id=pid,
            x_m=to_meters(float(row["x"]), unit),
            y_m=to_meters(float(row["y"]), unit),
            ref_value=float(row["ref_value"]),
            limit=abs(float(row["limit"])),
            unit_in=unit,
        )
    return out


def load_result(path: str) -> dict[str, Sample]:
    """读本机解算输出，按 part_id 建立索引。"""
    out: dict[str, Sample] = {}
    for row in _read_rows(path):
        _require(row, ["part_id", "x", "y", "unit", "value"], path)
        unit = row["unit"]
        pid = row["part_id"]
        if pid in out:
            raise ValueError(f"解算输出中 part_id 重复：{pid}")
        out[pid] = Sample(
            part_id=pid,
            x_m=to_meters(float(row["x"]), unit),
            y_m=to_meters(float(row["y"]), unit),
            value=float(row["value"]),
            unit_in=unit,
        )
    return out


# --------------------------------------------------------------------------
# 三、比对与判定（需求 1、2）
# --------------------------------------------------------------------------

OK = "OK"
ANOMALY = "ANOMALY"
SEVERE = "SEVERE"
MISSING = "MISSING"

SEVERE_RATIO = 2.0   # 超差比 >= 2 倍阈值 → 严重超标


@dataclass
class Diff:
    """单个部件的比对结果。"""

    part_id: str
    status: str
    unit_in: str
    value: float | None
    ref_value: float | None
    diff: float | None
    limit: float
    ratio: float        # |差值| / 阈值，越大越离谱
    coord_offset: float | None   # 坐标偏移(米)，单位换算是否生效看它


def compare(baseline: dict[str, Baseline],
            result: dict[str, Sample]) -> list[Diff]:
    """逐部件比对。

    以基准库为主表：基准库里有、解算输出里没有的，记为 MISSING（缺失）。
    解算输出多出来的部件会被忽略（见 README「已知风险」）。
    """
    diffs: list[Diff] = []

    for pid, base in baseline.items():
        sample = result.get(pid)

        if sample is None:
            diffs.append(Diff(pid, MISSING, base.unit_in, None,
                              base.ref_value, None, base.limit, 0.0, None))
            continue

        delta = sample.value - base.ref_value
        if base.limit > 0:
            ratio = abs(delta) / base.limit
        else:
            # 阈值为 0：只要有一丝偏差就算严重超标，避免除零
            ratio = math.inf if delta != 0 else 0.0

        if ratio >= SEVERE_RATIO:
            status = SEVERE
        elif ratio >= 1.0:
            status = ANOMALY
        else:
            status = OK

        # 坐标偏移：两边都先归一到米再算欧氏距离，
        # 所以「同一物理点用 m 写、另一边用 mm 写」时偏移应为 0
        offset = math.hypot(sample.x_m - base.x_m, sample.y_m - base.y_m)

        diffs.append(Diff(pid, status, sample.unit_in, sample.value,
                          base.ref_value, delta, base.limit, ratio, offset))

    # 固定按 part_id 排序，保证报告可复现
    diffs.sort(key=lambda d: d.part_id)
    return diffs


def summarize(diffs: list[Diff]) -> dict:
    """统计异常命中率与严重超标数量。"""
    total = len(diffs)
    ok = sum(1 for d in diffs if d.status == OK)
    anomaly = sum(1 for d in diffs if d.status == ANOMALY)
    severe = sum(1 for d in diffs if d.status == SEVERE)
    missing = sum(1 for d in diffs if d.status == MISSING)

    # 异常命中率 = 检出异常（含严重超标）的部件数 / 参与比对的部件总数
    hit = anomaly + severe
    return {
        "total": total,
        "ok": ok,
        "anomaly": anomaly,
        "severe": severe,
        "missing": missing,
        "hit": hit,
        "hit_rate": (hit / total * 100.0) if total else 0.0,
    }


# --------------------------------------------------------------------------
# 四、差异报告落盘（需求 2）
# --------------------------------------------------------------------------

_REPORT_COLS = ["part_id", "status", "unit_in", "value", "ref_value",
                "diff", "limit", "exceed_ratio", "coord_offset_m"]


def format_report(diffs: list[Diff]) -> str:
    """生成差异报告的 CSV 文本。"""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_REPORT_COLS)
    for d in diffs:
        writer.writerow([
            d.part_id,
            d.status,
            d.unit_in,
            "" if d.value is None else f"{d.value:g}",
            "" if d.ref_value is None else f"{d.ref_value:g}",
            "" if d.diff is None else f"{d.diff:+g}",
            f"{d.limit:g}",
            "" if math.isinf(d.ratio) else f"{d.ratio:.3f}",
            "" if d.coord_offset is None else f"{d.coord_offset:.6f}",
        ])
    return buf.getvalue()


def write_report(path: str, diffs: list[Diff], stats: dict) -> None:
    """把差异报告写到本地文件（UTF-8），并在结尾附一行汇总。"""
    text = format_report(diffs)
    summary = (f"# 汇总: total={stats['total']} ok={stats['ok']} "
               f"anomaly={stats['anomaly']} severe={stats['severe']} "
               f"missing={stats['missing']} hit_rate={stats['hit_rate']:.1f}%\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.write(summary)


# --------------------------------------------------------------------------
# 五、终端呈现（需求 3）
# --------------------------------------------------------------------------


class Palette:
    """ANSI 配色。非交互终端或 --no-color 时自动降级为无色。"""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def red(self, t: str) -> str:
        return self._wrap("31;1", t)

    def green(self, t: str) -> str:
        return self._wrap("32;1", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33;1", t)

    def cyan(self, t: str) -> str:
        return self._wrap("36", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)


def shooting_bar(hit: int, total: int, color: Palette,
                 width: int = 36, animate: bool = True) -> None:
    """彩色动效打靶进度条：把「异常命中率」画成一根逐渐填满的条。"""
    ratio = (hit / total) if total else 0.0
    frames = 20 if animate else 1

    for i in range(1, frames + 1):
        cur = ratio * i / frames
        filled = int(round(cur * width))
        bar = "█" * filled + "░" * (width - filled)
        pct = f"{cur * 100:5.1f}%"

        if cur >= 0.5:
            colored = color.red(bar)          # 命中率高 → 红色告警
        elif cur > 0:
            colored = color.yellow(bar)
        else:
            colored = color.dim(bar)

        line = (f"  打靶进度 [{colored}] {color.cyan(pct)}  "
                f"命中 {hit}/{total}")
        sys.stdout.write("\r" + line)
        sys.stdout.flush()
        if animate:
            time.sleep(0.035)
    sys.stdout.write("\n")


def render_target(diffs: list[Diff], color: Palette,
                  width: int = 57, rows: int = 25) -> str:
    """ASCII 靶盘：环形靶 + 按超差程度把部件画到不同环上。

    越离谱的部件落点越靠外环，靶心代表与基准完全一致。
    """
    cx, cy = width // 2, rows // 2
    grid = [[" "] * width for _ in range(rows)]

    # 画 4 道同心环（字符单元高约为宽的 2 倍，纵向距离乘 2 才看得出是圆）
    for r in range(rows):
        for c in range(width):
            dc = c - cx
            dr = (r - cy) * 2.0
            d = math.hypot(dc, dr)
            for ring in (6.0, 12.0, 18.0, 24.0):
                if abs(d - ring) < 0.75:
                    grid[r][c] = "·"
                    break

    grid[cy][cx] = "◎"          # 靶心

    # 落点：按超差比决定半径，沿圆周均匀铺开
    n = len(diffs)
    for i, d in enumerate(diffs):
        if n == 1:
            ang = -math.pi / 2
        else:
            ang = 2 * math.pi * i / n - math.pi / 2

        if d.status == MISSING:
            radius = 27.0            # 缺失件甩到靶外
        else:
            radius = min(d.ratio, SEVERE_RATIO) / SEVERE_RATIO * 22.0

        c = int(round(cx + radius * math.cos(ang)))
        r = int(round(cy + radius * math.sin(ang) / 2.0))
        if not (0 <= r < rows and 0 <= c < width):
            continue

        mark = {SEVERE: "✖", ANOMALY: "●", OK: "○", MISSING: "×"}[d.status]
        grid[r][c] = mark

    art = "\n".join("".join(row) for row in grid)
    legend = (f"    {color.cyan('◎')} 靶心(基准一致)   "
              f"{color.green('○')} 正常   "
              f"{color.yellow('●')} 异常   "
              f"{color.red('✖')} 严重超标   "
              f"{color.dim('× 缺失')}")
    return art + "\n" + legend


def print_summary(diffs: list[Diff], stats: dict, color: Palette) -> None:
    """打印汇总与严重超标部件清单。"""
    print()
    print(color.cyan("  ── 核对汇总 ────────────────────────────────"))
    print(f"   参与比对部件   : {stats['total']}")
    print(f"   正常           : {color.green(str(stats['ok']))}")
    print(f"   异常           : {color.yellow(str(stats['anomaly']))}")
    print(f"   严重超标       : {color.red(str(stats['severe']))}")
    if stats["missing"]:
        print(f"   缺失(未解算)   : {color.dim(str(stats['missing']))}")
    print(f"   异常命中率     : {stats['hit_rate']:.1f}%")

    offsets = [d.coord_offset for d in diffs if d.coord_offset is not None]
    if offsets:
        print(f"   坐标换算校验   : 最大偏移 {max(offsets):.6f} m"
              f"（基准与解算用不同单位写同一物理点时，此值应为 0）")

    severe = [d for d in diffs if d.status == SEVERE]
    if severe:
        print()
        print(color.red("  [!] 严重超标部件（超差 >= 2 倍阈值）："))
        for d in severe:
            print(f"     {color.red(d.part_id)}  实测={d.value:g}"
                  f"  基准={d.ref_value:g}  超差={d.ratio:.2f}×阈值")


# --------------------------------------------------------------------------
# 六、入口
# --------------------------------------------------------------------------


def run(baseline_path: str, result_path: str, output_path: str,
        color_enabled: bool, animate: bool, quiet: bool = False) -> dict:
    """完整流程：读 → 换算 → 比对 → 落盘 → 展示。返回统计结果。"""
    color = Palette(color_enabled)

    if not quiet:
        print(color.cyan("  装备仿真数据核对工具"))
        print(f"   基准规则库 : {baseline_path}")
        print(f"   解算输出   : {result_path}")
        print()

    baseline = load_baseline(baseline_path)
    result = load_result(result_path)

    if not quiet:
        mm_base = sum(1 for b in baseline.values()
                      if b.unit_in.lower() in _MM_ALIASES)
        mm_res = sum(1 for s in result.values()
                     if s.unit_in.lower() in _MM_ALIASES)
        print(f"   单位识别：基准库 {mm_base}/{len(baseline)} 行为毫米，"
              f"解算输出 {mm_res}/{len(result)} 行为毫米 → 已统一换算为米")

    diffs = compare(baseline, result)
    stats = summarize(diffs)

    if not quiet:
        print()
        shooting_bar(stats["hit"], stats["total"], color, animate=animate)
        print()
        print(render_target(diffs, color))
        print_summary(diffs, stats, color)

    write_report(output_path, diffs, stats)
    if not quiet:
        print()
        print(f"   差异报告已写入 : {color.cyan(output_path)}")
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="装备仿真数据核对工具：比对本机解算输出与基准规则库")
    ap.add_argument("-b", "--baseline", default="samples/baseline.csv",
                    help="基准规则库 CSV（默认 samples/baseline.csv）")
    ap.add_argument("-i", "--input", default="samples/result.csv",
                    help="本机解算输出 CSV（默认 samples/result.csv）")
    ap.add_argument("-o", "--output", default="diff_report.csv",
                    help="差异报告输出路径（默认 diff_report.csv）")
    ap.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    ap.add_argument("--no-anim", action="store_true", help="关闭进度条动画")
    args = ap.parse_args(argv)

    # 只有真终端才开色/放动画，重定向到文件时自动降级
    is_tty = sys.stdout.isatty()
    color_enabled = (not args.no_color) and is_tty
    animate = (not args.no_anim) and is_tty

    try:
        run(args.baseline, args.input, args.output, color_enabled, animate)
    except FileNotFoundError as e:
        print(f"[ERROR] 找不到输入文件：{e.filename}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"[ERROR] 数据格式有问题：{e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
