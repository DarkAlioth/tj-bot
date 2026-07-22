from tj_bot.db.filters import GB, ResultFilters


def test_code_round_trip() -> None:
    f = ResultFilters(seeders=2, size=3, date=1)
    assert f.to_code() == "231"
    assert ResultFilters.from_code("231") == f


def test_from_code_invalid_defaults_to_empty() -> None:
    assert ResultFilters.from_code("xyz") == ResultFilters()
    assert ResultFilters.from_code("12") == ResultFilters()
    assert not ResultFilters.from_code("000").is_active


def test_is_active() -> None:
    assert ResultFilters(seeders=1).is_active
    assert ResultFilters(date=2).is_active
    assert not ResultFilters().is_active


def test_cycled_wraps() -> None:
    f = ResultFilters()
    assert f.cycled("seeders").seeders == 1
    # seeders has 4 buckets: 0->1->2->3->0
    assert ResultFilters(seeders=3).cycled("seeders").seeders == 0
    # size has 5 buckets
    assert ResultFilters(size=4).cycled("size").size == 0


def test_conditions_built_for_active_filters() -> None:
    assert ResultFilters().conditions() == []
    conds = ResultFilters(seeders=2, size=2, date=1).conditions()
    # seeders>=10, size in [1GB,5GB) -> 2 conds, date>=cutoff -> 1 = 4 total
    assert len(conds) == 4


def test_size_bucket_open_ended() -> None:
    # bucket 4 = >20GB: only a lower bound, no upper
    conds = ResultFilters(size=4).conditions()
    assert len(conds) == 1  # only >= 20GB
    assert GB == 1024 * 1024 * 1024


def test_summary_readable() -> None:
    assert ResultFilters().summary() == "не заданы"
    s = ResultFilters(seeders=1, size=2).summary()
    assert "сиды" in s and "размер" in s
