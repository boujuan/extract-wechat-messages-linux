"""Contact loader tests. Opt-in via WXE_TEST_PLAIN_DBS + WXE_TEST_ALIAS."""
import hashlib

from wxextract.contacts import ContactRecord, find_by_alias, load_contacts


def test_md5_table_property_unit():
    """Pure-unit test on the hash — no real data needed."""
    rec = ContactRecord(
        username="wxid_example", alias="some_alias",
        nick_name="", remark="", local_type=1,
    )
    assert rec.md5_table() == "Msg_" + hashlib.md5(b"wxid_example").hexdigest()


def test_loads_contacts(plain_dbs):
    recs = load_contacts(plain_dbs)
    assert len(recs) >= 1


def test_sorted_by_last_seen(plain_dbs):
    recs = load_contacts(plain_dbs)
    last_ts_seq = [r.last_ts for r in recs]
    assert last_ts_seq == sorted(last_ts_seq, reverse=True)


def test_alias_resolvable(plain_dbs, test_alias):
    rec = find_by_alias(load_contacts(plain_dbs), test_alias)
    assert rec is not None
    assert rec.alias == test_alias
    assert rec.message_table is not None
    assert rec.message_db is not None
    assert rec.message_count > 0
