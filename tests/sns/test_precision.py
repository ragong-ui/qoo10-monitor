from __future__ import annotations

from sns_precision import contains_hard_exclusion, is_excluded_domain


def test_feedback_domains_are_exact_host_matches():
    assert is_excluded_domain("https://news.ecnomikata.com/a", source="google")
    assert is_excluded_domain("https://aucview.aucfan.com/yahoo/x", source="google")
    assert not is_excluded_domain("https://example.com/ecnomikata.com", source="google")
    assert not is_excluded_domain("https://lipscosme.com/posts/1", source="google")
    assert not is_excluded_domain("https://kigencheck.jp/posts/1", source="google")
    assert not is_excluded_domain("https://x.com/a/status/1", source="x")


def test_explicit_ad_markers_are_case_insensitive_but_not_prefixes():
    assert contains_hard_exclusion("新商品です #pr")
    assert contains_hard_exclusion("商品提供をいただきました")
    assert not contains_hard_exclusion("#adidas の偽物をQoo10で買った")


def test_existing_guide_and_makeup_exclusions_remain():
    assert contains_hard_exclusion("偽物の見分け方")
    assert contains_hard_exclusion("すっぴん詐欺メイク")
    assert not contains_hard_exclusion("Qoo10で買った商品が偽物でした")
