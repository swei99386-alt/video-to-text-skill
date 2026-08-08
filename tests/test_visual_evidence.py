import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from visual_evidence import (  # noqa: E402
    build_cli_command,
    build_runtime_env,
    configure_console,
    normalize_analysis,
    write_artifacts,
)
from grab import parse_cli_args  # noqa: E402


class VisualEvidenceTests(unittest.TestCase):
    def test_visual_cli_configures_utf8_console_without_error(self):
        self.assertTrue(configure_console())

    def test_runtime_env_exposes_portable_python_scripts_for_ytdlp(self):
        env = build_runtime_env()
        scripts_dir = str(Path(sys.executable).resolve().parent / "Scripts")
        self.assertIn(scripts_dir.lower(), env["PATH"].lower())

    def test_grab_cli_keeps_legacy_language_position_and_accepts_visual_flag(self):
        target, folder, language, visual = parse_cli_args(
            ["grab.py", "C:/videos/demo.mp4", "demo-output", "zh", "--visual"]
        )
        self.assertEqual((target, folder, language, visual), ("C:/videos/demo.mp4", "demo-output", "zh", True))

        target, folder, language, visual = parse_cli_args(
            ["grab.py", "C:/videos/demo.mp4", "demo-output", "--visual"]
        )
        self.assertEqual((target, folder, language, visual), ("C:/videos/demo.mp4", "demo-output", None, True))

    def test_grab_script_can_import_visual_sibling_when_launched_by_absolute_path(self):
        grab_path = SKILL_ROOT / "scripts" / "grab.py"
        script_dir = str(SKILL_ROOT / "scripts")
        code = (
            "import runpy, sys; "
            f"sys.path = [p for p in sys.path if p != {script_dir!r}]; "
            f"runpy.run_path({str(grab_path)!r}, run_name='not_main'); "
            "import visual_evidence; print('sibling_import_ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(SKILL_ROOT.parent.parent.parent),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sibling_import_ok", result.stdout)

    def test_build_cli_command_uses_the_pinned_cli_and_explicit_output(self):
        command = build_cli_command(
            source="C:/videos/demo.mp4",
            output_dir="C:/out/关键帧",
            detail="standard",
            max_frames=12,
            max_width=0,
            ocr_language="eng+chi_sim",
        )

        executable = "npx.cmd" if os.name == "nt" else "npx"
        self.assertEqual(command[:5], [executable, "-y", "mcp-video-analyzer@0.8.0", "analyze", "C:/videos/demo.mp4"])
        self.assertIn("--detail", command)
        self.assertIn("standard", command)
        self.assertIn("--max-frames", command)
        self.assertIn("12", command)
        self.assertIn("--max-width", command)
        self.assertIn("0", command)
        self.assertIn("--ocr-language", command)
        self.assertIn("eng+chi_sim", command)
        self.assertIn("--out", command)
        self.assertIn("C:/out/关键帧", command)

    def test_normalize_analysis_creates_traceable_speech_ocr_and_visual_records(self):
        raw = {
            "metadata": {"duration": 12.5, "title": "demo"},
            "transcript": [{"start": 1.0, "end": 2.5, "text": "这是语音"}],
            "frames": [{"time": 3.0, "filePath": "C:/out/关键帧/scene_003.jpg", "mimeType": "image/jpeg"}],
            "ocrResults": [{"time": 3.0, "text": "标题", "confidence": 0.91}],
            "timeline": [{"time": 3.0, "type": "frame", "text": "画面出现标题"}],
            "warnings": ["示例警告"],
        }

        result = normalize_analysis(raw, source="C:/videos/demo.mp4", engine="mcp-video-analyzer@0.8.0")

        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["source"], "C:/videos/demo.mp4")
        self.assertEqual(result["engine"], "mcp-video-analyzer@0.8.0")
        self.assertEqual([item["type"] for item in result["evidence"]], ["speech", "visual", "ocr"])
        self.assertEqual(result["evidence"][0]["start_ms"], 1000)
        self.assertEqual(result["evidence"][0]["end_ms"], 2500)
        self.assertEqual(result["evidence"][1]["frame_path"], "关键帧/scene_003.jpg")
        self.assertFalse(result["evidence"][1]["is_model_interpretation"])
        self.assertFalse(result["evidence"][2]["is_model_interpretation"])
        self.assertEqual(result["evidence"][2]["frame_path"], "关键帧/scene_003.jpg")
        self.assertEqual(result["warnings"], ["示例警告"])

    def test_write_artifacts_writes_json_manifest_and_report_without_overwriting_transcript(self):
        raw = {
            "metadata": {"duration": 12.5, "title": "demo"},
            "transcript": [{"start": 1.0, "end": 2.5, "text": "这是语音"}],
            "frames": [{"time": 3.0, "filePath": "C:/out/关键帧/scene_003.jpg"}],
            "ocrResults": [{"time": 3.0, "text": "标题"}],
            "timeline": [],
            "warnings": [],
        }

        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            transcript = out / "完整文字稿.txt"
            transcript.write_text("原有文字稿", encoding="utf-8")

            write_artifacts(
                raw=raw,
                source="C:/videos/demo.mp4",
                workdir=out,
                command=["npx", "..."],
                elapsed_seconds=1.25,
            )

            evidence = json.loads((out / "视觉证据.json").read_text(encoding="utf-8"))
            manifest = json.loads((out / "运行清单.json").read_text(encoding="utf-8"))
            report = (out / "视频理解报告.md").read_text(encoding="utf-8")

            self.assertEqual(evidence["source"], "C:/videos/demo.mp4")
            self.assertEqual(manifest["elapsed_seconds"], 1.25)
            self.assertEqual(transcript.read_text(encoding="utf-8"), "原有文字稿")
            self.assertIn("原始视觉证据", report)
            self.assertIn("OCR", report)
            self.assertIn("模型解释", report)

    def test_write_artifacts_merges_existing_timestamped_transcript_into_evidence(self):
        raw = {
            "metadata": {"duration": 12.5},
            "transcript": [],
            "frames": [],
            "ocrResults": [],
            "timeline": [],
            "warnings": [],
        }

        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / "transcript.srt").write_text(
                "1\n00:00:01,000 --> 00:00:02,500\n现有转写内容\n",
                encoding="utf-8",
            )

            write_artifacts(
                raw=raw,
                source="C:/videos/demo.mp4",
                workdir=out,
                command=["npx", "..."],
                elapsed_seconds=0.5,
            )

            evidence = json.loads((out / "视觉证据.json").read_text(encoding="utf-8"))
            speech = [item for item in evidence["evidence"] if item["type"] == "speech"]
            self.assertEqual(len(speech), 1)
            self.assertEqual(speech[0]["text"], "现有转写内容")
            self.assertEqual(speech[0]["start_ms"], 1000)
            self.assertEqual(speech[0]["end_ms"], 2500)


if __name__ == "__main__":
    unittest.main()
