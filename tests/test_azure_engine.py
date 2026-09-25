from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from deutsch_overlay.credentials import AzureCredentialStore, AzureCredentials
from deutsch_overlay.engines.azure import (
    AzureEngine,
    OnlineBudget,
    OnlineLimitReached,
    OnlineUnavailable,
    caption_from_azure_result,
)


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, name, value):
        self.values[(service, name)] = value

    def get_password(self, service, name):
        return self.values.get((service, name))


def test_credentials_use_keyring_and_do_not_show_secret_in_repr():
    backend = FakeKeyring()
    store = AzureCredentialStore(backend)
    store.save("eastasia", "a" * 32)
    credentials = store.get()
    assert credentials == AzureCredentials("eastasia", "a" * 32)
    assert "a" * 32 not in repr(credentials)


def test_budget_persists_daily_usage_and_refuses_extra_audio(tmp_path):
    path = tmp_path / "usage.json"
    budget = OnlineBudget(path, limit_minutes=1, today=lambda: date(2026, 9, 25))
    assert budget.allow(59)
    assert not budget.allow(2)
    budget.close()
    restarted = OnlineBudget(path, limit_minutes=1, today=lambda: date(2026, 9, 25))
    assert restarted.remaining_seconds == 1
    assert restarted.allow(1)
    assert not restarted.allow(0.1)


def test_budget_resets_on_next_day(tmp_path):
    path = tmp_path / "usage.json"
    first = OnlineBudget(path, 1, today=lambda: date(2026, 9, 25))
    first.allow(60)
    first.close()
    second = OnlineBudget(path, 1, today=lambda: date(2026, 9, 26))
    assert second.remaining_seconds == 60


@pytest.mark.parametrize(
    "language,original,german,expected",
    [
        ("de-DE", "Guten Tag", "Good day", ("de", "Guten Tag", None)),
        ("en-US", "Hello", "Hallo", ("en", "Hello", "Hallo")),
        ("zh-CN", "你好", "Hallo", ("zh", "你好", "Hallo")),
    ],
)
def test_azure_result_routing(language, original, german, expected):
    result = SimpleNamespace(text=original, translations={"de": german})
    event = caption_from_azure_result(result, 2, "r1", language)
    assert (event.language, event.original, event.german) == expected


def test_unsupported_or_untranslated_result_is_ignored():
    result = SimpleNamespace(text="Bonjour", translations={"de": "Hallo"})
    assert caption_from_azure_result(result, 1, "r", "fr-FR") is None
    result = SimpleNamespace(text="Hello", translations={})
    assert caption_from_azure_result(result, 1, "r", "en-US") is None


def test_online_start_without_credentials_never_creates_sdk(tmp_path):
    class NoCredentials:
        def get(self):
            return None

    engine = AzureEngine(NoCredentials(), OnlineBudget(tmp_path / "usage.json", 1))
    with pytest.raises(OnlineUnavailable, match="credentials"):
        engine.start(1, None, lambda _event: None, lambda _error: None)


def test_online_limit_stops_before_sending_audio(tmp_path):
    class FakeStream:
        def __init__(self):
            self.writes = []

        def write(self, data):
            self.writes.append(data)

    budget = OnlineBudget(tmp_path / "usage.json", 1)
    assert budget.allow(60)
    engine = AzureEngine(object(), budget)
    engine._stream = FakeStream()
    engine._active = True
    with pytest.raises(OnlineLimitReached):
        engine.push_frame(np.zeros(1600, dtype=np.float32))
    assert engine._stream.writes == []


class FakeSignal:
    def connect(self, callback):
        self.callback = callback

    def emit(self, result):
        self.callback(SimpleNamespace(result=result))


class FakeFuture:
    def get(self):
        return None


class FakeConfig:
    def __init__(self, **kwargs):
        self.credentials = kwargs
        self.targets = []
        self.properties = {}
        self.speech_recognition_language = None

    def add_target_language(self, language):
        self.targets.append(language)

    def set_property(self, key, value):
        self.properties[key] = value


class FakeStream:
    def __init__(self, **_kwargs):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)

    def close(self):
        self.closed = True


class FakeRecognizer:
    def __init__(self, **options):
        self.options = options
        self.recognized = FakeSignal()
        self.recognizing = FakeSignal()
        self.canceled = FakeSignal()

    def start_continuous_recognition_async(self):
        return FakeFuture()

    def stop_continuous_recognition_async(self):
        return FakeFuture()


def fake_sdk():
    return SimpleNamespace(
        translation=SimpleNamespace(SpeechTranslationConfig=FakeConfig, TranslationRecognizer=FakeRecognizer),
        languageconfig=SimpleNamespace(AutoDetectSourceLanguageConfig=lambda **kw: kw),
        audio=SimpleNamespace(
            AudioStreamFormat=lambda **kw: kw,
            PushAudioInputStream=FakeStream,
            AudioConfig=lambda **kw: kw,
        ),
        PropertyId=SimpleNamespace(SpeechServiceConnection_LanguageIdMode="language-mode"),
        ResultReason=SimpleNamespace(TranslatedSpeech="translated", TranslatingSpeech="translating"),
        AutoDetectSourceLanguageResult=lambda result: SimpleNamespace(language=result.language),
    )


def test_online_stream_uses_auto_language_and_emits_caption(tmp_path):
    class Store:
        def get(self):
            return AzureCredentials("eastasia", "a" * 32)

    sdk = fake_sdk()
    engine = AzureEngine(Store(), OnlineBudget(tmp_path / "usage.json", 1), sdk=sdk)
    captions = []
    errors = []
    engine.start(3, None, captions.append, errors.append)
    config = engine._recognizer.options["translation_config"]
    assert config.targets == ["de"]
    assert config.properties == {"language-mode": "Continuous"}
    assert "auto_detect_source_language_config" in engine._recognizer.options
    engine.push_frame(np.ones(1600, dtype=np.float32) * 0.2)
    assert len(engine._stream.writes[0]) == 3200
    engine._recognizer.recognized.emit(SimpleNamespace(
        reason="translated", text="Hello", translations={"de": "Hallo"},
        language="en-US", result_id="one",
    ))
    assert len(captions) == 1 and captions[0].german == "Hallo"
    engine._recognizer.canceled.emit(SimpleNamespace())
    assert errors == ["在线识别已中断，请检查网络或 Azure 服务状态"]
    with pytest.raises(OnlineUnavailable, match="interrupted"):
        engine.push_frame(np.ones(1600, dtype=np.float32) * 0.2)
    engine.stop()
    assert engine._stream.closed


def test_online_language_lock_skips_auto_detection(tmp_path):
    class Store:
        def get(self):
            return AzureCredentials("eastasia", "a" * 32)

    engine = AzureEngine(Store(), OnlineBudget(tmp_path / "usage.json", 1), sdk=fake_sdk())
    engine.start(1, "de", lambda _event: None, lambda _error: None)
    options = engine._recognizer.options
    assert options["translation_config"].speech_recognition_language == "de-DE"
    assert "auto_detect_source_language_config" not in options
    engine.stop()


def test_locked_chinese_online_stream_shows_provisional_then_final_translation(tmp_path):
    class Store:
        def get(self):
            return AzureCredentials("eastasia", "a" * 32)

    captions = []
    engine = AzureEngine(Store(), OnlineBudget(tmp_path / "usage.json", 1), sdk=fake_sdk())
    engine.start(1, "zh", captions.append, lambda _error: None)
    engine._recognizer.recognizing.emit(SimpleNamespace(
        reason="translating", text="你好", translations={"de": "Hallo"},
    ))
    engine._recognizer.recognized.emit(SimpleNamespace(
        reason="translated", text="你好，世界", translations={"de": "Hallo, Welt"},
    ))
    assert [(item.german, item.final) for item in captions] == [("Hallo", False), ("Hallo, Welt", True)]
    assert captions[0].segment_id == captions[1].segment_id
    engine.stop()


def test_online_no_match_discards_provisional_segment(tmp_path):
    class Store:
        def get(self):
            return AzureCredentials("eastasia", "a" * 32)

    engine = AzureEngine(Store(), OnlineBudget(tmp_path / "usage.json", 1), sdk=fake_sdk())
    engine.start(1, "zh", lambda _caption: None, lambda _error: None)
    engine._recognizer.recognizing.emit(SimpleNamespace(
        reason="translating", text="你好", translations={"de": "Hallo"},
    ))
    assert engine._partial_segment_id is not None
    engine._recognizer.recognized.emit(SimpleNamespace(reason="no-match"))
    assert engine._partial_segment_id is None
    engine.stop()
