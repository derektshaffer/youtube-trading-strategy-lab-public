from systematic_trader.discovery_bounds import observed_label,recall_bounds


def test_sparse_crossing_proves_positive_but_absence_cannot_prove_negative():
    rows=[dict(stamp=10,segment='regular',payload=dict(open='10',high='10')),dict(stamp=90,segment='regular',payload=dict(open='10',high='12'))]
    assert observed_label(rows,10,'0.10')['state']=='positive'
    assert observed_label(rows[:1],10,'0.10')['state']=='unknown'
    assert observed_label(rows[1:],10,'0.10')['state']=='unknown'
    assert observed_label(rows,10,'0.10',excluded=True)['state']=='unknown'


def test_recall_bounds_include_undetected_unknowns_without_false_zero_precision():
    assert recall_bounds(10,2,8,2)==[2/16,4/12]
    assert recall_bounds(0,0,0,0)==[None,None]
