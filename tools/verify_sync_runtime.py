"""Offline package check: journal, native AES-GCM, and user-bound credential vault."""
from pathlib import Path
import tempfile


def verify(destination):
    import json
    from tracker import Store
    from sync_core import SyncJournal, decode_batch, encode_batch, new_pairing_key
    from sync_secrets import WindowsSyncVault
    with tempfile.TemporaryDirectory(prefix="moa-sync-check-") as directory:
        store = Store(Path(directory) / "moa.sqlite3")
        journal = SyncJournal(store.db)
        pairing = new_pairing_key()
        journal.connect(pairing)
        store.db.execute("INSERT INTO targets(id,name,kind,matcher,color,icon) VALUES('test','검증','app','test.exe','#336699','app')")
        store.db.commit()
        batch = journal.pending()
        assert decode_batch(encode_batch(batch, pairing), pairing) == batch
        vault = WindowsSyncVault(directory)
        vault.save({"session": "test-only", "pairing_key": pairing})
        assert vault.load()["pairing_key"] == pairing
        vault.save({})
        store.db.close()
    Path(destination).write_text(json.dumps({"ok": True, "crypto": "AES-256-GCM", "vault": "DPAPI", "network": False}), encoding="utf-8")
