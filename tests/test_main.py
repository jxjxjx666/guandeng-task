"""核心逻辑的单元测试。跑：python -m pytest -q"""
import math

import pytest

from main import (
    ANOMALY, EXTRA, MISSING, OK, SEVERE,
    Baseline, Sample,
    compare, format_report, load_baseline, load_result, summarize, to_meters,
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
    """两边坐标都归一到米之后，同一个物理点的偏移应当是 0。

    注意这条只覆盖 compare() 的算术：Baseline/Sample 是直接构造的，
    x_m 字段本身就已经是米。换算「有没有被调用」由下面的集成测试负责。
    """
    base = {"A": Baseline("A", 1.5, 2.5, 20.0, 1.0, "m")}
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


# ---------------- 读 CSV 的整条链路（走 load_baseline / load_result） ----------------
#
# 这一组是补漏测用的。上面所有用例都是直接构造 Baseline / Sample，
# 而这两个类的 x_m 字段本身就已经是米，于是 to_meters() 从来没被走过。
# 后果：把 load_baseline 里的 to_meters(row["x"], unit) 改成 float(row["x"])，
# 上面那些用例照样全绿，但真实数据会全部算错。下面几条守的就是这条链路。


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_集成_基准库读入时毫米真的被换算(tmp_path):
    p = _write(tmp_path / "baseline.csv",
               "part_id,x,y,unit,ref_value,limit\n"
               "A,1500,2500,mm,20.0,1.0\n")
    base = load_baseline(p)
    assert base["A"].x_m == pytest.approx(1.5)    # 1500mm 必须变成 1.5m
    assert base["A"].y_m == pytest.approx(2.5)


def test_集成_解算输出读入时毫米真的被换算(tmp_path):
    p = _write(tmp_path / "result.csv",
               "part_id,x,y,unit,value\n"
               "A,1500,2500,mm,20.5\n")
    res = load_result(p)
    assert res["A"].x_m == pytest.approx(1.5)
    assert res["A"].y_m == pytest.approx(2.5)


def test_集成_混单位端到端偏移为零(tmp_path):
    """基准用毫米写、解算用米写同一个点，整条链路走完偏移必须是 0。"""
    bp = _write(tmp_path / "b.csv",
                "part_id,x,y,unit,ref_value,limit\nA,1500,2500,mm,20.0,1.0\n")
    rp = _write(tmp_path / "r.csv",
                "part_id,x,y,unit,value\nA,1.5,2.5,m,20.0\n")
    d = compare(load_baseline(bp), load_result(rp))[0]
    assert d.coord_offset == pytest.approx(0.0)


def test_集成_米的写法不被误除(tmp_path):
    """unit=m 的行不能被除以 1000，否则是把对的改错了。"""
    p = _write(tmp_path / "m.csv",
               "part_id,x,y,unit,ref_value,limit\nA,1.5,2.5,m,20.0,1.0\n")
    assert load_baseline(p)["A"].x_m == pytest.approx(1.5)


def test_集成_单位列非法时立刻报错(tmp_path):
    """unit 写了个不认识的词，必须报错，不能静默当成米。"""
    p = _write(tmp_path / "bad.csv",
               "part_id,x,y,unit,ref_value,limit\nA,1500,2500,厘米,20.0,1.0\n")
    with pytest.raises(ValueError):
        load_baseline(p)


# ---------------- 双向比对：越界部件 ----------------

def test_越界_解算多出的部件被报出来():
    base = {"A": _base("A", 10, 0.5)}
    res = {"A": _samp("A", 10.0), "Z": _samp("Z", 3.0)}
    diffs = {d.part_id: d for d in compare(base, res)}
    assert diffs["Z"].status == EXTRA
    assert diffs["Z"].ref_value is None


def test_越界_不计入命中率分母():
    base = {"A": _base("A", 10, 1.0), "B": _base("B", 10, 1.0)}
    res = {"A": _samp("A", 10.0),      # OK
           "B": _samp("B", 11.5),      # ANOMALY
           "Z": _samp("Z", 3.0),       # EXTRA
           "Y": _samp("Y", 4.0)}       # EXTRA
    stats = summarize(compare(base, res))
    assert stats["extra"] == 2
    assert stats["compared"] == 2      # 分母不含越界件
    assert stats["hit"] == 1
    assert stats["hit_rate"] == pytest.approx(50.0)


def test_越界_报告里标出EXTRA行():
    base = {"A": _base("A", 10, 0.5)}
    res = {"A": _samp("A", 10.0), "Z": _samp("Z", 3.0)}
    text = format_report(compare(base, res))
    assert "Z,EXTRA" in text
