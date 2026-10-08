"""自由问数只允许无歧义的单值参考回填。"""

from geoagent.report.reference_answer import unambiguous_scalar


def test_exact_single_value_with_matching_unit() -> None:
    slots = [{"id": "b1:s0", "kind": "placeholder"}]
    items = [{"binding": {"slot_id": "b1:s0"}, "unit": "亩"}]
    assert unambiguous_scalar("12.5亩。", ["b1:s0"], slots, items) == ("b1:s0", "12.5")
    assert unambiguous_scalar("12.5平方米", ["b1:s0"], slots, items) is None
    assert unambiguous_scalar("约12.5亩", ["b1:s0"], slots, items) is None
    assert unambiguous_scalar("12.5亩，另有2亩", ["b1:s0"], slots, items) is None
    assert unambiguous_scalar("12.5亩", ["b1:s0", "b1:s1"], slots, items) is None
