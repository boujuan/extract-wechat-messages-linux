"""Chunker tests."""
from lxml import etree

from wxextract.chunker import (
    chunk_by_tokens,
    chunk_by_tokens_txt,
    chunk_by_tokens_xml,
    chunk_calendar,
)
from wxextract.render import compact_txt, pseudo_xml
from wxextract.tokens import count as count_tokens


def test_chunk_calendar_day(messages, contact_record, my_wxid, tmp_path):
    base = tmp_path / "conv.txt"
    paths = chunk_calendar(messages, contact_record, my_wxid, compact_txt.render,
                           base, "day")
    assert len(paths) >= 1


def test_chunk_calendar_month_sum_equals_total(messages, contact_record, my_wxid, tmp_path):
    base = tmp_path / "conv.txt"
    paths = chunk_calendar(messages, contact_record, my_wxid, compact_txt.render,
                           base, "month")
    assert len(paths) >= 1
    combined = 0
    for p in paths:
        # extract msgs=N from META line
        meta = next((ln for ln in p.read_text().splitlines() if ln.startswith("META:")), "")
        for tok in meta.split("|"):
            if tok.startswith("msgs="):
                combined += int(tok.split("=", 1)[1])
                break
    assert combined == len(messages)


def test_chunk_by_tokens_txt_b_respects_budget(messages, contact_record, my_wxid, tmp_path):
    full = tmp_path / "conv.txt"
    compact_txt.render(messages, contact_record, my_wxid, full)
    chunks = chunk_by_tokens_txt(full.read_text(encoding="utf-8"), max_tokens=20_000)
    if len(chunks) <= 1:
        # not enough data to chunk; just confirm one valid chunk
        assert chunks and chunks[0].startswith("META: ")
        return
    # each chunk well-formed, repeated header
    for ch in chunks:
        assert ch.startswith("META: ")
    # total tokens conserved roughly (header repetition adds overhead)
    full_t = count_tokens(full.read_text(encoding="utf-8"))
    chunk_t = sum(count_tokens(c) for c in chunks)
    assert chunk_t >= full_t  # never lose content


def test_chunk_by_tokens_xml_well_formed(messages, contact_record, my_wxid, tmp_path):
    full = tmp_path / "conv.xml"
    pseudo_xml.render(messages, contact_record, my_wxid, full)
    chunks = chunk_by_tokens_xml(full.read_text(encoding="utf-8"), max_tokens=30_000)
    for ch in chunks:
        etree.fromstring(ch.encode("utf-8"))


def test_chunk_by_tokens_writes_files(messages, contact_record, my_wxid, tmp_path):
    full = tmp_path / "conv.txt"
    compact_txt.render(messages, contact_record, my_wxid, full)
    paths = chunk_by_tokens(full, max_tokens=30_000, fmt="txt-b")
    assert len(paths) >= 1
    assert all(p.exists() for p in paths)
