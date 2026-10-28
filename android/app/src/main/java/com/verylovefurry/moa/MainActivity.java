package com.verylovefurry.moa;

import android.Manifest;
import android.app.DatePickerDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ResolveInfo;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.NumberPicker;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import com.google.android.material.bottomnavigation.BottomNavigationView;
import com.google.android.material.button.MaterialButton;
import com.google.android.material.textfield.TextInputLayout;
import java.time.LocalDate;
import java.time.YearMonth;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Consumer;
import org.json.JSONArray;
import org.json.JSONObject;

public final class MainActivity extends AppCompatActivity {
  private static final int GREEN = Color.rgb(61, 107, 93),
      TEXT = Color.rgb(35, 49, 43),
      MUTED = Color.rgb(105, 117, 110),
      LINE = Color.rgb(220, 228, 222);
  private static final String[] STATUS_IDS = {
    "not_started", "in_progress", "first_done", "delivered", "paid", "completed"
  };
  private static final String[] STATUS_NAMES = {"미착수", "진행중", "1차 완료", "파일 전달", "보수 수령", "최종 완료"};
  private static final ZoneId KST = ZoneId.of("Asia/Seoul");
  private final Handler ui = new Handler(Looper.getMainLooper());
  private JSONObject snapshot = new JSONObject();
  private LinearLayout root, body, trackingRow;
  private TextView trackingText, message;
  private MaterialButton trackingButton;
  private YearMonth month = YearMonth.now(KST);
  private LocalDate selected = LocalDate.now(KST);
  private int tab;
  private boolean busy, loading;
  private int totalMode;
  private LocalDate totalStart = LocalDate.now(KST).withDayOfMonth(1),
      totalEnd = LocalDate.now(KST);

  private SharedPreferences prefs() {
    return getSharedPreferences("moa", MODE_PRIVATE);
  }

  private int dp(float value) {
    return Math.round(getResources().getDisplayMetrics().density * value);
  }

  private LinearLayout column() {
    LinearLayout v = new LinearLayout(this);
    v.setOrientation(LinearLayout.VERTICAL);
    return v;
  }

  private LinearLayout row() {
    LinearLayout v = new LinearLayout(this);
    v.setGravity(Gravity.CENTER_VERTICAL);
    return v;
  }

  private LinearLayout.LayoutParams weight() {
    return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
  }

  private TextView text(String value, int size, int color) {
    TextView v = new TextView(this);
    v.setText(value);
    v.setTextSize(size);
    v.setTextColor(color);
    v.setIncludeFontPadding(false);
    return v;
  }

  private TextView heading(String value) {
    TextView v = text(value, 20, TEXT);
    v.setTypeface(null, Typeface.BOLD);
    return v;
  }

  private void space(LinearLayout parent, int size) {
    View v = new View(this);
    parent.addView(v, new LinearLayout.LayoutParams(1, dp(size)));
  }

  private void line(LinearLayout parent) {
    View v = new View(this);
    v.setBackgroundColor(LINE);
    parent.addView(v, new LinearLayout.LayoutParams(-1, dp(1)));
  }

  private MaterialButton button(String title, Runnable action) {
    MaterialButton b =
        new MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle);
    b.setText(title);
    b.setTextSize(14);
    b.setAllCaps(false);
    b.setCornerRadius(dp(6));
    b.setMinHeight(dp(48));
    b.setTextColor(GREEN);
    b.setOnClickListener(v -> action.run());
    return b;
  }

  private MaterialButton icon(int resource, String label, Runnable action) {
    MaterialButton b = button("", action);
    b.setIconResource(resource);
    b.setIconSize(dp(22));
    b.setIconPadding(0);
    b.setContentDescription(label);
    b.setPadding(dp(12), 0, dp(12), 0);
    b.setMinimumWidth(dp(48));
    b.setLayoutParams(new LinearLayout.LayoutParams(dp(48), dp(48)));
    return b;
  }

  private static JSONObject obj(String key, Object value) {
    JSONObject r = new JSONObject();
    try {
      r.put(key, value == null ? JSONObject.NULL : value);
    } catch (Exception e) {
      throw new IllegalArgumentException(e);
    }
    return r;
  }

  private static void put(JSONObject data, String key, Object value) {
    try {
      data.put(key, value == null ? JSONObject.NULL : value);
    } catch (Exception e) {
      throw new IllegalArgumentException(e);
    }
  }

  private JSONArray array(String key) {
    return snapshot.optJSONArray(key) == null ? new JSONArray() : snapshot.optJSONArray(key);
  }

  private String money(JSONObject item, String field) {
    return item.isNull(field) || !item.has(field)
        ? "—"
        : String.format(Locale.KOREA, "%,d원", item.optLong(field));
  }

  private static String duration(double seconds) {
    int minutes = Math.max(0, (int) seconds / 60);
    return minutes >= 60 ? (minutes / 60) + "시간 " + (minutes % 60) + "분" : minutes + "분";
  }

  private String statusName(String id) {
    for (int i = 0; i < STATUS_IDS.length; i++)
      if (STATUS_IDS[i].equals(id)) return STATUS_NAMES[i];
    return id;
  }

  private GradientDrawable background(int color, boolean border) {
    GradientDrawable d = new GradientDrawable();
    d.setColor(color);
    d.setCornerRadius(dp(5));
    if (border) d.setStroke(dp(1), GREEN);
    return d;
  }

  @Override
  public void onCreate(Bundle state) {
    super.onCreate(state);
    SecureStore.initialize(this);
    if (state != null) {
      tab = state.getInt("tab");
      month = YearMonth.parse(state.getString("month", month.toString()));
      selected = LocalDate.parse(state.getString("selected", selected.toString()));
    }
    root = column();
    root.setBackgroundColor(Color.rgb(246, 248, 246));
    ViewCompat.setOnApplyWindowInsetsListener(
        root,
        (v, insets) -> {
          Insets i = insets.getInsets(WindowInsetsCompat.Type.systemBars());
          root.setPadding(i.left, i.top, i.right, i.bottom);
          return insets;
        });
    LinearLayout toolbar = row();
    toolbar.setPadding(dp(20), dp(4), dp(12), dp(4));
    toolbar.addView(heading("모아"), weight());
    toolbar.addView(
        button(
            "동기화",
            () -> {
              if (snapshot.optJSONObject("sync") != null
                  && snapshot.optJSONObject("sync").optBoolean("connected"))
                command("sync-sync", new JSONObject(), null);
              else syncDialog();
            }));
    root.addView(toolbar);
    line(root);
    message = text("기록을 불러오는 중…", 13, MUTED);
    message.setPadding(dp(20), dp(8), dp(20), dp(8));
    root.addView(message);
    ScrollView scroll = new ScrollView(this);
    scroll.setFillViewport(true);
    body = column();
    body.setPadding(dp(20), dp(12), dp(20), dp(24));
    scroll.addView(body);
    root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    line(root);
    trackingRow = row();
    trackingRow.setPadding(dp(20), dp(4), dp(12), dp(4));
    trackingText = text("추적 중지", 13, TEXT);
    trackingRow.addView(trackingText, weight());
    trackingButton = button("자동 추적 시작", this::toggleTracking);
    trackingRow.addView(trackingButton);
    root.addView(trackingRow);
    BottomNavigationView navigation = new BottomNavigationView(this);
    navigation.setBackgroundColor(Color.WHITE);
    navigation.setLabelVisibilityMode(
        com.google.android.material.navigation.NavigationBarView.LABEL_VISIBILITY_LABELED);
    navigation.getMenu().add(0, 1, 0, "기록").setIcon(R.drawable.ic_calendar);
    navigation.getMenu().add(0, 2, 1, "일정").setIcon(R.drawable.ic_tasks);
    navigation.getMenu().add(0, 3, 2, "합계").setIcon(R.drawable.ic_chart);
    navigation.getMenu().add(0, 4, 3, "설정").setIcon(R.drawable.ic_settings);
    navigation.setSelectedItemId(tab + 1);
    navigation.setOnItemSelectedListener(
        item -> {
          tab = item.getItemId() - 1;
          render();
          return true;
        });
    root.addView(navigation);
    setContentView(root);
    render();
    load();
  }

  @Override
  protected void onSaveInstanceState(Bundle state) {
    super.onSaveInstanceState(state);
    state.putInt("tab", tab);
    state.putString("month", month.toString());
    state.putString("selected", selected.toString());
  }

  private final Runnable refresh =
      new Runnable() {
        @Override
        public void run() {
          load();
          ui.postDelayed(this, 5000);
        }
      };

  @Override
  protected void onResume() {
    super.onResume();
    if (prefs().getBoolean("tracking", false) && TrackingService.hasAccess(this))
      ContextCompat.startForegroundService(this, new Intent(this, TrackingService.class));
    ui.post(refresh);
  }

  @Override
  protected void onPause() {
    super.onPause();
    ui.removeCallbacks(refresh);
  }

  private void load() {
    if (loading || busy) return;
    loading = true;
    Bridge.IO.execute(
        () -> {
          try {
            JSONObject value = Bridge.state(this);
            ui.post(
                () -> {
                  loading = false;
                  if (!isDestroyed()) {
                    snapshot = value;
                    render();
                  }
                });
          } catch (Exception e) {
            ui.post(
                () -> {
                  loading = false;
                  message.setVisibility(View.VISIBLE);
                  message.setText("기록을 불러오지 못했습니다. 앱을 다시 열어주세요.");
                });
          }
        });
  }

  private void command(String action, JSONObject payload, Consumer<JSONObject> done) {
    if (busy) return;
    busy = true;
    message.setVisibility(View.VISIBLE);
    message.setText("처리 중…");
    Bridge.IO.execute(
        () -> {
          try {
            JSONObject result = Bridge.command(this, action, payload);
            JSONObject updated = Bridge.state(this);
            ui.post(
                () -> {
                  busy = false;
                  if (!isDestroyed()) {
                    snapshot = updated;
                    render();
                    if (done != null) done.accept(result);
                  }
                });
          } catch (Exception e) {
            String error = e.getMessage();
            ui.post(
                () -> {
                  busy = false;
                  if (!isDestroyed()) {
                    message.setVisibility(View.VISIBLE);
                    message.setText(error == null ? "요청을 처리하지 못했습니다." : error);
                    new AlertDialog.Builder(this)
                        .setTitle("확인")
                        .setMessage(error)
                        .setPositiveButton("확인", null)
                        .show();
                  }
                });
          }
        });
  }

  private void render() {
    body.removeAllViews();
    boolean tracking = prefs().getBoolean("tracking", false) && TrackingService.hasAccess(this);
    JSONObject tracker = snapshot.optJSONObject("tracker");
    String status = tracker == null ? "" : tracker.optString("status");
    trackingText.setText(
        !tracking
            ? "추적 중지"
            : status.equals("recording")
                ? "자동 기록 중"
                : status.equals("continuing") ? "이어 기록 중" : "앱 감지 대기");
    trackingButton.setText(tracking ? "추적 중지" : "자동 추적 시작");
    trackingButton.setIconResource(tracking ? R.drawable.ic_pause : R.drawable.ic_play);
    trackingButton.setIconSize(dp(20));
    String error = prefs().getString("tracking_error", "");
    message.setVisibility(busy || !error.isEmpty() ? View.VISIBLE : View.GONE);
    if (!error.isEmpty()) message.setText(error);
    if (tab == 0) calendar();
    else if (tab == 1) schedules();
    else if (tab == 2) totals();
    else settings();
  }

  private void monthRow(LinearLayout parent) {
    LinearLayout row = row();
    row.addView(
        icon(
            R.drawable.ic_previous,
            "이전 달",
            () -> {
              month = month.minusMonths(1);
              render();
            }));
    MaterialButton title =
        button(month.getYear() + "년 " + month.getMonthValue() + "월", this::chooseMonth);
    title.setTextSize(18);
    row.addView(title, weight());
    row.addView(
        icon(
            R.drawable.ic_next,
            "다음 달",
            () -> {
              month = month.plusMonths(1);
              render();
            }));
    parent.addView(row);
  }

  private void chooseMonth() {
    LinearLayout row = row();
    row.setGravity(Gravity.CENTER);
    NumberPicker year = new NumberPicker(this);
    year.setMinValue(1900);
    year.setMaxValue(9999);
    year.setValue(month.getYear());
    NumberPicker number = new NumberPicker(this);
    number.setMinValue(1);
    number.setMaxValue(12);
    number.setValue(month.getMonthValue());
    year.setContentDescription("연도");
    number.setContentDescription("월");
    row.addView(year);
    row.addView(number);
    new AlertDialog.Builder(this)
        .setTitle("연도·월 이동")
        .setView(row)
        .setNegativeButton("취소", null)
        .setPositiveButton(
            "이동",
            (d, w) -> {
              month = YearMonth.of(year.getValue(), number.getValue());
              render();
            })
        .show();
  }

  private JSONObject daily(String date) {
    JSONObject map = snapshot.optJSONObject("daily");
    return map == null
        ? new JSONObject()
        : map.optJSONObject(date) == null ? new JSONObject() : map.optJSONObject(date);
  }

  private Map<String, JSONObject> targetMap() {
    Map<String, JSONObject> map = new HashMap<>();
    JSONArray list = array("targets");
    for (int i = 0; i < list.length(); i++) {
      JSONObject t = list.optJSONObject(i);
      map.put(t.optString("id"), t);
    }
    return map;
  }

  private void calendar() {
    monthRow(body);
    double total = 0;
    for (int d = 1; d <= month.lengthOfMonth(); d++)
      total += daily(month.atDay(d).toString()).optDouble("total");
    TextView accumulated = text("이달 작업 시간  " + duration(total), 15, TEXT);
    accumulated.setPadding(0, dp(8), 0, dp(16));
    body.addView(accumulated);
    String[] days = {"월", "화", "수", "목", "금", "토", "일"};
    LinearLayout week = row();
    for (String day : days) {
      TextView v = text(day, 12, MUTED);
      v.setGravity(Gravity.CENTER);
      week.addView(v, weight());
    }
    body.addView(week);
    space(body, 8);
    int offset = month.atDay(1).getDayOfWeek().getValue() - 1;
    for (int r = 0; r < (offset + month.lengthOfMonth() + 6) / 7; r++) {
      LinearLayout strip = row();
      for (int c = 0; c < 7; c++) {
        int day = r * 7 + c - offset + 1;
        TextView cell = text("", 14, TEXT);
        cell.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, dp(58), 1);
        p.setMargins(dp(1), dp(1), dp(1), dp(1));
        if (day > 0 && day <= month.lengthOfMonth()) {
          LocalDate value = month.atDay(day);
          double seconds = daily(value.toString()).optDouble("total");
          cell.setText(day + (seconds >= 60 ? "\n" + (int) (seconds / 60) + "분" : ""));
          if (value.equals(selected))
            cell.setBackground(background(Color.rgb(225, 236, 229), false));
          else if (value.equals(LocalDate.now(KST)))
            cell.setBackground(background(Color.TRANSPARENT, true));
          cell.setContentDescription(value + ", " + duration(seconds));
          cell.setFocusable(true);
          cell.setOnClickListener(
              v -> {
                selected = value;
                render();
              });
        }
        strip.addView(cell, p);
      }
      body.addView(strip);
    }
    space(body, 20);
    line(body);
    space(body, 20);
    LinearLayout heading = row();
    heading.addView(
        text(selected.getMonthValue() + "월 " + selected.getDayOfMonth() + "일", 18, TEXT), weight());
    heading.addView(text(duration(daily(selected.toString()).optDouble("total")), 16, GREEN));
    body.addView(heading);
    space(body, 12);
    Map<String, JSONObject> targets = targetMap();
    JSONArray sessions = array("sessions");
    boolean found = false;
    double start = selected.atStartOfDay(KST).toEpochSecond(),
        end = selected.plusDays(1).atStartOfDay(KST).toEpochSecond();
    for (int i = 0; i < sessions.length(); i++) {
      JSONObject s = sessions.optJSONObject(i);
      double a = Math.max(start, s.optDouble("start")), b = Math.min(end, s.optDouble("end"));
      if (b <= a) continue;
      found = true;
      JSONObject target = targets.get(s.optString("target_id"));
      String name = target == null ? "작업" : target.optString("name");
      String device =
          target != null && target.optString("platform").equals("android") ? "Android" : "PC";
      LinearLayout entry = column();
      entry.setPadding(0, dp(10), 0, dp(10));
      LinearLayout top = row();
      top.addView(text(name, 15, TEXT), weight());
      top.addView(text(duration(b - a), 14, TEXT));
      entry.addView(top);
      String times =
          java.time.Instant.ofEpochSecond((long) a)
                  .atZone(KST)
                  .format(DateTimeFormatter.ofPattern("HH:mm"))
              + "–"
              + java.time.Instant.ofEpochSecond((long) b)
                  .atZone(KST)
                  .format(DateTimeFormatter.ofPattern("HH:mm"));
      space(entry, 5);
      entry.addView(
          text(
              times + " · " + device + (s.optDouble("recovered_seconds") > 0 ? " · 추정 복원 포함" : ""),
              12,
              MUTED));
      body.addView(entry);
      line(body);
    }
    if (!found) body.addView(text("기록된 작업 시간이 없습니다.", 14, MUTED));
  }

  private void schedules() {
    LinearLayout title = row();
    title.addView(heading("일정 관리"), weight());
    title.addView(button("새 일정", () -> editAssignment(null)));
    body.addView(title);
    monthRow(body);
    space(body, 12);
    JSONArray items = array("assignments");
    boolean found = false;
    for (int i = 0; i < items.length(); i++) {
      JSONObject item = items.optJSONObject(i);
      String start = item.optString("start_date");
      String due = item.isNull("due_date") ? start : item.optString("due_date", start);
      if (start.compareTo(month.atEndOfMonth().toString()) > 0
          || due.compareTo(month.atDay(1).toString()) < 0) continue;
      found = true;
      LinearLayout entry = column();
      entry.setPadding(0, dp(16), 0, dp(16));
      LinearLayout first = row();
      first.addView(text(item.optString("title"), 16, TEXT), weight());
      first.addView(text(statusName(item.optString("status")), 12, GREEN));
      entry.addView(first);
      if (!item.optString("client").isEmpty()) {
        space(entry, 6);
        entry.addView(text(item.optString("client"), 13, MUTED));
      }
      space(entry, 8);
      entry.addView(text(start + (start.equals(due) ? "" : " → " + due), 13, MUTED));
      space(entry, 8);
      entry.addView(new PeriodBar(start, due), new LinearLayout.LayoutParams(-1, dp(14)));
      entry.setContentDescription(
          item.optString("title")
              + ", "
              + statusName(item.optString("status"))
              + ", "
              + start
              + ", 상세 보기");
      entry.setFocusable(true);
      entry.setOnClickListener(v -> assignmentDetails(item));
      body.addView(entry);
      line(body);
    }
    if (!found) body.addView(text("이달 일정이 없습니다.", 14, MUTED));
  }

  private final class PeriodBar extends View {
    private final LocalDate start, end;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

    PeriodBar(String a, String b) {
      super(MainActivity.this);
      start = LocalDate.parse(a);
      end = LocalDate.parse(b);
      setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
    }

    @Override
    protected void onDraw(Canvas canvas) {
      super.onDraw(canvas);
      float width = getWidth(), mid = getHeight() / 2f;
      paint.setColor(LINE);
      paint.setStrokeWidth(dp(2));
      canvas.drawLine(0, mid, width, mid, paint);
      long base = month.atDay(1).toEpochDay();
      float a = Math.max(0, (start.toEpochDay() - base + .5f) / month.lengthOfMonth() * width),
          b = Math.min(width, (end.toEpochDay() - base + .5f) / month.lengthOfMonth() * width);
      paint.setColor(GREEN);
      paint.setStrokeWidth(dp(5));
      paint.setStrokeCap(Paint.Cap.ROUND);
      canvas.drawLine(a, mid, b, mid, paint);
      canvas.drawCircle(a, mid, dp(3), paint);
    }
  }

  private LinearLayout dialogBody() {
    LinearLayout v = column();
    v.setPadding(dp(24), dp(12), dp(24), dp(16));
    return v;
  }

  private ScrollView scroll(LinearLayout content) {
    ScrollView v = new ScrollView(this);
    v.addView(content);
    return v;
  }

  private void detail(LinearLayout parent, String label, String value) {
    LinearLayout r = row();
    r.setPadding(0, dp(8), 0, dp(8));
    TextView l = text(label, 13, MUTED);
    r.addView(l, new LinearLayout.LayoutParams(dp(88), -2));
    r.addView(text(value, 14, TEXT), new LinearLayout.LayoutParams(0, -2, 1));
    parent.addView(r);
  }

  private void assignmentDetails(JSONObject item) {
    LinearLayout content = dialogBody();
    detail(content, "의뢰인", item.optString("client").isEmpty() ? "—" : item.optString("client"));
    detail(content, "시작일", item.optString("start_date"));
    detail(content, "마감일", item.isNull("due_date") ? "없음" : item.optString("due_date"));
    detail(content, "의뢰비", money(item, "fee"));
    detail(
        content,
        "실수령액",
        item.isNull("net_fee") ? money(item, "fee") + " (의뢰비 기준)" : money(item, "net_fee"));
    space(content, 8);
    Spinner status = new Spinner(this);
    status.setAdapter(
        new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, STATUS_NAMES));
    for (int i = 0; i < STATUS_IDS.length; i++)
      if (STATUS_IDS[i].equals(item.optString("status"))) status.setSelection(i);
    content.addView(text("진행 상태", 13, MUTED));
    content.addView(status, new LinearLayout.LayoutParams(-1, dp(48)));
    if (!item.optString("note").isEmpty()) {
      space(content, 16);
      content.addView(text(item.optString("note"), 14, TEXT));
    }
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle(item.optString("title"))
            .setView(scroll(content))
            .setNegativeButton("닫기", null)
            .setNeutralButton("편집", (d, w) -> editAssignment(item))
            .setPositiveButton("상태 저장", null)
            .create();
    dialog.setOnShowListener(
        d ->
            dialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    v -> {
                      JSONObject p = obj("id", item.optString("id"));
                      put(p, "status", STATUS_IDS[status.getSelectedItemPosition()]);
                      command("assignment-status", p, result -> dialog.dismiss());
                    }));
    dialog.show();
  }

  private EditText field(LinearLayout parent, String label, String value, int type) {
    TextInputLayout box =
        new TextInputLayout(this, null, com.google.android.material.R.attr.textInputOutlinedStyle);
    box.setHint(label);
    box.setBoxBackgroundMode(TextInputLayout.BOX_BACKGROUND_OUTLINE);
    box.setBoxCornerRadii(dp(6), dp(6), dp(6), dp(6));
    EditText input = new com.google.android.material.textfield.TextInputEditText(box.getContext());
    input.setText(value);
    input.setInputType(type);
    input.setTextSize(15);
    input.setSingleLine(type != InputType.TYPE_CLASS_TEXT + InputType.TYPE_TEXT_FLAG_MULTI_LINE);
    box.addView(input);
    LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
    p.setMargins(0, dp(8), 0, dp(6));
    parent.addView(box, p);
    return input;
  }

  private void dateButton(
      LinearLayout parent, String label, LocalDate initial, Consumer<LocalDate> changed) {
    final LocalDate[] current = {initial};
    MaterialButton button = button(label + "  " + initial, () -> {});
    button.setOnClickListener(
        v ->
            new DatePickerDialog(
                    this,
                    (view, y, m, d) -> {
                      current[0] = LocalDate.of(y, m + 1, d);
                      button.setText(label + "  " + current[0]);
                      changed.accept(current[0]);
                    },
                    current[0].getYear(),
                    current[0].getMonthValue() - 1,
                    current[0].getDayOfMonth())
                .show());
    parent.addView(button);
  }

  private void editAssignment(JSONObject existing) {
    JSONObject item = existing == null ? new JSONObject() : existing;
    LinearLayout content = dialogBody();
    EditText title = field(content, "의뢰·할 일", item.optString("title"), InputType.TYPE_CLASS_TEXT);
    EditText client = field(content, "의뢰인", item.optString("client"), InputType.TYPE_CLASS_TEXT);
    final LocalDate[] start = {
      LocalDate.parse(item.optString("start_date", LocalDate.now(KST).toString()))
    };
    final LocalDate[] due = {
      item.isNull("due_date")
          ? start[0]
          : LocalDate.parse(item.optString("due_date", start[0].toString()))
    };
    dateButton(content, "시작일", start[0], v -> start[0] = v);
    CheckBox hasDue = new CheckBox(this);
    hasDue.setText("마감일 지정");
    hasDue.setChecked(item.has("due_date") && !item.isNull("due_date"));
    content.addView(hasDue);
    int dueIndex = content.getChildCount();
    dateButton(content, "마감일", due[0], v -> due[0] = v);
    content.getChildAt(dueIndex).setVisibility(hasDue.isChecked() ? View.VISIBLE : View.GONE);
    hasDue.setOnCheckedChangeListener(
        (b, c) -> content.getChildAt(dueIndex).setVisibility(c ? View.VISIBLE : View.GONE));
    CheckBox hasFee = new CheckBox(this);
    hasFee.setText("의뢰비 입력");
    hasFee.setChecked(item.has("fee") && !item.isNull("fee"));
    content.addView(hasFee);
    EditText fee =
        field(
            content,
            "의뢰비 (원)",
            hasFee.isChecked() ? item.optString("fee") : "",
            InputType.TYPE_CLASS_NUMBER);
    fee.setEnabled(hasFee.isChecked());
    hasFee.setOnCheckedChangeListener((b, c) -> fee.setEnabled(c));
    CheckBox hasNet = new CheckBox(this);
    hasNet.setText("실수령액 별도 입력");
    hasNet.setChecked(item.has("net_fee") && !item.isNull("net_fee"));
    content.addView(hasNet);
    EditText net =
        field(
            content,
            "실수령액 (원)",
            hasNet.isChecked() ? item.optString("net_fee") : "",
            InputType.TYPE_CLASS_NUMBER);
    net.setEnabled(hasNet.isChecked());
    hasNet.setOnCheckedChangeListener((b, c) -> net.setEnabled(c));
    content.addView(text("진행 상태", 13, MUTED));
    Spinner status = new Spinner(this);
    status.setAdapter(
        new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, STATUS_NAMES));
    for (int i = 0; i < STATUS_IDS.length; i++)
      if (STATUS_IDS[i].equals(item.optString("status"))) status.setSelection(i);
    content.addView(status, new LinearLayout.LayoutParams(-1, dp(48)));
    EditText note =
        field(
            content,
            "메모",
            item.optString("note"),
            InputType.TYPE_CLASS_TEXT + InputType.TYPE_TEXT_FLAG_MULTI_LINE);
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle(existing == null ? "새 일정" : "일정 편집")
            .setView(scroll(content))
            .setNegativeButton("취소", null)
            .setPositiveButton("저장", null)
            .setNeutralButton(
                existing == null ? null : "일정 삭제",
                (d, w) ->
                    new AlertDialog.Builder(this)
                        .setTitle("일정 삭제")
                        .setMessage("이 일정을 삭제할까요?")
                        .setNegativeButton("취소", null)
                        .setPositiveButton(
                            "삭제",
                            (dd, ww) ->
                                command("assignment-delete", obj("id", item.optString("id")), null))
                        .show())
            .create();
    dialog.setOnShowListener(
        d ->
            dialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    v -> {
                      try {
                        JSONObject p = obj("title", title.getText().toString());
                        if (existing != null) put(p, "id", item.optString("id"));
                        put(p, "client", client.getText().toString());
                        put(p, "start_date", start[0].toString());
                        put(p, "due_date", hasDue.isChecked() ? due[0].toString() : null);
                        put(
                            p,
                            "fee",
                            hasFee.isChecked() ? Long.parseLong(fee.getText().toString()) : null);
                        put(
                            p,
                            "net_fee",
                            hasNet.isChecked() ? Long.parseLong(net.getText().toString()) : null);
                        put(p, "status", STATUS_IDS[status.getSelectedItemPosition()]);
                        put(p, "note", note.getText().toString());
                        command("assignment", p, r -> dialog.dismiss());
                      } catch (NumberFormatException e) {
                        Toast.makeText(this, "금액을 원 단위 숫자로 입력해주세요.", Toast.LENGTH_LONG).show();
                      }
                    }));
    dialog.show();
  }

  private void totals() {
    body.addView(heading("금액 합계"));
    space(body, 16);
    Spinner period = new Spinner(this);
    period.setAdapter(
        new ArrayAdapter<>(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            new String[] {"월별", "연간", "지정기간", "누적"}));
    period.setSelection(totalMode);
    period.setContentDescription("금액 합계 기간");
    body.addView(period, new LinearLayout.LayoutParams(-1, dp(48)));
    period.setOnItemSelectedListener(
        new android.widget.AdapterView.OnItemSelectedListener() {
          public void onNothingSelected(android.widget.AdapterView<?> p) {}

          public void onItemSelected(
              android.widget.AdapterView<?> p, View v, int position, long id) {
            if (totalMode != position) {
              totalMode = position;
              render();
            }
          }
        });
    LocalDate start = null, end = null;
    if (totalMode == 0) {
      monthRow(body);
      start = month.atDay(1);
      end = month.atEndOfMonth();
    } else if (totalMode == 1) {
      monthRow(body);
      start = LocalDate.of(month.getYear(), 1, 1);
      end = LocalDate.of(month.getYear(), 12, 31);
    } else if (totalMode == 2) {
      dateButton(
          body,
          "시작일",
          totalStart,
          d -> {
            totalStart = d;
            render();
          });
      dateButton(
          body,
          "종료일",
          totalEnd,
          d -> {
            totalEnd = d;
            render();
          });
      start = totalStart;
      end = totalEnd;
    }
    if (start != null && start.isAfter(end)) {
      space(body, 16);
      body.addView(text("종료일은 시작일 이후로 지정해주세요.", 14, Color.rgb(164, 63, 50)));
      return;
    }
    JSONArray items = array("assignments");
    space(body, 24);
    TextView feeValue = text("계산 중…", 22, TEXT),
        netValue = text("계산 중…", 22, GREEN),
        countValue = text("", 13, MUTED);
    body.addView(text("의뢰비", 13, MUTED));
    space(body, 8);
    body.addView(feeValue);
    space(body, 16);
    line(body);
    space(body, 16);
    body.addView(text("실수령액", 13, MUTED));
    space(body, 8);
    body.addView(netValue);
    space(body, 16);
    body.addView(countValue);
    JSONObject bounds = obj("start", start == null ? null : start.toString());
    put(bounds, "end", end == null ? null : end.toString());
    Bridge.IO.execute(
        () -> {
          try {
            JSONObject amounts = Bridge.command(this, "totals", bounds);
            ui.post(
                () -> {
                  if (isDestroyed()) return;
                  feeValue.setText(String.format(Locale.KOREA, "%,d원", amounts.optLong("fee")));
                  netValue.setText(String.format(Locale.KOREA, "%,d원", amounts.optLong("net_fee")));
                  countValue.setText("보수 수령·최종 완료 " + amounts.optInt("count") + "건 · 시작일 기준");
                });
          } catch (Exception error) {
            ui.post(() -> feeValue.setText("합계를 불러오지 못했습니다."));
          }
        });
    space(body, 24);
    body.addView(text("집계된 일정", 16, TEXT));
    space(body, 8);
    boolean found = false;
    for (int i = 0; i < items.length(); i++) {
      JSONObject a = items.optJSONObject(i);
      String status = a.optString("status"), date = a.optString("start_date");
      if (!(status.equals("paid") || status.equals("completed")) || a.optInt("archived") != 0)
        continue;
      if (start != null
          && (date.compareTo(start.toString()) < 0 || date.compareTo(end.toString()) > 0)) continue;
      found = true;
      MaterialButton entry =
          button(a.optString("title") + " · " + date, () -> assignmentDetails(a));
      entry.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
      body.addView(entry);
    }
    if (!found) body.addView(text("집계할 일정이 없습니다.", 14, MUTED));
  }

  private void toggleTracking() {
    if (prefs().getBoolean("tracking", false) && TrackingService.hasAccess(this)) {
      prefs().edit().putBoolean("tracking", false).apply();
      stopService(new Intent(this, TrackingService.class));
      render();
      return;
    }
    if (!TrackingService.hasAccess(this)) {
      new AlertDialog.Builder(this)
          .setTitle("앱 사용 정보 접근")
          .setMessage("선택한 앱의 실행 시간을 감지하려면 ‘모아’의 사용 정보 접근을 허용해주세요. 화면이 꺼지거나 잠기면 기록을 멈춥니다.")
          .setNegativeButton("취소", null)
          .setPositiveButton(
              "권한 설정", (d, w) -> startActivity(new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)))
          .show();
      return;
    }
    if (Build.VERSION.SDK_INT >= 33
        && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != android.content.pm.PackageManager.PERMISSION_GRANTED)
      requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 51);
    prefs().edit().putBoolean("tracking", true).apply();
    ContextCompat.startForegroundService(this, new Intent(this, TrackingService.class));
    render();
  }

  private void settings() {
    body.addView(heading("설정"));
    space(body, 16);
    body.addView(button("추적할 Android 앱 추가", this::pickApp));
    Map<String, JSONObject> targets = targetMap();
    boolean found = false;
    for (JSONObject t : targets.values()) {
      if (t.optInt("archived") != 0 || !t.optString("platform").equals("android")) continue;
      found = true;
      LinearLayout entry = row();
      CheckBox enabled = new CheckBox(this);
      enabled.setText(t.optString("name"));
      enabled.setChecked(t.optInt("enabled") == 1);
      enabled.setOnCheckedChangeListener(
          (b, c) -> {
            JSONObject p = obj("id", t.optString("id"));
            put(p, "enabled", c);
            command("target-toggle", p, null);
          });
      entry.addView(enabled, weight());
      entry.addView(
          button(
              "삭제",
              () ->
                  new AlertDialog.Builder(this)
                      .setTitle("추적 대상 삭제")
                      .setMessage(t.optString("name") + "을 추적 대상에서 삭제할까요? 이전 기록은 유지됩니다.")
                      .setNegativeButton("취소", null)
                      .setPositiveButton(
                          "삭제",
                          (d, w) -> command("target-delete", obj("id", t.optString("id")), null))
                      .show()));
      body.addView(entry);
    }
    if (!found) {
      space(body, 8);
      body.addView(text("등록된 Android 앱이 없습니다.", 14, MUTED));
    }
    space(body, 20);
    line(body);
    space(body, 16);
    body.addView(text("앱 전환 후 이어 기록", 15, TEXT));
    int[] graces = {0, 1, 3, 5, 10, 15, 30};
    String[] labels = {"사용 안 함", "1분", "3분", "5분", "10분", "15분", "30분"};
    Spinner grace = new Spinner(this);
    grace.setAdapter(
        new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, labels));
    int current =
        snapshot.optJSONObject("settings") == null
            ? 5
            : snapshot.optJSONObject("settings").optInt("switch_grace_minutes", 5);
    for (int i = 0; i < graces.length; i++) if (graces[i] == current) grace.setSelection(i);
    body.addView(grace, new LinearLayout.LayoutParams(-1, dp(48)));
    grace.setOnItemSelectedListener(
        new android.widget.AdapterView.OnItemSelectedListener() {
          public void onNothingSelected(android.widget.AdapterView<?> p) {}

          public void onItemSelected(
              android.widget.AdapterView<?> p, View v, int position, long id) {
            if (graces[position] != current)
              command("settings", obj("switch_grace_minutes", graces[position]), null);
          }
        });
    CheckBox boot = new CheckBox(this);
    boot.setText("기기 시작 시 자동 추적");
    boot.setChecked(!BuildConfig.DEBUG && prefs().getBoolean("start_at_boot", false));
    boot.setEnabled(!BuildConfig.DEBUG);
    boot.setOnCheckedChangeListener(
        (b, c) -> prefs().edit().putBoolean("start_at_boot", c).apply());
    body.addView(boot);
    if (BuildConfig.DEBUG) body.addView(text("테스트 앱은 부팅 시 자동 실행되지 않습니다.", 12, MUTED));
    space(body, 20);
    line(body);
    space(body, 16);
    body.addView(text("기기 연동", 16, TEXT));
    JSONObject sync = snapshot.optJSONObject("sync");
    space(body, 8);
    body.addView(text(sync == null ? "연동 안 됨" : sync.optString("message"), 14, MUTED));
    body.addView(button("텔레그램 연결 설정", this::syncDialog));
    space(body, 16);
    body.addView(text("모아 " + BuildConfig.VERSION_NAME, 12, MUTED));
  }

  private void pickApp() {
    Intent query = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
    List<ResolveInfo> resolved = getPackageManager().queryIntentActivities(query, 0);
    Map<String, ResolveInfo> unique = new HashMap<>();
    for (ResolveInfo r : resolved)
      if (!r.activityInfo.packageName.equals(getPackageName()))
        unique.put(r.activityInfo.packageName, r);
    List<ResolveInfo> apps = new ArrayList<>(unique.values());
    apps.sort(
        Comparator.comparing(
            r -> r.loadLabel(getPackageManager()).toString(), String.CASE_INSENSITIVE_ORDER));
    String[] names = new String[apps.size()];
    for (int i = 0; i < apps.size(); i++)
      names[i] = apps.get(i).loadLabel(getPackageManager()).toString();
    if (apps.isEmpty()) {
      new AlertDialog.Builder(this)
          .setMessage("선택할 수 있는 앱이 없습니다.")
          .setPositiveButton("확인", null)
          .show();
      return;
    }
    new AlertDialog.Builder(this)
        .setTitle("추적할 앱")
        .setItems(
            names,
            (d, w) -> {
              JSONObject p = obj("name", names[w]);
              put(p, "matcher", apps.get(w).activityInfo.packageName);
              command("target", p, null);
            })
        .setNegativeButton("취소", null)
        .show();
  }

  private void copy(String value) {
    ((ClipboardManager) getSystemService(CLIPBOARD_SERVICE))
        .setPrimaryClip(ClipData.newPlainText("모아 연동 키", value));
    Toast.makeText(this, "연동 키를 복사했습니다.", Toast.LENGTH_SHORT).show();
  }

  private void syncDialog() {
    LinearLayout content = dialogBody();
    JSONObject sync = snapshot.optJSONObject("sync");
    String phase = sync == null ? "setup" : sync.optString("phase", "setup");
    TextView status = text(sync == null ? "연동 안 됨" : sync.optString("message"), 14, TEXT);
    content.addView(status);
    space(content, 12);
    TextView disclosure =
        text(
            "텔레그램 API를 사용해 ‘저장된 메시지’에 암호화된 기록을 저장합니다. 각 기기에서 같은 계정과 연동 키를 사용하세요. 앱 실행 시, 연결 후 5분마다"
                + " 동기화합니다.",
            13,
            MUTED);
    content.addView(disclosure);
    space(content, 12);
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle("기기 연동")
            .setView(scroll(content))
            .setNegativeButton("닫기", null)
            .create();
    if (phase.equals("ready")) {
      content.addView(
          button(
              "지금 동기화",
              () -> {
                dialog.dismiss();
                command("sync-sync", new JSONObject(), null);
              }));
      content.addView(
          button(
              "연동 키 복사",
              () ->
                  command(
                      "sync-pairing", new JSONObject(), r -> copy(r.optString("pairing_key")))));
      content.addView(
          button(
              "연동 해제",
              () ->
                  new AlertDialog.Builder(this)
                      .setTitle("기기 연동 해제")
                      .setMessage("이 기기의 연동을 해제할까요? 기기에 저장된 기록은 유지됩니다.")
                      .setNegativeButton("취소", null)
                      .setPositiveButton(
                          "해제",
                          (d, w) -> {
                            dialog.dismiss();
                            command("sync-disconnect", new JSONObject(), null);
                          })
                      .show()));
    } else if (phase.equals("code") || phase.equals("password")) {
      EditText value =
          field(
              content,
              phase.equals("code") ? "텔레그램 인증번호" : "2단계 인증 비밀번호",
              "",
              InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
      content.addView(
          button(
              "확인",
              () -> {
                String secret = value.getText().toString();
                value.setText("");
                dialog.dismiss();
                command("sync-" + phase, obj("value", secret), r -> syncDialog());
              }));
      content.addView(
          button(
              "인증 다시 시작",
              () -> {
                dialog.dismiss();
                command("sync-cancel-auth", new JSONObject(), r -> syncDialog());
              }));
    } else {
      TextView link = text("API ID·API hash 발급: my.telegram.org/apps", 13, GREEN);
      link.setPadding(0, dp(12), 0, dp(12));
      link.setOnClickListener(
          v ->
              startActivity(
                  new Intent(
                      Intent.ACTION_VIEW, android.net.Uri.parse("https://my.telegram.org/apps"))));
      content.addView(link);
      EditText id = field(content, "API ID", "", InputType.TYPE_CLASS_NUMBER);
      EditText hash =
          field(
              content,
              "API hash",
              "",
              InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
      EditText phone = field(content, "전화번호 (+821012345678)", "", InputType.TYPE_CLASS_PHONE);
      EditText key =
          field(
              content,
              "연동 키",
              "",
              InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
      content.addView(
          button(
              "첫 기기: 새 연동 키 생성",
              () ->
                  command(
                      "new-key",
                      new JSONObject(),
                      r -> {
                        key.setText(r.optString("pairing_key"));
                        copy(r.optString("pairing_key"));
                      })));
      content.addView(
          button(
              "인증번호 받기",
              () -> {
                JSONObject p = obj("api_id", id.getText().toString());
                put(p, "api_hash", hash.getText().toString());
                put(p, "phone", phone.getText().toString());
                put(p, "pairing_key", key.getText().toString());
                dialog.dismiss();
                command("sync-setup", p, r -> syncDialog());
              }));
    }
    dialog.show();
  }
}
