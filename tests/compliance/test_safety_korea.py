from types import SimpleNamespace

from compliance_briefing.collectors.safety_korea import SafetyKoreaCollector


def test_html_row_uses_recall_uid_for_navigable_detail_url():
    row = """
    <tr onclick="goDetail('10022503')">
      <td>4241</td>
      <td><input type="hidden" name="recallUid" value="10022503"></td>
      <td><a href="#none">보바 맥세이프 보조배터리 10000mAh</a></td>
      <td>VA-111<!-- </font> --></td>
      <td>(주)명성</td>
      <td>자발적리콜</td>
      <td></td>
      <td>2026-07-24</td>
    </tr>
    """
    collector = SafetyKoreaCollector(SimpleNamespace())

    item = collector._parse_html_row(row)

    assert item is not None
    assert item["external_id"] == "10022503"
    assert item["url"] == (
        "https://www.safetykorea.kr/recall/ajax/recallBoard"
        "?recallUid=10022503"
    )
    assert item["title"] == "[소비자24 리콜] 보바 맥세이프 보조배터리 10000mAh"
    assert item["brand"] == "(주)명성"
    assert item["extra"]["recall_no"] == "VA-111"
