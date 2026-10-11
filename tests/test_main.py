"""核心逻辑的单元测试。跑：python -m pytest -q"""
import math

import pytest

from main import (
    ANOMALY, MISSING, OK, SEVERE,
    Baseline, Sample,
    compare, format_report, summarize, to_meters,
)


# ---------------- 单位换算 ----------------

def test_单位_毫米转米():
    assert to_meters(1500, "mm") == pytest.approx(1.5)


def test_单位_米不变():
    assert to_meters(1.5, "m") == pytest.approx(1.5)


def test_单位_中英文与大小写都认():
    for u in ("MM", "毫米", "Millimeters"):
        assert to_meters(2000, u) == pytest.approx(2.0)


def test_单位_非法单位报错():
    with pytest.raises(ValueError):
        to_meters(1, "厘米")


# ---------------- 比对与分级 ----------------

def _base(pid, ref, limit, x=0.0, y=0.0):
    return Baseline(pid, x, y, ref, limit, "m")


def _samp(pid, val, x=0.0, y=0.0, unit="m"):
    return Sample(pid, x, y, val, unit)


def test_正常情况_三分级都命中():
    base = {"A": _base("A", 10, 0.5), "B": _base("B", 20, 1.0),
            "C": _base("C", 30, 0.3)}
    res = {"A": _samp("A", 10.2),     # 0.4 倍 → OK
           "B": _samp("B", 24.5),     # 4.5 倍 → SEVERE
           "C": _samp("C", 30.4)}     # 1.33 倍 → ANOMALY
    diffs = {d.part_id: d for d in compare(base, res)}
    assert diffs["A"].status == OK
    assert diffs["B"].status == SEVERE
    assert diffs["C"].status == ANOMALY


def test_边界_空输入():
    diffs = compare({}, {})
    assert diffs == []
    stats = summarize(diffs)
    assert stats["total"] == 0
    assert stats["hit_rate"] == 0.0      # 不能除零崩掉


def test_边界_阈值为零且有偏差():
    base = {"A": _base("A", 10, 0)}
    res = {"A": _samp("A", 10.1)}
    d = compare(base, res)[0]
    assert d.status == SEVERE
    assert math.isinf(d.ratio)


def test_边界_阈值为零且完全一致():
    base = {"A": _base("A", 10, 0)}
    res = {"A": _samp("A", 10)}
    d = compare(base, res)[0]
    assert d.status == OK


def test_边界_解算缺失该部件():
    base = {"A": _base("A", 10, 0.5), "B": _base("B", 20, 1.0)}
    res = {"A": _samp("A", 10.0)}
    diffs = {d.part_id: d for d in compare(base, res)}
    assert diffs["B"].status == MISSING
    assert diffs["B"].value is None


def test_单位混写同一物理点_偏移为零():
    """基准用 mm、解算用 m 描述同一个点，换算后偏移必须是 0。"""
    base = {"A": Baseline("A", 1.5, 2.5, 20.0, 1.0, "mm")}   # 1500mm/2500mm
    res = {"A": Sample("A", 1.5, 2.5, 20.0, "m")}
    assert compare(base, res)[0].coord_offset == pytest.approx(0.0)


def test_命中率统计():
    base = {p: _base(p, 10, 1.0) for p in "ABCD"}
    res = {"A": _samp("A", 10.0),      # OK
           "B": _samp("B", 11.5),      # ANOMALY
           "C": _samp("C", 13.0),      # SEVERE
           "D": _samp("D", 10.0)}      # OK
    stats = summarize(compare(base, res))
    assert stats["total"] == 4
    assert stats["hit"] == 2
    assert stats["hit_rate"] == pytest.approx(50.0)


# ---------------- 报告 ----------------

def test_报告_表头与行数():
    base = {"A": _base("A", 10, 0.5)}
    res = {"A": _samp("A", 10.2)}
    text = format_report(compare(base, res))
    lines = text.strip().splitlines()
    assert lines[0].startswith("part_id,status")
    assert len(lines) == 2               # 表头 + 1 行数据


def test_报告_无穷大比率不写inf():
    base = {"A": _base("A", 10, 0)}
    res = {"A": _samp("A", 10.1)}
    assert "inf" not in format_report(compare(base, res))
