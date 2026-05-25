"""Pseudo-XML renderer test."""
from lxml import etree

from wxextract.render import pseudo_xml


def test_pseudo_xml_parses(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.xml"
    pseudo_xml.render(messages, contact_record, my_wxid, out)
    root = etree.fromstring(out.read_bytes())
    assert root.tag == "conversation"
    assert root.get("contact") == contact_record.alias
    assert int(root.get("msgs")) == len(messages)
    assert root.get("tokens", "").startswith("~")


def test_pseudo_xml_session_counts(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.xml"
    pseudo_xml.render(messages, contact_record, my_wxid, out)
    root = etree.fromstring(out.read_bytes())
    sessions = root.findall("session")
    assert len(sessions) >= 1
    total_m = sum(len(s.findall("m")) for s in sessions)
    assert total_m == len(messages)


def test_pseudo_xml_escape_safe(messages, contact_record, my_wxid, tmp_path):
    """Strict XML parser tolerates the full output."""
    out = tmp_path / "conv.xml"
    pseudo_xml.render(messages, contact_record, my_wxid, out)
    etree.fromstring(out.read_bytes())
