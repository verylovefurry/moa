package com.verylovefurry.moa;

import android.app.AppOpsManager;
import android.app.KeyguardManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.app.usage.UsageEvents;
import android.app.usage.UsageStatsManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.Process;
import androidx.core.app.NotificationCompat;

public final class TrackingService extends Service {
  public static final String STOP = "com.verylovefurry.moa.STOP";
  private HandlerThread worker;
  private Handler handler;
  private volatile boolean stopping;
  private long lastQuery;
  private String foreground = "";
  private BroadcastReceiver screen;

  public static boolean hasAccess(Context context) {
    AppOpsManager manager = (AppOpsManager) context.getSystemService(APP_OPS_SERVICE);
    return manager.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.getPackageName())
        == AppOpsManager.MODE_ALLOWED;
  }

  @Override
  public void onCreate() {
    super.onCreate();
    worker = new HandlerThread("MoaUsage");
    worker.start();
    handler = new Handler(worker.getLooper());
    NotificationManager manager = getSystemService(NotificationManager.class);
    manager.createNotificationChannel(
        new NotificationChannel("tracking", "자동 작업 시간 기록", NotificationManager.IMPORTANCE_LOW));
    PendingIntent open =
        PendingIntent.getActivity(
            this,
            0,
            new Intent(this, MainActivity.class),
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    PendingIntent stop =
        PendingIntent.getService(
            this,
            1,
            new Intent(this, TrackingService.class).setAction(STOP),
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    Notification notification =
        new NotificationCompat.Builder(this, "tracking")
            .setSmallIcon(R.drawable.ic_moa)
            .setContentTitle("모아 자동 추적 중")
            .setContentText("선택한 앱의 작업 시간을 기록합니다.")
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .addAction(0, "추적 중지", stop)
            .build();
    if (Build.VERSION.SDK_INT >= 34)
      startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
    else startForeground(1, notification);
    lastQuery = System.currentTimeMillis() - 120000;
    screen =
        new BroadcastReceiver() {
          @Override
          public void onReceive(Context context, Intent intent) {
            handler.post(() -> sample(false));
          }
        };
    IntentFilter filter = new IntentFilter();
    filter.addAction(Intent.ACTION_SCREEN_OFF);
    filter.addAction(Intent.ACTION_SCREEN_ON);
    filter.addAction(Intent.ACTION_USER_PRESENT);
    if (Build.VERSION.SDK_INT >= 33) registerReceiver(screen, filter, RECEIVER_NOT_EXPORTED);
    else registerReceiver(screen, filter);
    handler.post(poll);
  }

  private final Runnable poll =
      new Runnable() {
        @Override
        public void run() {
          if (stopping) return;
          sample(true);
          if (!stopping) handler.postDelayed(this, 1000);
        }
      };

  private void sample(boolean checkPermission) {
    if (stopping) return;
    if (checkPermission && !hasAccess(this)) {
      getSharedPreferences("moa", MODE_PRIVATE).edit().putBoolean("tracking", false).apply();
      stopSelf();
      return;
    }
    try {
      long now = System.currentTimeMillis();
      UsageEvents events =
          ((UsageStatsManager) getSystemService(USAGE_STATS_SERVICE)).queryEvents(lastQuery, now);
      UsageEvents.Event event = new UsageEvents.Event();
      while (events.hasNextEvent()) {
        events.getNextEvent(event);
        if (event.getEventType() == UsageEvents.Event.ACTIVITY_RESUMED)
          foreground = event.getPackageName();
        else if (event.getEventType() == UsageEvents.Event.ACTIVITY_PAUSED
            && event.getPackageName().equals(foreground)) foreground = "";
      }
      lastQuery = now;
      boolean active =
          ((PowerManager) getSystemService(POWER_SERVICE)).isInteractive()
              && !((KeyguardManager) getSystemService(KEYGUARD_SERVICE)).isKeyguardLocked();
      Bridge.module(this).callAttr("tick", foreground, active, true);
      getSharedPreferences("moa", MODE_PRIVATE).edit().remove("tracking_error").apply();
    } catch (Exception error) {
      getSharedPreferences("moa", MODE_PRIVATE)
          .edit()
          .putString("tracking_error", "기록을 저장하지 못했습니다. 앱을 열어 저장 공간과 권한을 확인해주세요.")
          .apply();
      stopSelf();
    }
  }

  @Override
  public int onStartCommand(Intent intent, int flags, int startId) {
    if (intent != null && STOP.equals(intent.getAction())) {
      getSharedPreferences("moa", MODE_PRIVATE).edit().putBoolean("tracking", false).apply();
      stopSelf();
      return START_NOT_STICKY;
    }
    if (!hasAccess(this)
        || !getSharedPreferences("moa", MODE_PRIVATE).getBoolean("tracking", false)) {
      stopSelf();
      return START_NOT_STICKY;
    }
    return START_STICKY;
  }

  @Override
  public void onDestroy() {
    stopping = true;
    if (screen != null) unregisterReceiver(screen);
    handler.removeCallbacksAndMessages(null);
    handler.post(
        () -> {
          try {
            Bridge.module(this).callAttr("tick", "", false, false);
          } finally {
            worker.quitSafely();
          }
        });
    stopForeground(STOP_FOREGROUND_REMOVE);
    super.onDestroy();
  }

  @Override
  public IBinder onBind(Intent intent) {
    return null;
  }
}
