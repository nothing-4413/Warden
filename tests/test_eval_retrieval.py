"""评测脚本里的纯计算部分：Hit@K 曲线（不联网、不加载模型）。

曲线是从同一份排序里数出来的，所以它必须和「直接按 k 检索」得到同样的数——
这里的用例就是钉住这件事，顺带钉住未召回（rank=0）不计入任何截断点。
"""

from scripts.eval_retrieval import hit_at, hit_curve


def test_hit_at_counts_ranks_within_cutoff() -> None:
    ranks = [1, 0, 3, 5, 4]

    assert hit_at(ranks, 1) == 1 / 5  # 只有 rank 1
    assert hit_at(ranks, 3) == 2 / 5  # rank 1、3
    assert hit_at(ranks, 4) == 3 / 5  # 再加 rank 4
    assert hit_at(ranks, 5) == 4 / 5  # rank 5 进来，未召回的那个仍然不算
    assert hit_at(ranks, 99) == 4 / 5


def test_hit_at_handles_empty_ranks() -> None:
    assert hit_at([], 4) == 0.0


def test_hit_curve_is_monotonic_and_keyed_by_cutoff() -> None:
    ranks = [1, 2, 0, 7, 3]

    curve = hit_curve(ranks, [1, 3, 4, 8])

    assert curve == {"1": 1 / 5, "3": 3 / 5, "4": 3 / 5, "8": 4 / 5}
    values = [curve[str(c)] for c in (1, 3, 4, 8)]
    assert values == sorted(values), "截断点越大，命中率只能持平或上升"
