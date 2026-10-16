"""Test-only targets. Production installations always start with an empty list."""
from datetime import datetime
from tracker import COLORS, KST


def seed_test_store(store, sessions=False):
    store.db.executemany("INSERT INTO targets(id,name,kind,matcher,color,icon) VALUES (?,?,?,?,?,?)", (
        ("figma", "Figma", "app", "figma.exe", COLORS[0], "figma"),
        ("code", "Visual Studio Code", "app", "code.exe", COLORS[1], "code"),
        ("docs", "Google 문서", "url", "https://docs.google.com/document/", COLORS[2], "document"),
        ("sheets", "Google 스프레드시트", "url", "https://docs.google.com/spreadsheets/", COLORS[3], "sheet"),
    ))
    store.db.commit()
    if sessions:
        start = datetime.now(KST).replace(hour=9,minute=0,second=0,microsecond=0).timestamp()
        store.add_session("figma",start,start+1800,"demo")
        store.add_session("code",start+3600,start+7200,"demo")
    return store
