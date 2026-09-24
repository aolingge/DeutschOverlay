"""Opt-in Azure continuous speech translation with a local usage cap."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import date
from pathlib import Path
from threading import Lock
from threading import Event
from typing import Callable

import numpy as np

from deutsch_overlay.captions import CaptionEvent


class OnlineUnavailable(RuntimeError):
    """Online recognition cannot start without an explicit usable setup."""


class OnlineLimitReached(RuntimeError):
    """The application-level daily online audio cap was reached."""


class OnlineBudget:
    def __init__(self, path: Path, limit_minutes: int, today: Callable[[], date] = date.today) -> None:
        if not 1 <= limit_minutes <= 1440:
            raise ValueError("online minute cap must be between 1 and 1440")
        self.path = path
        self.limit_seconds = limit_minutes * 60
        self.today = today
        self._lock = Lock()
        self._day = today().isoformat()
        self._used = 0.0
        self._last_saved = 0.0
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["day"] == self._day:
                    used = float(record["seconds"])
                    if not 0 <= used <= 86400:
                        raise ValueError("invalid online usage")
                    self._used = used
                    self._last_saved = used
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise OnlineUnavailable("online usage record is invalid") from exc

    @property
    def remaining_seconds(self) -> float:
        with self._lock:
            self._roll_day()
            return max(0.0, self.limit_seconds - self._used)

    def _roll_day(self) -> None:
        day = self.today().isoformat()
        if day != self._day:
            self._day = day
            self._used = 0.0
            self._last_saved = 0.0

    def allow(self, seconds: float) -> bool:
        if not 0 < seconds <= 60:
            raise ValueError("audio duration must be between 0 and 60 seconds")
        with self._lock:
            self._roll_day()
            if self._used + seconds > self.limit_seconds + 1e-8:
                return False
            self._used += seconds
            if self._used - self._last_saved >= 1:
                self._save()
            return True

    def close(self) -> None:
        with self._lock:
            if self._used != self._last_saved:
                self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump({"day": self._day, "seconds": self._used}, stream)
            os.replace(temp_path, self.path)
            self._last_saved = self._used
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def caption_from_azure_result(result, session_id: int, segment_id: str, language: str) -> CaptionEvent | None:
    code = language.lower().split("-")[0]
    if code not in {"de", "en", "zh"}:
        return None
    original = (result.text or "").strip()
    if not original:
        return None
    german = None
    if code != "de":
        german = (result.translations.get("de") or "").strip()
        if not german:
            return None
    return CaptionEvent(session_id, segment_id, code, original, german, True, time.monotonic())


class AzureEngine:
    def __init__(self, credential_store, budget: OnlineBudget, *, sdk=None) -> None:
        self.credential_store = credential_store
        self.budget = budget
        self.sdk = sdk
        self._stream = None
        self._recognizer = None
        self._active = False
        self._session_id = 0
        self._language_lock: str | None = None
        self._on_caption = None
        self._on_error = None
        self._sequence = 0
        self._interrupted = Event()

    def start(self, session_id: int, language_lock: str | None, on_caption, on_error) -> None:
        self._interrupted.clear()
        credentials = self.credential_store.get()
        if credentials is None:
            raise OnlineUnavailable("Azure credentials are not configured")
        if self.budget.remaining_seconds <= 0:
            raise OnlineLimitReached("daily online audio limit reached")
        if self._active:
            raise OnlineUnavailable("online recognition is already running")
        if self.sdk is None:
            import azure.cognitiveservices.speech as speechsdk

            self.sdk = speechsdk
        sdk = self.sdk
        config = sdk.translation.SpeechTranslationConfig(
            subscription=credentials.key, region=credentials.region
        )
        config.add_target_language("de")
        self._language_lock = language_lock if language_lock in {"de", "en", "zh"} else None
        autodetect = None
        if self._language_lock:
            config.speech_recognition_language = {"de": "de-DE", "en": "en-US", "zh": "zh-CN"}[
                self._language_lock
            ]
        else:
            config.set_property(sdk.PropertyId.SpeechServiceConnection_LanguageIdMode, "Continuous")
            autodetect = sdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=["de-DE", "en-US", "zh-CN"]
            )
        audio_format = sdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        self._stream = sdk.audio.PushAudioInputStream(stream_format=audio_format)
        audio_config = sdk.audio.AudioConfig(stream=self._stream)
        options = {"translation_config": config, "audio_config": audio_config}
        if autodetect is not None:
            options["auto_detect_source_language_config"] = autodetect
        self._recognizer = sdk.translation.TranslationRecognizer(**options)
        self._session_id = session_id
        self._on_caption = on_caption
        self._on_error = on_error
        self._recognizer.recognized.connect(self._recognized)
        self._recognizer.canceled.connect(self._canceled)
        try:
            self._recognizer.start_continuous_recognition_async().get()
        except Exception as exc:
            self.stop()
            raise OnlineUnavailable("Azure recognition could not start") from exc
        self._active = True

    def push_frame(self, frame: np.ndarray) -> None:
        if self._interrupted.is_set():
            raise OnlineUnavailable("Azure recognition was interrupted")
        if not self._active or self._stream is None:
            raise OnlineUnavailable("online recognition is not running")
        if frame.ndim != 1 or not len(frame):
            raise ValueError("online audio must be nonempty mono frames")
        if not self.budget.allow(len(frame) / 16000):
            self.stop()
            raise OnlineLimitReached("daily online audio limit reached")
        pcm = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
        try:
            self._stream.write(pcm)
        except Exception as exc:
            self.stop()
            raise OnlineUnavailable("Azure audio stream failed") from exc

    def _recognized(self, event) -> None:
        if self._interrupted.is_set() or not self._active:
            return
        result = event.result
        if result.reason != self.sdk.ResultReason.TranslatedSpeech:
            return
        if self._language_lock:
            language = self._language_lock
        else:
            language = self.sdk.AutoDetectSourceLanguageResult(result).language
        self._sequence += 1
        segment_id = str(getattr(result, "result_id", self._sequence))
        caption = caption_from_azure_result(result, self._session_id, segment_id, language)
        if caption is not None:
            self._on_caption(caption)

    def _canceled(self, _event) -> None:
        if self._active and not self._interrupted.is_set():
            self._interrupted.set()
            if self._on_error is not None:
                self._on_error("在线识别已中断，请检查网络或 Azure 服务状态")

    def stop(self) -> None:
        was_active = self._active
        self._active = False
        if was_active and self._recognizer is not None:
            try:
                self._recognizer.stop_continuous_recognition_async().get()
            except Exception:
                pass
        if self._stream is not None and hasattr(self._stream, "close"):
            self._stream.close()
        self.budget.close()
