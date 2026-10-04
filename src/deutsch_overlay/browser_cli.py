"""Command-line entry point for the local browser bridge.

Two jobs, both from the desktop side:

* ``--serve`` (default) runs the loopback HTTP bridge the extension talks to.
  It writes a handshake file next to the settings so the extension can learn
  the port and token without the user copying anything.
* ``--transcribe-file`` runs the same recognizer over a local media file and
  writes real subtitles with the model's own timings. This is the path for a
  video that has no caption track and no live audio to capture.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .browser_bridge import AsrBridgeService
from .browser_server import BridgeServer, BridgeServerConfig
from .engines.local import LocalEngine
from .transcript import TranscriptResult


def build_speech_segmenter(**options):
    from .audio import PositionedSpeechSegmenter, SileroSpeechDetector

    detector = SileroSpeechDetector(sample_rate=options["sample_rate"], frame_samples=options["frame_samples"])
    return PositionedSpeechSegmenter(**options, voice_detector=detector)


def default_state_dir() -> Path:
    override = os.environ.get("DEUTSCH_OVERLAY_STATE")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DeutschOverlay"


def default_handshake_path() -> Path:
    return default_state_dir() / "bridge.json"


def default_token_path() -> Path:
    return default_state_dir() / "bridge-token"


def write_handshake(path: Path, *, host: str, port: int, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocolVersion": 1,
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}",
        "token": token,
        "pid": os.getpid(),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _format_timestamp(milliseconds: int) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_srt(result: TranscriptResult) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(result.segments, start=1):
        blocks.append(
            "\n".join(
                (
                    str(index),
                    f"{_format_timestamp(segment.start_ms)} --> {_format_timestamp(segment.end_ms)}",
                    segment.text,
                )
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def transcribe_file(engine: LocalEngine, path: Path, *, language: str, translate: bool) -> dict:
    from faster_whisper.audio import decode_audio

    audio = decode_audio(str(path), sampling_rate=16000)
    result = engine.transcribe_segments(
        audio, language=None if language == "auto" else language, offset_samples=0
    )
    payload: dict = {
        "file": str(path),
        "language": result.language,
        "durationSeconds": result.duration_seconds,
        "device": result.device,
        "warning": result.warning,
        "segments": [],
    }
    for segment in result.segments:
        entry = segment.to_dict()
        if translate and result.language and result.language != "de":
            translation = engine.translate(segment.text, result.language)
            entry["german"] = translation.text
            entry["translationFailed"] = translation.failed
            entry["translationBackend"] = translation.backend
        payload["segments"].append(entry)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deutsch-overlay-bridge",
        description="本机识别桥接：为没有字幕的网页视频提供本地识别字幕",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--token", default="", help="固定令牌；默认读取或生成 bridge-token")
    parser.add_argument("--token-file", default="", help="令牌文件路径")
    parser.add_argument("--handshake", default="", help="握手文件路径（默认 LOCALAPPDATA/DeutschOverlay/bridge.json）")
    parser.add_argument("--no-handshake", action="store_true", help="不写握手文件")
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--print-token", action="store_true", help="打印令牌后退出")
    parser.add_argument("--transcribe-file", default="", help="直接转写本地音视频文件")
    parser.add_argument("--language", default="auto", choices=["auto", "de", "en", "zh"])
    parser.add_argument("--translate", action="store_true", help="同时输出德语译文")
    parser.add_argument("--srt", default="", help="SRT 输出路径")
    parser.add_argument("--json", default="", help="JSON 输出路径")
    parser.add_argument("--no-gpu", action="store_true", help="只用 CPU 识别")
    parser.add_argument("--no-prepare", action="store_true", help="不在启动时预热模型")
    parser.add_argument("--asr-model-path", type=Path, help="已准备好的本地 CTranslate2 Whisper 模型目录；不会下载")
    parser.add_argument("--chinese-script", choices=("simplified", "traditional", "raw"), default="simplified",
                        help="中文识别字形：默认简体；JSON 同时保留原始识别文本")
    parser.add_argument("--hotwords-file", type=Path, help="本地 JSON 术语表，键为 de/en/zh；每种语言最多 1000 字符")
    parser.add_argument("--speech-gate", choices=("silero", "energy"), default="silero",
                        help="实时分句检测：默认 Silero；energy 用于兼容性比较")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token_path = Path(args.token_file) if args.token_file else default_token_path()
    handshake_path = Path(args.handshake) if args.handshake else default_handshake_path()
    try:
        hotwords = json.loads(args.hotwords_file.read_text(encoding="utf-8-sig")) if args.hotwords_file else {}
        if not isinstance(hotwords, dict):
            raise ValueError("术语表必须是 JSON 对象")
        engine = LocalEngine(prefer_gpu=not args.no_gpu, asr_model_path=args.asr_model_path,
                             chinese_script=args.chinese_script, hotwords=hotwords)
    except (OSError, ValueError) as exc:
        # Terms may be private. Do not echo file contents or JSON error context.
        print(f"识别配置无效（{type(exc).__name__}）；请检查模型目录和术语表格式", file=sys.stderr)
        return 2

    if args.print_token:
        config = BridgeServerConfig(token=args.token, token_path=token_path)
        from .browser_server import load_token

        print(load_token(config))
        return 0

    if args.transcribe_file:
        path = Path(args.transcribe_file)
        if not path.exists():
            print(f"找不到文件：{path}", file=sys.stderr)
            return 2
        payload = transcribe_file(engine, path, language=args.language, translate=args.translate)
        if args.srt:
            from .transcript import TranscriptSegment

            synthetic = TranscriptResult(
                segments=tuple(TranscriptSegment.from_dict(item) for item in payload["segments"]),
                language=payload["language"],
            )
            Path(args.srt).write_text(format_srt(synthetic), encoding="utf-8")
        if args.json:
            Path(args.json).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(
            f"识别完成：{len(payload['segments'])} 段，语言 {payload['language']}，"
            f"设备 {payload['device']}"
        )
        if not args.srt and not args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    service = AsrBridgeService(engine, max_sessions=args.max_sessions,
                              segmenter_factory=build_speech_segmenter if args.speech_gate == "silero" else None)
    if not args.no_prepare:
        service.prepare(blocking=True)
    config = BridgeServerConfig(
        host=args.host, port=args.port, token=args.token, token_path=token_path
    )
    server = BridgeServer(service, config)
    server.start()
    if not args.no_handshake:
        try:
            write_handshake(
                handshake_path,
                host=config.host,
                port=server.httpd.config.port if server.httpd else config.port,
                token=server.token,
            )
        except OSError as exc:
            print(f"握手文件写入失败：{exc}", file=sys.stderr)
    print(f"本机识别桥接已启动：{server.url}")
    print(f"令牌文件：{token_path}")
    if not args.no_handshake:
        print(f"握手文件：{handshake_path}")
    if service.prepare_error:
        print(f"警告：{service.prepare_error}", file=sys.stderr)
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                import time

                time.sleep(0.5)
                continue
            command = line.strip().lower()
            if command in ("quit", "exit", "stop"):
                break
            if command == "health":
                print(json.dumps(service.health(), ensure_ascii=False))
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
