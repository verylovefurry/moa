# 모아 Android

Android 8.0 이상, ARM64 기기용 네이티브 앱입니다. x86_64 에뮬레이터에서도 실행됩니다.
웹뷰를 사용하지 않으며 PC와 같은 SQLite 저장·일정·금액 계산·동기화 코드를 사용합니다.

## 설치·자동 기록

릴리스의 `Moa-Android.apk`를 설치합니다. 설정에서 추적할 앱을 추가하고 **자동 추적 시작**을 누릅니다.
Android의 **사용 정보 접근**에서 모아를 허용해야 합니다. 기록 중에는 알림이 표시됩니다.
화면이 꺼지거나 잠기면 기록을 중단하며, 다른 앱으로 이동하면 설정한 시간 동안 이어 기록합니다.
알림이나 앱의 **추적 중지**로 중단합니다. 기기 시작 시 실행은 설정에서 선택할 수 있습니다.
테스트 패키지(`.dev`)는 부팅 시 자동 실행되지 않습니다.

Android는 일반 사용 정보 권한으로 전체 키보드·터치의 마지막 입력이나 브라우저 안의 개별 URL을 제공하지 않습니다.
따라서 모바일은 선택한 **앱의 사용 상태와 화면 켜짐·잠금 상태**를 기록합니다. PC의 프로그램·URL 기록은 모바일에서 함께 볼 수 있습니다.
일부 제조사의 배터리 절약 기능이 서비스를 중단하면 앱의 배터리 설정에서 백그라운드 실행을 허용해야 합니다.

## PC와 연결

1. 각 기기의 **기기 연동**을 엽니다. PC는 왼쪽 메뉴, Android는 설정에 있습니다.
2. [Telegram API 개발 도구](https://my.telegram.org/apps)에서 본인 API ID·API hash를 발급받습니다.
3. 첫 기기에서 연동 키를 생성해 별도로 보관합니다. 다른 기기에 **같은 키**를 입력합니다.
4. 두 기기에서 같은 Telegram 계정의 전화번호(국가 번호 포함)·인증번호·필요시 2단계 인증 비밀번호로 로그인합니다.
5. 연결 후 첫 동기화와 5분 간격 자동 동기화가 실행됩니다. **지금 동기화**로 즉시 반영할 수도 있습니다.

인증번호·비밀번호·API hash·연동 키는 채팅, 소스 코드, 이슈 등에 올리지 마세요.
로그인 세션과 연동 키는 Windows DPAPI 또는 Android Keystore로 보호해 해당 기기에 저장합니다.
다른 Telegram 앱의 세션을 읽거나 공유하지 않습니다. 다른 대화·연락처·알림은 조회하지 않습니다.
연동 키는 안전한 방법으로 다른 기기에 옮기고 별도로 보관하세요. 키를 동기화 파일과 함께 공개하면 암호화의 보호 효과가 사라집니다.
텔레그램 API의 로그인 정책·인증 방식·요청 제한은 Telegram이 관리합니다. 로그인에 실패하면 앱이 표시하는 오류를 확인하세요.

**저장된 메시지**에는 `MOA_SYNC_V1_…`로 시작하는 파일 메시지가 생성됩니다.
내용은 임의의 256비트 연동 키로 AES-GCM 암호화합니다. Telegram의 클라우드 저장 자체는 종단간 암호화가 아니므로 앱이 추가로 암호화합니다.
키를 잃으면 서버 파일을 해독할 수 없습니다. 연동 중에는 해당 파일을 편집·삭제하지 마세요.
새 기기는 과거 동기화 파일부터 읽으므로 기존 기록이 복원됩니다. 동기화 파일을 삭제한 경우 최신 로컬 백업을 먼저 보존해야 합니다.

메시지 시간 대신 **메시지 ID**로 수신 위치를 관리하고, 배치 ID로 중복 업로드를 방지합니다.
오프라인 변경은 기기에 먼저 저장하고 다시 연결되면 전송합니다. 다른 필드 수정은 합치고, 같은 필드는 나중에 업로드한 변경을 적용합니다.
변경 전 값은 로컬 DB의 `sync_conflicts`에 보존합니다. 시작일·마감일은 유효한 기간을 유지하도록 한 쌍으로 처리합니다.
기기별 자동 실행 설정, 권한, 브라우저 연결 토큰, 계정 인증 정보는 동기화하지 않습니다.
연동 해제는 해당 기기의 네트워크 연결·저장된 인증 정보를 지우며 작업·일정 기록이나 Telegram의 파일은 지우지 않습니다.
계정에서 세션 자체를 취소하려면 Telegram의 **설정 → 기기**에서 해당 Moa 세션을 종료할 수 있습니다.

## 데이터 보존

기록은 앱 내부 `files/moa.sqlite3`에 매초 커밋하고, 일별·첫 수신 전 SQLite 백업을 만듭니다.
오프라인에서도 기록·일정 편집·합계를 사용할 수 있습니다. 앱을 삭제하면 Android의 로컬 데이터와 키가 지워집니다.
기기 교체 전에 동기화를 완료하고 연동 키를 보관하세요. 기기별 동시 작업은 각각의 기록으로 보존됩니다.

## 빌드

JDK 17, Python 3.11, Android SDK 플랫폼 35를 설치하고 `JAVA_HOME`, `ANDROID_HOME`을 설정합니다.

```powershell
python tools/build_android.py
```

디버그 APK는 `output/package/Moa-Android-test.apk`에 생성됩니다.
Gradle 8.14.3(배포 SHA-256 고정), AGP 8.11.0, Chaquopy 17.2.0을 사용합니다.
Telethon의 순수 Python 의존성 PyAES는 공식 1.6.1 배포에서 any wheel로 빌드합니다.

정식 APK에는 본인의 지속적인 서명 키가 필요합니다. **키와 비밀번호를 저장소에 커밋하지 마세요.**
`android/signing.properties`(Git 제외)에 다음을 지정하고 키 파일은 `.local/` 등 비공개 위치에 보관합니다.

```properties
storeFile=../.local/android-signing.p12
storePassword=YOUR_PRIVATE_PASSWORD
keyAlias=moa
keyPassword=YOUR_PRIVATE_PASSWORD
```

```powershell
python tools/build_android.py --release
```

`release/Moa-Android.apk`와 SHA-256 파일을 생성합니다. 다음 업데이트에도 같은 서명 키를 사용해야 합니다.
Android 버전은 루트의 `version.py`와 일치하며 PC의 자동 업데이트는 기존 Windows 릴리스를 사용합니다.
Android는 APK를 내려받아 설치해 업데이트합니다.

```powershell
cd android
./gradlew.bat :app:lintDebug :app:connectedDebugAndroidTest
```

계정 로그인이 필요한 실제 Telegram 전송 테스트는 사용자 계정을 앱에서 연결한 후 수행합니다.
자동 테스트는 네트워크 대신 메시지 전송 어댑터를 사용하며 실제 Android SQLite·암호화·기기 보안 저장소를 검증합니다.

일반 UI 아이콘은 루트 `assets/reicon`의 검증된 Reicon Outline SVG를 VectorDrawable로 변환한 것입니다.
출처와 MIT 라이선스는 APK의 `assets/reicon-source.json`, `assets/reicon-LICENSE`에 포함됩니다.
