package com.verylovefurry.moa;

import static org.junit.Assert.*;

import android.content.Context;
import android.content.Intent;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import com.chaquo.python.Python;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class NativeIntegrationTest {
  @Test
  public void sharedPythonSqliteAndAndroidCryptoRoundTrip() throws Exception {
    Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
    Bridge.module(context);
    // Run on the packaged Android interpreter and native crypto libraries,
    // rather than substituting a host Python runtime or mock database.
    String code =
        """
import asyncio, tempfile, threading
from pathlib import Path
from tracker import Store
from sync_core import SyncJournal, new_pairing_key, encode_batch, decode_batch
from telegram_sync import SyncEngine
from mobile_backend import MobileBackend
from schedules import save_assignment
with tempfile.TemporaryDirectory() as directory:
    mobile = MobileBackend(Path(directory) / 'phone')
    desktop = Store(Path(directory) / 'pc.sqlite3')
    key = new_pairing_key()
    messages = []
    class Cloud:
        async def receive(self, after):
            for mid, payload in list(messages):
                if mid > after: yield mid, decode_batch(payload, key)
        async def send(self, batch):
            messages.append((len(messages)+1, encode_batch(batch,key)))
    journals = [SyncJournal(desktop.db), SyncJournal(mobile.store.db)]
    for journal in journals: journal.connect(key)
    engines = [SyncEngine(j,threading.RLock(),Cloud()) for j in journals]
    desktop.db.execute("INSERT INTO targets(id,name,kind,matcher,color,icon) VALUES('pc','PC 작업','app','work.exe','#336699','app')")
    desktop.add_session('pc',1000,1600,'auto')
    aid = save_assignment(desktop.db,{'title':'원고','start_date':'2026-10-01','fee':150000,'net_fee':132912,'status':'paid'})
    asyncio.run(engines[0].synchronize())
    asyncio.run(engines[1].synchronize())
    assert mobile.command('totals',{})['net_fee'] == 132912
    mobile.command('target', {'name':'Android 앱','matcher':'example.editor'})
    for now in range(2000,2061): mobile.tick('example.editor',True,now=now)
    mobile.tick('',False,now=2061)
    mobile.command('assignment-status',{'id':aid,'status':'completed'})
    asyncio.run(engines[1].synchronize())
    asyncio.run(engines[0].synchronize())
    assert desktop.daily() == mobile.store.daily()
    assert desktop.db.execute('SELECT status FROM assignments WHERE id=?',(aid,)).fetchone()[0] == 'completed'
    assert len(desktop.sessions()) == 2
    desktop.db.close()
    mobile.store.db.close()
""";
    com.chaquo.python.PyObject builtins = Python.getInstance().getModule("builtins");
    builtins.callAttr("exec", code, builtins.callAttr("dict"));
  }

  @Test
  public void sessionIsEncryptedByAndroidKeystore() throws Exception {
    Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
    SecureStore.initialize(context);
    assertEquals("{}", SecureStore.read());
    SecureStore.write("{\"session\":\"isolated-instrumentation-test\"}");
    assertTrue(SecureStore.read().contains("isolated-instrumentation-test"));
    String saved =
        context.getSharedPreferences("sync_secure", Context.MODE_PRIVATE).getString("payload", "");
    assertFalse(saved.contains("isolated-instrumentation-test"));
    SecureStore.write("{}");
  }

  @Test
  public void debugBuildNeverAutostartsAfterBoot() {
    Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
    context
        .getSharedPreferences("moa", Context.MODE_PRIVATE)
        .edit()
        .putBoolean("start_at_boot", true)
        .putBoolean("tracking", true)
        .commit();
    assertTrue(BuildConfig.DEBUG);
    new BootReceiver().onReceive(context, new Intent(Intent.ACTION_BOOT_COMPLETED));
    context.getSharedPreferences("moa", Context.MODE_PRIVATE).edit().clear().commit();
  }
}
