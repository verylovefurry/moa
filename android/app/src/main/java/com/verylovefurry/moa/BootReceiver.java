package com.verylovefurry.moa;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import androidx.core.content.ContextCompat;

public final class BootReceiver extends BroadcastReceiver {
  @Override
  public void onReceive(Context context, Intent intent) {
    // Test packages never register themselves for boot-time monitoring.
    if (BuildConfig.DEBUG || !Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
    android.content.SharedPreferences prefs =
        context.getSharedPreferences("moa", Context.MODE_PRIVATE);
    if (prefs.getBoolean("start_at_boot", false)
        && prefs.getBoolean("tracking", false)
        && TrackingService.hasAccess(context))
      ContextCompat.startForegroundService(context, new Intent(context, TrackingService.class));
  }
}
