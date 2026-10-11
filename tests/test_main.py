import pytest

from main import solve


def test_正常情况():
    assert solve("示例输入") == "示例输出"


@pytest.mark.skip(reason="先照着任务书补边界用例")
def test_边界_空输入():
    assert solve("") == ""
