from eval.run_eval import compare


def test_exact_match():
    assert compare([["A", 1.0]], [["A", 1.0]])


def test_tolerates_different_rounding_and_extra_columns():
    assert compare([["Ação", 123456.85]], [["Ação", 123456.849, "extra"]])


def test_agent_with_fewer_rows_fails():
    assert not compare([["A", 1], ["B", 2]], [["A", 1]])


def test_agent_with_more_rows_still_passes_when_reference_is_contained():
    assert compare([["A", 1]], [["A", 1], ["B", 2]])


def test_wrong_value_fails():
    assert not compare([["A", 10.0]], [["A", 99.0]])


def test_percent_vs_fraction_is_not_confused():
    assert not compare([["A", 74.67]], [["A", 0.7467]])


def test_metric_mode_ignores_titles_when_there_are_ties():
    assert compare([["X", 99.9], ["Y", 98.0]], [["Z", 99.9], ["W", 98.0]], "metric")
    assert not compare([["X", 99.9]], [["Z", 50.0]], "metric")


def test_numeric_text_and_position_column_are_understood():
    ref = [["Dad, I'm Sorry", 100.0], ["Jailbait", 99.99]]
    agent = [[1, "Dad, I'm Sorry", "100.00%"], [2, "Jailbait", "99.99%"]]
    assert compare(ref, agent)
    assert compare(ref, agent, "metric")


def test_brazilian_number_format():
    assert compare([["A", 1234.5]], [["A", "R$ 1.234,50"]])
    assert not compare([["A", 1234.5]], [["A", "R$ 9.999,00"]])
