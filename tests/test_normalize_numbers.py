from decimal import Decimal

from steer_pipeline.normalize import canonical
from steer_pipeline.numbers import find_numbers


def keys(text):
    return [n.key() for n in find_numbers(text)]


def test_canonical_form_and_index():
    c, idx = canonical("The  ﬁrst “Follow-up”­ – done\n")
    assert c == 'the first "followup" - done'
    assert len(idx) == len(c)
    assert idx[4] == 5  # "f" of the ligature points at the ligature


def test_canonical_keeps_hyphen_before_digits():
    assert canonical("GLP-1RA and GLP‑1")[0] == "glp-1ra and glp-1"


def test_signs_minus_and_hyphen_are_the_same():
    assert keys("−0.4") == keys("-0.4") == [(None, False, Decimal("-0.4"), None, None)]
    assert keys("+0.4") == keys("0.4") != keys("-0.4")
    assert keys("= + 0.7 mmol/L") == keys("0.7 mmol/l")


def test_units_ignore_case_where_it_carries_no_meaning():
    assert keys("5.4 mg/dL") == keys("5.4 mg/dl")
    assert keys("5 mmol/L") == keys("5 mmol/l")
    assert keys("30 kg/m²") == keys("30 kg/m2") == keys("30 kg/m^2")
    assert keys("5.4 mg/dl") != keys("5.4 mmol/L")


def test_percentage_points_are_not_percent():
    assert keys("5 percentage points") == keys("5 pp") == [(None, False, Decimal(5), None, "pp")]
    assert keys("5 percentage points") != keys("5%")
    assert keys("5 per cent") == keys("5%") == keys("5 percent")


def test_comparators_ranges_ci_and_separators():
    assert keys(">30 kg/m2") == keys("more than 30 kg/m2") != keys("30 kg/m2")
    assert keys("p<0.001") == [("<", False, Decimal("0.001"), None, None)]
    assert keys("aged ≥18 years") == keys("at least 18 years")
    assert keys("55%–65%") == keys("55% to 65%") == [(None, False, Decimal(55), Decimal(65), "%")]
    assert keys("14–17 years") == [(None, False, Decimal(14), Decimal(17), "year")]
    assert keys("95% CI") == [(None, False, Decimal(95), None, "% ci")]
    assert keys("8,993") == keys("8993") and keys("1,35,000") == keys("135000")
    assert keys("15.09±1.04") == [(None, False, Decimal("15.09"), None, None), (None, True, Decimal("1.04"), None, None)]
    assert keys("0.7") == keys("0.70")


def test_identifiers_are_not_numbers():
    assert find_numbers("GLP-1RAs, GLP-1, T2D and HbA1c") == []
    assert keys("turnover 5") == [(None, False, Decimal(5), None, None)]


def test_number_words():
    assert keys("within one year") == keys("within 1 year")


def test_duration_words_are_not_comparators():
    assert keys("lost 5 kg over 12 weeks") == keys("5 kg for 12 weeks")
    assert keys("under 2 years") == keys("2 years")
    assert keys("over 30 kg/m2") != keys("30 kg/m2")
    assert keys("more than 12 weeks") != keys("12 weeks")  # phrase comparators always attach


def test_hedge_is_recorded_not_compared():
    a, b = find_numbers("approximately 55%–65%")[0], find_numbers("55% to 65%")[0]
    assert a.key() == b.key() and a.hedged and not b.hedged
    assert find_numbers("~ 20 kg")[0].hedged and find_numbers("about 3 days")[0].hedged


def test_ci_pair_equals_ci_range_only_in_ci_context():
    assert set(keys("1.21 (0.76, 1.84) times higher")) >= set(keys("1.21 (0.76 to 1.84)"))
    assert set(keys("+ 0.7 mmol/L (0.1, 1.3); p")) == set(keys("0.7 mmol/l (0.1 to 1.3)"))
    assert keys("95% CI 0.1, 1.3)") == keys("95% CI 0.1 to 1.3)")
    # control: a reference list "(6, 7)" after text is not a range
    assert keys("as shown before (6, 7) here") == keys("6 and 7")


def test_confidence_interval_spelled_out_equals_ci():
    assert keys("95% confidence interval: -5.08 to -3.46") == keys("95% CI: -5.08 to -3.46")
    assert keys("95% confidence interval (CI) 0.3 to 0.5") == keys("95% CI 0.3 to 0.5")
    assert keys("95% of the confidence") != keys("95% CI")  # control


def test_postfix_comparators():
    assert keys("aged 45 years or older") == keys("aged ≥45 years")
    assert keys("6 or more") == keys("≥6")
    assert keys("aged 30 years or younger") == keys("aged ≤30 years")
    assert keys("aged 45 years") != keys("aged ≥45 years")  # control: no postfix, no comparator


def test_duration_words_match_their_comparator_only_when_the_other_side_has_it():
    under = find_numbers("studies lasting under 52 weeks")[0]
    lt = find_numbers("studies lasting <52 weeks")[0]
    bare = find_numbers("studies lasting 52 weeks")[0]
    assert under.keys() & lt.keys()          # "under 52 weeks" = "<52 weeks"
    assert under.keys() & bare.keys()        # and still = "52 weeks" (a duration)
    assert not bare.keys() & lt.keys()       # a bare "52 weeks" never equals "<52 weeks"
    over = find_numbers("over 12 weeks")[0]
    assert over.keys() & find_numbers(">12 weeks")[0].keys()
