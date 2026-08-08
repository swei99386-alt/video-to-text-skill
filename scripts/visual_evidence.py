#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视频视觉证据旁路。

默认复用 mcp-video-analyzer 的 CLI，把视频帧、OCR 和时间线整理成稳定的
本地证据包。这个脚本不负责替换现有的字幕/Whisper 转写链。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ENGINE = "mcp-video-analyzer@0.8.0"
SCHEMA_VERSION = "1.0"


def configure_console() -> bool:
    """Windows 命令行统一使用 UTF-8，避免中文进度被 GBK 误解码。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return True


configure_console()


def build_runtime_env() -> dict[str, str]:
    """让 Node 子进程也能找到 Skill 已使用的 portable yt-dlp。"""
    env = os.environ.copy()
    scripts_dir = Path(sys.executable).resolve().parent / "Scripts"
    if scripts_dir.is_dir():
        env["PATH"] = str(scripts_dir) + os.pathsep + env.get("PATH", "")
    return env


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?", text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int((match.group(4) or "0").ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _time_seconds(item: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in item:
            value = _number(item[key])
            if value is not None:
                if key.endswith("_ms") or key in {"timestamp_ms", "startMs", "endMs"}:
                    return value / 1000
                return value
    return None


def _text(item: dict[str, Any]) -> str:
    for key in ("text", "transcript", "content", "description", "value"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _relative_frame_path(value: Any, workdir: Path | None = None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("\\", "/")
    if workdir:
        root = str(workdir).replace("\\", "/").rstrip("/") + "/"
        if raw.lower().startswith(root.lower()):
            return raw[len(root) :]
    for marker in ("/关键帧/", "/frames/", "/frame/"):
        index = raw.lower().find(marker.lower())
        if index >= 0:
            return raw[index + 1 :]
    return raw


def _record(
    *,
    evidence_type: str,
    item: dict[str, Any],
    index: int,
    workdir: Path | None = None,
    frame_path: str | None = None,
    model_interpretation: bool = False,
) -> dict[str, Any]:
    start = _time_seconds(item, "start", "startTime", "time", "timestamp", "start_ms", "startMs") or 0.0
    end = _time_seconds(item, "end", "endTime", "end_ms", "endMs")
    if end is None:
        end = start
    record: dict[str, Any] = {
        "evidence_id": f"{evidence_type}-{index:04d}",
        "type": evidence_type,
        "start_ms": round(start * 1000),
        "end_ms": round(max(start, end) * 1000),
        "frame_path": frame_path or _relative_frame_path(item.get("filePath") or item.get("path"), workdir),
        "text": _text(item),
        "confidence": item.get("confidence"),
        "generator": ENGINE,
        "is_model_interpretation": model_interpretation,
    }
    if evidence_type == "visual" and not record["text"]:
        record["text"] = "关键画面"
    return record


def _parse_timestamped_transcript(path: Path) -> list[dict[str, Any]]:
    """读取现有 SRT/VTT 时间轴，不重新识别语音。"""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current and current.get("text"):
            current["text"] = re.sub(r"<[^>]+>", "", " ".join(current["text"])).strip()
            if current["text"]:
                segments.append(current)
        current = None

    for raw_line in lines:
        line = raw_line.strip()
        if "-->" in line:
            flush()
            start_text, end_text = [part.strip().split()[0] for part in line.split("-->", 1)]
            start = _number(start_text)
            end = _number(end_text)
            if start is not None:
                current = {"start": start, "end": end if end is not None else start, "text": []}
            continue
        if current is None:
            continue
        if not line or line.isdigit() or line.upper().startswith("WEBVTT"):
            if not line:
                flush()
            continue
        current["text"].append(line)
    flush()
    return segments


def _merge_existing_transcript(raw: dict[str, Any], workdir: Path) -> dict[str, Any]:
    """视觉引擎没返回语音时，接回 grab.py 已经生成的时间轴字幕。"""
    existing = raw.get("transcript")
    if existing:
        return raw
    candidates = [workdir / "transcript.srt", workdir / "audio.srt"]
    candidates.extend(sorted(workdir.glob("*.srt")))
    candidates.extend(sorted(workdir.glob("*.vtt")))
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        segments = _parse_timestamped_transcript(path)
        if segments:
            merged = dict(raw)
            merged["transcript"] = segments
            merged["transcript_source"] = path.name
            warnings = list(merged.get("warnings") or [])
            warnings.append(f"Transcript merged from existing sidecar: {path.name}")
            merged["warnings"] = warnings
            return merged
    return raw


def normalize_analysis(
    raw: dict[str, Any],
    *,
    source: str,
    engine: str = ENGINE,
    workdir: Path | None = None,
) -> dict[str, Any]:
    """把候选项目输出映射成稳定、可追溯的本地证据格式。"""
    evidence: list[dict[str, Any]] = []

    transcript = raw.get("transcript") or []
    if isinstance(transcript, dict):
        transcript = transcript.get("segments") or transcript.get("entries") or []
    for index, item in enumerate(transcript, 1):
        if isinstance(item, dict):
            evidence.append(_record(evidence_type="speech", item=item, index=index, workdir=workdir))

    frames = raw.get("frames") or []
    if isinstance(frames, dict):
        frames = frames.get("items") or frames.get("entries") or []
    frame_links: list[tuple[float, str]] = []
    for index, item in enumerate(frames, 1):
        if isinstance(item, dict):
            record = _record(evidence_type="visual", item=item, index=index, workdir=workdir)
            evidence.append(record)
            if record["frame_path"]:
                frame_links.append((record["start_ms"] / 1000, record["frame_path"]))

    ocr_results = raw.get("ocrResults") or raw.get("ocr_results") or []
    if isinstance(ocr_results, dict):
        ocr_results = ocr_results.get("items") or ocr_results.get("entries") or []
    for index, item in enumerate(ocr_results, 1):
        if isinstance(item, dict):
            record = _record(evidence_type="ocr", item=item, index=index, workdir=workdir)
            if record["frame_path"] is None and frame_links:
                ocr_time = record["start_ms"] / 1000
                _, nearest_path = min(frame_links, key=lambda pair: abs(pair[0] - ocr_time))
                record["frame_path"] = nearest_path
            evidence.append(record)

    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "engine": engine,
        "metadata": raw.get("metadata") or {},
        "transcript_source": raw.get("transcript_source"),
        "warnings": raw.get("warnings") or [],
        "evidence": evidence,
        "timeline": raw.get("timeline") or [],
        "raw_ai_summary": raw.get("aiSummary"),
    }


def build_cli_command(
    *,
    source: str,
    output_dir: str | Path,
    detail: str = "standard",
    max_frames: int | None = None,
    max_width: int | None = None,
    ocr_language: str | None = None,
    model: str | None = None,
    language: str | None = None,
) -> list[str]:
    # PowerShell 能启动 npx.ps1，但 Python 的 CreateProcess 不能直接启动
    # PowerShell 包装脚本；Windows 必须明确调用 npx.cmd。
    npx = "npx.cmd" if os.name == "nt" else "npx"
    command = [npx, "-y", ENGINE, "analyze", str(source), "--detail", detail]
    if max_frames is not None:
        command.extend(["--max-frames", str(max_frames)])
    if max_width is not None:
        command.extend(["--max-width", str(max_width)])
    if ocr_language:
        command.extend(["--ocr-language", ocr_language])
    if model:
        command.extend(["--model", model])
    if language:
        command.extend(["--language", language])
    command.extend(["--fields", "metadata,transcript,frames,ocrResults,timeline,aiSummary"])
    command.extend(["--out", str(output_dir)])
    return command


def _iter_items(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        return (item for item in value if isinstance(item, dict))
    if isinstance(value, dict):
        return (value,)
    return ()


def _timeline_line(item: dict[str, Any]) -> str:
    time_value = _time_seconds(item, "time", "timestamp", "start", "start_ms", "startMs")
    stamp = "未知时间" if time_value is None else f"{time_value:.3f}s"
    text = _text(item) or json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    return f"- `{stamp}` {text}"


def _make_report(evidence: dict[str, Any], raw: dict[str, Any], elapsed_seconds: float) -> str:
    metadata = evidence.get("metadata") or {}
    records = evidence.get("evidence") or []
    speech = [item for item in records if item["type"] == "speech"]
    visual = [item for item in records if item["type"] == "visual"]
    ocr = [item for item in records if item["type"] == "ocr"]
    warnings = evidence.get("warnings") or []
    timeline = list(_iter_items(raw.get("timeline") or []))
    summary = raw.get("aiSummary")

    lines = [
        "# 视频理解报告",
        "",
        "## 处理信息",
        "",
        f"- 来源：`{evidence['source']}`",
        f"- 引擎：`{evidence['engine']}`",
        f"- 时长：`{metadata.get('duration', '未知')}`",
        f"- 本次视觉处理耗时：`{elapsed_seconds:.2f} 秒`",
        f"- 语音证据：`{len(speech)}` 条；关键帧：`{len(visual)}` 张；OCR：`{len(ocr)}` 条",
        "",
        "## 原始视觉证据",
        "",
        "关键帧和 OCR 是从视频画面中提取的可回看证据，不能被模型摘要替代。",
        "",
    ]
    for item in visual:
        path = item.get("frame_path") or "未返回路径"
        lines.append(f"- `{item['start_ms'] / 1000:.3f}s`：[{path}]({path})")
    if not visual:
        lines.append("- 未提取到关键帧。")

    lines.extend(["", "## OCR", ""])
    for item in ocr:
        text = item.get("text") or "（未识别到文字）"
        lines.append(f"- `{item['start_ms'] / 1000:.3f}s`：{text}")
    if not ocr:
        lines.append("- 未返回 OCR 结果。")

    lines.extend(["", "## 时间线原始条目", ""])
    if timeline:
        lines.extend(_timeline_line(item) for item in timeline[:200])
        if len(timeline) > 200:
            lines.append(f"- 其余 `{len(timeline) - 200}` 条已保存在 `视觉证据.json`。")
    else:
        lines.append("- 未返回时间线条目。")

    lines.extend(["", "## 模型解释", ""])
    if summary:
        lines.append("以下内容属于模型生成的解释，不属于原始证据：")
        lines.append("")
        lines.append(str(summary).strip())
    else:
        lines.append("本次没有返回模型摘要；报告只保留可追溯的帧、OCR和原始时间线。")

    lines.extend(["", "## 警告与不确定性", ""])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- 分析引擎没有返回警告。仍需人工抽查中文 OCR 和时间戳。")
    return "\n".join(lines) + "\n"


def write_artifacts(
    *,
    raw: dict[str, Any],
    source: str,
    workdir: str | Path,
    command: list[str],
    elapsed_seconds: float,
) -> dict[str, Any]:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    frames_dir = workdir / "关键帧"
    frames_dir.mkdir(parents=True, exist_ok=True)
    raw = _merge_existing_transcript(raw, workdir)
    evidence = normalize_analysis(raw, source=source, workdir=workdir)
    (workdir / "视觉证据.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "engine": ENGINE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "command": command,
        "counts": {
            "speech": sum(item["type"] == "speech" for item in evidence["evidence"]),
            "visual": sum(item["type"] == "visual" for item in evidence["evidence"]),
            "ocr": sum(item["type"] == "ocr" for item in evidence["evidence"]),
        },
        "warnings": evidence["warnings"],
    }
    (workdir / "运行清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (workdir / "视频理解报告.md").write_text(
        _make_report(evidence, raw, elapsed_seconds), encoding="utf-8"
    )
    return evidence


def run_visual_analysis(
    *,
    source: str,
    workdir: str | Path,
    detail: str = "standard",
    max_frames: int | None = None,
    max_width: int | None = 1280,
    ocr_language: str = "chi_sim+eng",
    model: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    output_dir = Path(workdir) / "关键帧"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_cli_command(
        source=source,
        output_dir=output_dir,
        detail=detail,
        max_frames=max_frames,
        max_width=max_width,
        ocr_language=ocr_language,
        model=model,
        language=language,
    )
    print("=== 启动视觉证据分析 ===", flush=True)
    print(f"=== 输出目录: {output_dir} ===", flush=True)
    print("=== 视觉阶段进度由 mcp-video-analyzer 输出到下方日志 ===", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=None,
            env=build_runtime_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 npx，请先确认 Node.js 18+ 已安装。") from exc
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        detail_text = (completed.stdout or "").strip()[-2000:]
        raise RuntimeError(f"视觉分析失败，退出码 {completed.returncode}。{detail_text}")
    try:
        raw = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("视觉分析返回的不是合法 JSON，原始输出已丢弃以避免生成伪证据。") from exc
    return write_artifacts(
        raw=raw,
        source=source,
        workdir=workdir,
        command=command,
        elapsed_seconds=elapsed,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="把视频的关键帧、OCR和时间线整理成本地证据包")
    parser.add_argument("source", help="本地视频绝对路径或视频链接")
    parser.add_argument("workdir", help="输出目录")
    parser.add_argument("--detail", choices=("standard", "detailed"), default="standard")
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--max-width", type=int, default=1280)
    parser.add_argument("--ocr-language", default="chi_sim+eng")
    parser.add_argument("--model", default=None)
    parser.add_argument("--language", default=None)
    args = parser.parse_args(argv)
    try:
        run_visual_analysis(
            source=args.source,
            workdir=args.workdir,
            detail=args.detail,
            max_frames=args.max_frames,
            max_width=args.max_width,
            ocr_language=args.ocr_language,
            model=args.model,
            language=args.language,
        )
    except RuntimeError as exc:
        print(f"[失败] {exc}", file=sys.stderr, flush=True)
        return 2
    print("[成功] 视觉证据包已生成。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
