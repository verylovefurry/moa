package com.verylovefurry.moa;

import android.content.Context;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

public final class Bridge {
  public static final ExecutorService IO = Executors.newSingleThreadExecutor();
  private static PyObject backend;

  public static synchronized PyObject module(Context context) {
    if (backend == null) {
      SecureStore.initialize(context);
      PyObject candidate = Python.getInstance().getModule("mobile_backend");
      candidate.callAttr("initialize", context.getFilesDir().getAbsolutePath());
      backend = candidate;
    }
    return backend;
  }

  public static JSONObject state(Context context) throws Exception {
    return new JSONObject(module(context).callAttr("state").toString());
  }

  public static JSONObject command(Context context, String action, JSONObject payload)
      throws Exception {
    JSONObject response =
        new JSONObject(module(context).callAttr("command", action, payload.toString()).toString());
    if (!response.optBoolean("ok"))
      throw new IllegalArgumentException(response.optString("error", "저장하지 못했습니다."));
    return response.getJSONObject("result");
  }
}
