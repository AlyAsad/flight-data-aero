from app.uk_groups import load_groups, pack_batches


def test_default_config():
    groups = load_groups()
    enabled = [g for g in groups if g.enabled]
    assert len(enabled) == 8
    assert sum(len(g.airports) for g in enabled) == 11
    assert {g.id for g in groups if not g.enabled} == {"LPL", "EMA"}
    assert "SHF" not in {g.id for g in groups}


def test_default_groups_pack_into_two_batches():
    enabled = [g for g in load_groups() if g.enabled]
    batches = pack_batches(enabled, 7)
    assert len(batches) == 2
    for b in batches:
        assert sum(len(g.airports) for g in b) <= 7
    # London is never split
    assert sum(1 for b in batches for g in b if g.id == "LON") == 1


def test_more_groups_need_three_batches_at_small_size():
    groups = load_groups()  # all 10 groups, 13 airports
    batches = pack_batches(groups, 5)
    assert all(sum(len(g.airports) for g in b) <= 5 for b in batches)
    assert sorted(g.id for b in batches for g in b) == sorted(g.id for g in groups)
    assert len(batches) == 3


def test_oversized_group_gets_its_own_batch():
    groups = [g for g in load_groups() if g.id in {"LON", "MAN"}]
    batches = pack_batches(groups, 3)
    assert [[g.id for g in b] for b in batches] == [["LON"], ["MAN"]]
