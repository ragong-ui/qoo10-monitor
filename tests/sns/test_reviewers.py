from reviewers import (
    REVIEWER_OPTIONS,
    excel_validation_formula,
    reviewer_dropdown_options,
)


def test_reviewer_list_matches_operational_roster():
    assert REVIEWER_OPTIONS == [
        "Rani Gong",
        "Jihyun Kwon",
        "Minjong Jang",
        "Donghee Kim",
        "Whajoon Ryu",
        "Woongsoo Shin",
        "Kim Meekyoung",
        "Kim Jinsun",
        "Choi Yunju",
        "Hyejin Jegal",
    ]


def test_dropdown_keeps_blank_first_and_preserves_legacy_values():
    choices = reviewer_dropdown_options(["Rani Gong", "Legacy User", "", None])
    assert choices[0] == ""
    assert choices.count("Rani Gong") == 1
    assert choices[-1] == "Legacy User"


def test_excel_formula_is_a_valid_inline_list():
    formula = excel_validation_formula()
    assert formula.startswith('"')
    assert formula.endswith('"')
    assert "Rani Gong" in formula
    assert "Hyejin Jegal" in formula
