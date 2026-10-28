package com.verylovefurry.moa;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Telegram session and pairing key are bound to this app's Android Keystore. */
public final class SecureStore {
  // Only the application context is retained, never an Activity or its views.
  @android.annotation.SuppressLint("StaticFieldLeak")
  private static Context context;

  private static final String ALIAS = "moa.telegram.v1";

  public static synchronized void initialize(Context value) {
    context = value.getApplicationContext();
  }

  private static SecretKey key() throws Exception {
    KeyStore store = KeyStore.getInstance("AndroidKeyStore");
    store.load(null);
    if (!store.containsAlias(ALIAS)) {
      KeyGenerator generator =
          KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
      generator.init(
          new KeyGenParameterSpec.Builder(
                  ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
              .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
              .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
              .setKeySize(256)
              .build());
      generator.generateKey();
    }
    return (SecretKey) store.getKey(ALIAS, null);
  }

  public static synchronized String read() throws Exception {
    String encoded =
        context.getSharedPreferences("sync_secure", Context.MODE_PRIVATE).getString("payload", "");
    if (encoded.isEmpty()) return "{}";
    byte[] data = Base64.decode(encoded, Base64.NO_WRAP);
    if (data.length < 29) throw new IllegalStateException("연결 정보를 읽을 수 없습니다.");
    Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
    cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, data, 0, 12));
    return new String(cipher.doFinal(data, 12, data.length - 12), StandardCharsets.UTF_8);
  }

  public static synchronized void write(String value) throws Exception {
    if (value.equals("{}")) {
      if (!context
          .getSharedPreferences("sync_secure", Context.MODE_PRIVATE)
          .edit()
          .clear()
          .commit()) throw new IllegalStateException("연결 정보를 삭제하지 못했습니다.");
      return;
    }
    Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
    cipher.init(Cipher.ENCRYPT_MODE, key());
    byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
    byte[] payload = new byte[12 + encrypted.length];
    System.arraycopy(cipher.getIV(), 0, payload, 0, 12);
    System.arraycopy(encrypted, 0, payload, 12, encrypted.length);
    if (!context
        .getSharedPreferences("sync_secure", Context.MODE_PRIVATE)
        .edit()
        .putString("payload", Base64.encodeToString(payload, Base64.NO_WRAP))
        .commit()) throw new IllegalStateException("연결 정보를 저장하지 못했습니다.");
  }
}
