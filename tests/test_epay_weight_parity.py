"""权重分流与老 epay 的**逐点对齐**测试。

参照实现 `_php_random_weight` 是 `epay/includes/lib/Channel.php::random_weight` 的逐行
Python 转写（只把 `mt_rand(1, $weightSum)` 换成外部给定的随机点）。这里用「给定随机点 →
选中的通道」做确定性比对，而不是只比分布比例：分界点、累减顺序、全零返回都要一模一样。
"""
import random

import pytest

from app.models import Channel, ChannelCodeItem
from app.services.channel import (
    _epay_pick,
    _order_code_candidates,
    _weighted_order,
)


def _php_random_weight(items: list[tuple[str, int]], point: int):
    """`Channel::random_weight` 的逐行转写（mt_rand 由调用方给定）。

    ```php
    foreach ($arr as $value) $weightSum += intval($value['weight']);
    if ($weightSum <= 0) return false;
    $randNum = mt_rand(1, $weightSum);
    foreach ($arr as $v) {
        if ($randNum <= $v['weight']) return $v['name'];
        $randNum -= $v['weight'];
    }
    ```
    """
    weight_sum = 0
    for _, weight in items:
        weight_sum += int(weight)
    if weight_sum <= 0:
        return False
    rand_num = point
    for name, weight in items:
        if rand_num <= weight:
            return name
        rand_num -= weight
    return None


def _fixed_randint(point: int):
    """把第一条随机点钉死在 `point` 上（epay 的 mt_rand(1, sum)）。

    后续抽签（不放回时池子变小）返回 1，只保证落在合法区间内；断言点只看首个候选。
    """
    calls = []

    def _randint(a, b):
        calls.append((a, b))
        assert a == 1, f"随机区间下界必须是 1，实际 {a}"
        if len(calls) == 1:
            assert point <= b, f"随机点 {point} 超出区间 [1, {b}]"
            return point
        return 1

    return _randint


# 用户的实际场景：一个编码 3 个通道，权重 50 / 30 / 20，合计 100
THREE = [("A", 50), ("B", 30), ("C", 20)]
NAMES = [name for name, _ in THREE]


def test_epay_pick_matches_php_for_every_point(monkeypatch):
    """1~100 每个随机点选出的通道都与 PHP 参照实现一致（分界点 50 / 80）。"""
    pairs = [(name, weight) for name, weight in THREE]
    for point in range(1, 101):
        monkeypatch.setattr(random, "randint", _fixed_randint(point))
        assert _epay_pick(pairs) == _php_random_weight(THREE, point), point


def test_epay_pick_boundaries_are_percentage_ranges(monkeypatch):
    """权重就是百分比：1~50 → A、51~80 → B、81~100 → C。"""
    pairs = [(name, weight) for name, weight in THREE]
    picked = []
    for point in range(1, 101):
        monkeypatch.setattr(random, "randint", _fixed_randint(point))
        picked.append(_epay_pick(pairs))
    assert picked == ["A"] * 50 + ["B"] * 30 + ["C"] * 20


def test_epay_pick_returns_none_when_all_weights_zero(monkeypatch):
    """全零权重 = epay 的 `return false`（不选通道），不退化成等概率。"""
    monkeypatch.setattr(random, "randint", _fixed_randint(1))
    assert _epay_pick([("A", 0), ("B", 0)]) is None


def test_epay_pick_skips_zero_weight_without_advancing(monkeypatch):
    """0 权重成员永远不会命中，也不会占掉随机区间（A:0 / B:40 / C:60）。"""
    pairs = [("A", 0), ("B", 40), ("C", 60)]
    for point in range(1, 101):
        monkeypatch.setattr(random, "randint", _fixed_randint(point))
        assert _epay_pick(pairs) == _php_random_weight(pairs, point), point
        assert _epay_pick(pairs) != "A"


def test_weighted_order_first_pick_is_the_epay_draw(monkeypatch):
    """不放回抽签的第一个元素就是 epay 那一抽（后续元素不影响首选分布）。"""
    pairs = [(name, weight) for name, weight in THREE]
    for point in range(1, 101):
        monkeypatch.setattr(random, "randint", _fixed_randint(point))
        assert _weighted_order(pairs)[0] == _php_random_weight(THREE, point), point


def test_weighted_order_repeats_single_draws(monkeypatch):
    """后续候选 = 在剩余集合上重抽一次 epay 抽签（剔除后相对权重不变）。

    钉死随机点：100 → C（累减到底），剩余 A:50/B:30 取 80 → B，最后 A。
    """
    pairs = [(name, weight) for name, weight in THREE]
    points = iter([100, 80, 40])
    monkeypatch.setattr(random, "randint", lambda a, b: next(points))
    assert _weighted_order(pairs) == ["C", "B", "A"]


def test_excluded_member_renormalizes_like_epay():
    """某个通道不可用（被剔除）后，剩余通道按比例重归一 —— 与 epay「先剔除再抽签」等价。

    A(50) 被摘掉时，B:C = 30:20，即 60% / 40%。
    """
    pairs = [(name, weight) for name, weight in THREE]
    rounds = 4000
    hits = {"B": 0, "C": 0}
    state = random.getstate()
    random.seed(20261121)
    try:
        for _ in range(rounds):
            for name in _weighted_order(pairs):
                if name != "A":
                    hits[name] += 1
                    break
    finally:
        random.setstate(state)
    share = hits["B"] / rounds
    assert abs(share - 0.6) < 0.03, hits


def _chan(cid: int, sort: int = 0) -> Channel:
    return Channel(
        id=cid, name=f"通道{cid}", plugin_name="epay", payment_type_id=1,
        merchant_rate=1, cost_rate=0, status=1, mode=0, sort=sort,
        min_amount=0, max_amount=0,
    )


def test_code_draw_order_follows_member_table(monkeypatch):
    """抽签累减顺序 = 后台成员表顺序（绑定行 sort），不是 channels.sort。

    配置成「成员表 A/B/C」，但 channels.sort 恰好相反：随机点 1 必须命中成员的
    第一行 A，51 命中 B，81 命中 C —— 运营照着弹窗核对分界点能对上。
    """
    a, b, c = _chan(11, sort=30), _chan(12, sort=20), _chan(13, sort=10)
    item_map = {
        a.id: ChannelCodeItem(code_id=1, channel_id=a.id, weight=50, sort=0),
        b.id: ChannelCodeItem(code_id=1, channel_id=b.id, weight=30, sort=1),
        c.id: ChannelCodeItem(code_id=1, channel_id=c.id, weight=20, sort=2),
    }
    # 传进来的 available 是 channels.sort 顺序（C/B/A），结果不能跟着这个顺序走
    available = [c, b, a]
    for point, expected in [(1, a), (50, a), (51, b), (80, b), (81, c), (100, c)]:
        monkeypatch.setattr(random, "randint", _fixed_randint(point))
        assert _order_code_candidates(available, 2, item_map)[0] is expected, point


def test_code_draw_ignores_zero_weight_members(monkeypatch):
    """权重 0 的成员不在候选里（首选与回退都轮不到），候选只剩正权重成员。"""
    a, b, c = _chan(11), _chan(12), _chan(13)
    item_map = {
        a.id: ChannelCodeItem(code_id=1, channel_id=a.id, weight=0, sort=0),
        b.id: ChannelCodeItem(code_id=1, channel_id=b.id, weight=60, sort=1),
        c.id: ChannelCodeItem(code_id=1, channel_id=c.id, weight=40, sort=2),
    }
    monkeypatch.setattr(random, "randint", _fixed_randint(1))
    assert [ch.id for ch in _order_code_candidates([a, b, c], 2, item_map)] == [b.id, c.id]


@pytest.mark.parametrize("point,expected", [(1, "A"), (50, "A"), (51, "B"), (80, "B"), (81, "C")])
def test_epay_pick_parametrized_points(monkeypatch, point, expected):
    """分界点抽样（与 PHP 参照实现同一张表）。"""
    monkeypatch.setattr(random, "randint", _fixed_randint(point))
    assert _epay_pick([(name, weight) for name, weight in THREE]) == expected
