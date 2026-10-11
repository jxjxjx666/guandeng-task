"""<一句话说明这个脚本干什么>"""
from __future__ import annotations

import argparse
import sys


def solve(text: str) -> str:
    """核心逻辑。先跑通，再谈优化。"""
    raise NotImplementedError("待实现")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="<任务说明>")
    ap.add_argument("-i", "--input", help="输入文件路径（不给就读 stdin）")
    ap.add_argument("-o", "--output", help="输出文件路径（不给就打到 stdout）")
    args = ap.parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()

    result = solve(raw)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
