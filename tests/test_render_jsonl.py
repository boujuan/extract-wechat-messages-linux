"""End-to-end JSONL renderer test."""
import json

from wxextract.render import jsonl as render_jsonl


def test_render_jsonl_writes_all_messages(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.jsonl"
    n = render_jsonl.render(messages, contact_record, my_wxid, out)
    assert n == len(messages)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n + 1  # leading _meta + records


def test_jsonl_meta_record(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.jsonl"
    render_jsonl.render(messages, contact_record, my_wxid, out)
    meta = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert meta.get("_meta") is True
    assert meta["contact"]["alias"] == contact_record.alias
    assert meta["glossary"]["U"] == "Me"


def test_jsonl_records_have_minimum_fields(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.jsonl"
    render_jsonl.render(messages, contact_record, my_wxid, out)
    for line in out.read_text(encoding="utf-8").splitlines()[1:101]:
        obj = json.loads(line)
        for field in ("id", "ts", "dt", "sender", "type", "body", "kind"):
            assert field in obj, f"missing {field}: {obj}"
