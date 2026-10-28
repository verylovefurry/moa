"""Telegram user API transport. Only the current account's Saved Messages is used."""
from __future__ import annotations

import asyncio
from io import BytesIO
import re
import threading
import time

from sync_core import MAX_FILE, SyncJournal, caption_prefix, decode_batch, encode_batch, key_bytes
from version import APP_VERSION

SYNC_INTERVAL = 300


class TelegramTransport:
    def __init__(self, client, pairing_key):
        self.client = client
        self.key = pairing_key
        self.prefix = caption_prefix(pairing_key)

    async def receive(self, after):
        # getHistory preserves order without depending on Telegram search indexing.
        async for message in self.client.iter_messages("me", min_id=after, reverse=True):
            caption = message.message or ""
            batch = None
            if caption.startswith(self.prefix + " "):
                if not message.document or not message.file or message.file.size > MAX_FILE:
                    raise ValueError("저장된 메시지의 모아 동기화 파일을 확인해주세요.")
                payload = await self.client.download_media(message, bytes)
                batch = decode_batch(payload, self.key)
                if caption != self.prefix + " " + batch["batch_id"]:
                    raise ValueError("동기화 파일과 메시지 정보가 일치하지 않습니다.")
            yield message.id, batch

    async def send(self, batch):
        file = BytesIO(encode_batch(batch, self.key))
        file.name = f"moa-sync-{batch['batch_id']}.bin"
        await self.client.send_file("me", file, caption=self.prefix + " " + batch["batch_id"],
                                    force_document=True, parse_mode=None)


class SyncEngine:
    def __init__(self, journal, lock, transport):
        self.journal, self.lock, self.transport = journal, lock, transport

    async def pull(self):
        with self.lock:
            cursor = self.journal.get("cursor", 0)
        async for message_id, batch in self.transport.receive(cursor):
            with self.lock:
                if batch is not None:
                    self.journal.apply(batch, message_id)
                else:
                    with self.journal.db:
                        self.journal.set("cursor", message_id)

    async def synchronize(self):
        await self.pull()
        with self.lock:
            self.journal.acknowledge()
            self.journal.bootstrap()
            # A running tracker keeps changing every second. Bound this exchange
            # to its starting revision so new ticks wait for the next interval.
            cutoff = self.journal.db.execute("SELECT revision FROM sync_control WHERE id=1").fetchone()[0]
        for _ in range(40):
            with self.lock:
                batch = self.journal.pending(cutoff)
            if batch is None:
                break
            await self.transport.send(batch)
            await self.pull()
            with self.lock:
                next_batch = self.journal.pending(cutoff)
                if next_batch and next_batch["batch_id"] == batch["batch_id"]:
                    # A successful upload must become observable before acknowledging it.
                    raise RuntimeError("업로드 확인을 기다리고 있습니다. 잠시 후 다시 동기화합니다.")
        with self.lock:
            self.journal.set("last_sync", time.time())
            self.journal.db.commit()
            return self.journal.status()


def public_error(exc):
    """Never surface RPC arguments, phone numbers, codes, or session material."""
    from telethon import errors
    if isinstance(exc, errors.FloodWaitError):
        return f"텔레그램 요청 제한: {exc.seconds}초 후 다시 시도해주세요."
    if isinstance(exc, (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError)):
        return "인증번호가 올바르지 않거나 만료되었습니다. 번호를 확인하거나 인증을 다시 시작해주세요."
    if isinstance(exc, errors.PasswordHashInvalidError):
        return "2단계 인증 비밀번호를 확인해주세요."
    if isinstance(exc, (errors.ApiIdInvalidError, errors.PhoneNumberInvalidError)):
        return "API ID·API hash 또는 전화번호를 확인해주세요."
    if isinstance(exc, (errors.AuthKeyUnregisteredError, errors.SessionRevokedError)):
        return "텔레그램 연결이 만료되었습니다. 연동을 해제한 뒤 다시 로그인해주세요."
    if isinstance(exc, (OSError, asyncio.TimeoutError, ConnectionError)):
        return "텔레그램에 연결할 수 없습니다. 기록은 기기에 저장되며 다시 연결되면 동기화합니다."
    if isinstance(exc, ValueError):
        # Only messages authored by our validation code are safe to display.
        text = str(exc)
        if re.search(r"[가-힣]", text) and len(text) < 200:
            return text
    return "동기화를 완료하지 못했습니다. 연결 상태를 확인하고 다시 시도해주세요."


class SyncService:
    """One asyncio thread owns each Telethon session; the UI never runs network I/O."""

    def __init__(self, store, lock, load_config, save_config, backup=None, platform="Windows"):
        self.store, self.lock = store, lock
        self.load_config, self.save_config, self.backup = load_config, save_config, backup
        self.platform = platform
        self.journal = SyncJournal(store.db)
        self.client = None
        self.config = {}
        self.phone_code_hash = None
        self._status = {"connected": False, "busy": False, "phase": "setup", "message": "연동 안 됨", "last_sync": None, "pending": 0}
        self._status_lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = False
        self.thread = threading.Thread(target=self._thread_main, name="MoaTelegram", daemon=True)
        self.thread.start()
        self._ready.wait(5)

    def _thread_main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.operation_lock = asyncio.Lock()
        self._ready.set()
        self.auto_task = self.loop.create_task(self._automatic())
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    def status(self):
        with self._status_lock:
            return dict(self._status)

    def _update(self, **values):
        with self._status_lock:
            self._status.update(values)

    def submit(self, action, params=None):
        if self._closed or not self._ready.is_set():
            raise RuntimeError("연동 서비스가 종료되었습니다.")
        return asyncio.run_coroutine_threadsafe(self._perform(action, params or {}), self.loop)

    async def _new_client(self, config):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        if self.client:
            await self.client.disconnect()
        self.client = TelegramClient(StringSession(config.get("session", "")), int(config["api_id"]), config["api_hash"],
                                     device_model=f"Moa {self.platform}", system_version=self.platform, app_version=APP_VERSION,
                                     receive_updates=False, timeout=15, request_retries=1, connection_retries=1,
                                     flood_sleep_threshold=0, auto_reconnect=True)
        await asyncio.wait_for(self.client.connect(), 30)

    async def _automatic(self):
        try:
            self.config = self.load_config() or {}
            if self.config.get("session"):
                self._update(connected=True, phase="ready", message="동기화 대기")
        except Exception:
            self._update(message="연결 정보를 읽지 못했습니다. 기기 연동을 다시 설정해주세요.")
        while True:
            if self.config.get("session"):
                try:
                    await self._perform("sync", {})
                except Exception:
                    pass  # _perform has already recorded a sanitized, visible error.
            await asyncio.sleep(SYNC_INTERVAL)

    async def _perform(self, action, params):
        async with self.operation_lock:
            self._update(busy=True)
            try:
                result = await self._action(action, params)
                return result
            except Exception as exc:
                message = public_error(exc)
                self._update(message=message)
                raise ValueError(message) from None
            finally:
                self._update(busy=False)

    async def _action(self, action, params):
        from telethon import errors
        if action == "setup":
            api_id = str(params.get("api_id", "")).strip()
            api_hash = str(params.get("api_hash", "")).strip()
            phone = str(params.get("phone", "")).strip().replace(" ", "").replace("-", "")
            pairing_key = str(params.get("pairing_key", "")).strip()
            if not api_id.isdigit() or not 0 < int(api_id) < 2**31 or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
                raise ValueError("텔레그램 API ID와 API hash를 확인해주세요.")
            if not re.fullmatch(r"\+[1-9][0-9]{6,14}", phone):
                raise ValueError("국가 번호를 포함한 전화번호를 입력해주세요. 예: +821012345678")
            key_bytes(pairing_key)
            if self.config.get("session"):
                raise ValueError("이미 연결되어 있습니다. 기존 연동을 해제한 뒤 다시 설정해주세요.")
            self.config = {"api_id": int(api_id), "api_hash": api_hash, "phone": phone, "pairing_key": pairing_key}
            await self._new_client(self.config)
            sent = await self.client.send_code_request(phone)
            self.phone_code_hash = sent.phone_code_hash
            self._update(phase="code", message="텔레그램에서 받은 인증번호를 입력해주세요.")
            return {"phase": "code"}
        if action in ("code", "password"):
            if not self.client or not self.phone_code_hash:
                raise ValueError("전화번호 인증을 먼저 시작해주세요.")
            try:
                if action == "code":
                    await self.client.sign_in(phone=self.config["phone"], code=str(params.get("value", "")).strip(), phone_code_hash=self.phone_code_hash)
                else:
                    await self.client.sign_in(password=str(params.get("value", "")))
            except errors.SessionPasswordNeededError:
                self._update(phase="password", message="텔레그램 2단계 인증 비밀번호를 입력해주세요.")
                return {"phase": "password"}
            self.config["session"] = self.client.session.save()
            self.save_config(self.config)
            with self.lock:
                self.journal.connect(self.config["pairing_key"])
            self._update(connected=True, phase="ready", message="연결됨 · 첫 동기화 중")
            await self._sync()
            return {"phase": "ready"}
        if action == "sync":
            return await self._sync()
        if action == "cancel-auth":
            if self.config.get("session"):
                raise ValueError("이미 연결되어 있습니다.")
            if self.client:
                await self.client.disconnect()
                self.client = None
            self.config = {}
            self.phone_code_hash = None
            self._update(phase="setup", message="전화번호 인증을 다시 시작해주세요.")
            return {"phase": "setup"}
        if action == "pairing":
            return {"pairing_key": self.config.get("pairing_key", "")}
        if action == "disconnect":
            if self.client:
                await self.client.disconnect()
                self.client = None
            self.save_config({})
            self.config = {}
            self.phone_code_hash = None
            with self.lock:
                self.journal.disconnect()
            self._update(connected=False, phase="setup", message="연동 안 됨", last_sync=None, pending=0)
            return {"phase": "setup"}
        raise ValueError("요청한 연동 기능을 찾을 수 없습니다.")

    async def _sync(self):
        if not self.config.get("session"):
            raise ValueError("텔레그램 연결을 먼저 설정해주세요.")
        if not self.client or not self.client.is_connected():
            await self._new_client(self.config)
        if not await self.client.is_user_authorized():
            raise ValueError("텔레그램 연결이 만료되었습니다. 다시 로그인해주세요.")
        with self.lock:
            first = not self.journal.get("bootstrapped")
            self.journal.connect(self.config["pairing_key"])
        if first and self.backup:
            self.backup()
        self._update(message="동기화 중")
        engine = SyncEngine(self.journal, self.lock, TelegramTransport(self.client, self.config["pairing_key"]))
        result = await engine.synchronize()
        self._update(connected=True, phase="ready", message="동기화 완료", last_sync=time.time(), pending=result["pending"])
        return {"phase": "ready", **result}

    def close(self):
        if self._closed:
            return
        self._closed = True
        async def shutdown():
            self.auto_task.cancel()
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()
            if self.client:
                await self.client.disconnect()
        future = asyncio.run_coroutine_threadsafe(shutdown(), self.loop)
        try:
            future.result(8)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError("연동 서비스를 종료하지 못했습니다.")
