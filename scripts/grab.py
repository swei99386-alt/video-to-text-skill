#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
视频/音频 → 文字（傻瓜一键版）
用法:  python grab.py <链接或本地文件> [输出文件夹名]

核心逻辑（写死，别绕）:
  第1步 先扒现成字幕（yt-dlp 直接下 caption）——秒级、免费、不下整段视频、不占显卡。
  第2步 只有在"一条字幕都扒不到"时，才走听译：抠出声音 → 用显卡跑 whisper。
  产物统一放到桌面的一个文件夹里，含: 完整文字稿.txt（干净纯文字）+ 原始字幕文件。

设计目标: 调用方（哪怕是很笨的第三方模型）只要会跑这一条命令就行，不需要自己判断走哪条路。
"""
import sys, os, subprocess, re, glob, shutil

# Skill 会被 Codex 从任意工作目录用绝对路径启动；确保同目录的视觉旁路
# 能被稳定找到，不依赖调用方是否设置 PYTHONPATH。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Windows 中文命令行默认 GBK, 强制 UTF-8 输出避免打印中文/符号时崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PY      = r"C:\Users\Administrator\portable-python311\runtime\python.exe"
YTDLP   = r"C:\Users\Administrator\portable-python311\runtime\Scripts\yt-dlp.exe"
DESKTOP = r"C:\Users\Administrator\Desktop"

# 想优先要的字幕语言（正则, 顺序即优先级）
# 涵盖主流语种, 避免脚本对西/法/德/葡/日/韩等外语"装看不见"→白白下整段视频去听译
# 用户传第3个语言参数时, 会在 try_subtitles 里优先挑匹配的那份字幕
SUB_LANGS = ",".join([
    # 英文原轨优先（看英文视频拿人工字幕, 比机翻强）
    "en-orig", "en.*", "en",
    # 中文（简体→繁体→中文兜底）
    "zh-Hans.*", "zh.*", "zh",
    # 西方主流: 西/法/德/葡
    "es-orig", "es.*", "es",
    "fr-orig", "fr.*", "fr",
    "de-orig", "de.*", "de",
    "pt-orig", "pt.*", "pt",
    # 东亚外语: 日/韩
    "ja.*", "ja",
    "ko.*", "ko",
])

VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv", ".flv",
    ".mpeg", ".mpg", ".m2ts", ".mts", ".3gp", ".ogv", ".ts",
}


def parse_cli_args(argv):
    """解析旧参数并增加可选 --visual，不改变原来的位置参数含义。"""
    args = list(argv[1:])
    visual = "--visual" in args
    args = [arg for arg in args if arg != "--visual"]
    if not args:
        raise ValueError("缺少视频/音频链接或本地文件")
    target = args[0]
    folder = args[1] if len(args) > 1 else "视频转文字_输出"
    language = args[2] if len(args) > 2 else None
    return target, folder, language, visual


def run_visual_if_requested(source, workdir, visual):
    """只在明确传 --visual 时运行视觉旁路；原有默认路径完全不触发。"""
    if not visual:
        return
    is_url = str(source).startswith("http")
    if not is_url and os.path.splitext(str(source))[1].lower() not in VIDEO_EXTS:
        print("[跳过] --visual 需要视频文件或视频链接；当前输入看起来是纯音频。", flush=True)
        return
    try:
        from visual_evidence import run_visual_analysis
        run_visual_analysis(source=str(source), workdir=workdir)
    except RuntimeError as exc:
        print(f"[失败] 视觉证据分析失败: {exc}", file=sys.stderr, flush=True)
        sys.exit(5)


def sh(cmd):
    print("··· " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd)


def clean_srt_to_text(srt_path, out_path):
    """把 srt/vtt 字幕清成一整段干净纯文字: 去时间码、去标签、去自动字幕的滚动重复行。"""
    raw = open(srt_path, encoding="utf-8", errors="ignore").read().splitlines()
    out = []
    for ln in raw:
        ln = ln.strip()
        if not ln or ln.isdigit() or "-->" in ln or ln.upper().startswith("WEBVTT"):
            continue
        ln = re.sub(r"<[^>]+>", "", ln)          # 去掉 <c> 之类标签
        ln = re.sub(r"\[[^\]]*\]", "", ln).strip()  # 去掉 [音乐] 之类
        if not ln:
            continue
        if out and out[-1] == ln:                 # 相邻重复行只留一条
            continue
        out.append(ln)
    text = re.sub(r"\s+", " ", " ".join(out)).strip()
    open(out_path, "w", encoding="utf-8").write(text)
    return len(text.split())


def try_subtitles(url, workdir, lang=None):
    """第1步: 尝试直接扒现成字幕。成功返回清洗后的词数, 失败返回 None。
    lang: 可选. 用户传了语言参数时, 优先挑匹配那份字幕(避免拿到 en 机翻版)。"""
    sh([YTDLP, "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", SUB_LANGS, "--sub-format", "srt/vtt/best",
        "--convert-subs", "srt", "-o", os.path.join(workdir, "%(title)s.%(ext)s"), url])
    subs = sorted(glob.glob(os.path.join(workdir, "*.srt")) +
                  glob.glob(os.path.join(workdir, "*.vtt")))
    if not subs:
        return None
    # 优先挑: 用户指定的语言 > 英文原轨/英文 > alphabetic 第一个
    pick = subs[0]
    if lang:
        for s in subs:
            low = os.path.basename(s).lower()
            if f".{lang}." in low:
                pick = s
                break
    else:
        for s in subs:
            low = os.path.basename(s).lower()
            if ".en-orig." in low or ".en." in low:
                pick = s
                break
    # 给视觉旁路一个稳定入口，避免它在多语言字幕文件中重新猜选哪一份。
    transcript_sidecar = os.path.join(workdir, "transcript.srt")
    if os.path.abspath(pick) != os.path.abspath(transcript_sidecar):
        shutil.copyfile(pick, transcript_sidecar)
    words = clean_srt_to_text(pick, os.path.join(workdir, "完整文字稿.txt"))
    return words


def transcribe_with_gpu(src_media, workdir, lang=None):
    """第2步: 没字幕才走这条。抠声音 → 显卡跑 faster-whisper → 清成纯文字。
    用 faster-whisper 替代 openai-whisper: 速度 4-5 倍, 显存省一半以上, 精度几乎一样。
    lang: 可选, 传 'zh' 强制中文并输出简体; 传 'en' 强制英文; 不传则自动判断。"""
    wav = os.path.join(workdir, "audio.wav")
    if src_media.lower().endswith(".pcm"):
        # 录屏内录产出的裸 pcm(s16le/16k/单声道), 需指定格式再转
        sh(["ffmpeg", "-y", "-f", "s16le", "-ar", "16000", "-ac", "1",
            "-i", src_media, wav])
    else:
        sh(["ffmpeg", "-y", "-i", src_media, "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", wav])
    # 检测显卡是否可用。两步验证：先看 cuda 是否报告可用, 再实际在 GPU 上跑个空 tensor 真跑一遍
    # （光看 is_available() 不够, 驱动刚升完级、torch 版本不匹配等情况下它可能返 True 但其实跑不动）
    gpu = False
    gpu_diag = ""
    try:
        import torch
        if torch.cuda.is_available():
            try:
                _ = torch.zeros(1, device="cuda")  # 真在 GPU 上分配一个空 tensor, 出错就视为不可用
                gpu = True
            except Exception as e:
                gpu_diag = f"torch.cuda.is_available() 返 True, 但在 GPU 上分配 tensor 失败: {type(e).__name__}: {e}"
        else:
            gpu_diag = "torch.cuda.is_available() 返 False（多半是驱动/torch 版本不匹配）"
    except Exception as e:
        gpu_diag = f"import torch 失败: {type(e).__name__}: {e}"
    device = "cuda" if gpu else "cpu"
    print(f"=== 听译设备: {'显卡(GPU)' if gpu else '处理器(CPU)'} ===", flush=True)
    if not gpu:
        warn_lines = [
            "!!! 显卡不可用, 退回 CPU 听译（很慢, 1 小时音频约 1 小时）",
            f"!!! 诊断: {gpu_diag}",
            "!!! 自助修法（按显卡地基卡片里的命令）:",
            "!!!   C:/Users/Administrator/portable-python311/runtime/python.exe -m pip install --force-reinstall torch==2.11.0 --index-url https://download.pytorch.org/whl/cu126",
            "!!! 装完重跑即可, 不必动 skill。",
        ]
        for line in warn_lines:
            print(line, flush=True)
    # 用 faster-whisper 替代 openai-whisper: 速度 4-5 倍, 显存省一半以上, 精度几乎一样
    # 5G 显卡用 int8 比 float16 更稳(老架构 GPU fp16 容易出问题;int8 显存更省)
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        print(f"[失败] faster-whisper 没装,装一下: {PY} -m pip install faster-whisper", flush=True)
        print(f"     详细错误: {type(e).__name__}: {e}", flush=True)
        sys.exit(4)
    compute_type = "int8"
    print(f"=== 加载 faster-whisper 模型: small, compute_type={compute_type} ===", flush=True)
    model = WhisperModel("small", device=device, compute_type=compute_type)
    transcribe_kwargs = {}
    if lang:
        transcribe_kwargs["language"] = lang
        if lang == "zh":  # 逼模型输出简体而非繁体
            transcribe_kwargs["initial_prompt"] = "以下是简体中文普通话的视频内容。"

    # 检测 wav 时长, 决定是否需要切段。>4 小时 35 分自动切(实测不切段能跑 4:35:01, 5G 显卡不爆)
    SEGMENT_SECONDS = 16501  # 4 小时 35 分 1 秒 = 16501 秒(2026-08-03 实测: 4:35:01 wav 一次性塞进去跑通)
    import wave as _wave
    with _wave.open(wav, 'rb') as _w:
        _dur = _w.getnframes() / _w.getframerate()
    if _dur > SEGMENT_SECONDS:
        print(f"=== 视频时长 {_dur/60:.1f} 分钟 > 10 分钟, 启用切段 ===", flush=True)
        segs_dir = os.path.join(workdir, "segs")
        os.makedirs(segs_dir, exist_ok=True)
        n_segs = int((_dur + SEGMENT_SECONDS - 1) // SEGMENT_SECONDS)
        segments_info = []
        for i in range(n_segs):
            offset = i * SEGMENT_SECONDS
            seg_dur = min(SEGMENT_SECONDS, _dur - offset)
            seg_path = os.path.join(segs_dir, f"seg_{i:03d}.wav")
            # -ss 在 -i 之前=快速定位;re-encode 保证段边界干净(不用 -c copy 避免半采样切坏)
            sh(["ffmpeg", "-y", "-ss", f"{offset:.3f}", "-i", wav,
                "-t", f"{seg_dur:.3f}", "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le", seg_path])
            segments_info.append((seg_path, offset))
        print(f"=== 切成 {len(segments_info)} 段, 逐段听译 (模型只加载一次) ===", flush=True)
    else:
        segments_info = [(wav, 0.0)]
        print(f"=== 视频时长 {_dur/60:.1f} 分钟, 不切段 ===", flush=True)

    all_segments = []  # 跨段合并: (绝对起始秒, 绝对结束秒, text)
    show_progress = len(segments_info) > 1  # 多段时打进度, 单段不刷屏
    for i, (seg_path, offset) in enumerate(segments_info):
        if len(segments_info) > 1:
            print(f"\n--- 第 {i+1}/{len(segments_info)} 段 (原视频 {offset/60:.1f} 分起) ---", flush=True)
        seg_iter, info = model.transcribe(seg_path, **transcribe_kwargs)
        if i == 0:
            print(f"=== 检测到语言: {info.language} (概率 {info.language_probability:.2f}) ===", flush=True)
        for seg in seg_iter:
            # seg.start/end 是段内时间, 加 offset 转成绝对时间, 拼起来时间轴才对得上
            all_segments.append((seg.start + offset, seg.end + offset, seg.text))
            if show_progress:
                print(f"  [{seg.start+offset:6.1f}s - {seg.end+offset:6.1f}s] {seg.text.strip()}", flush=True)

    # 拼成 srt, 让 clean_srt_to_text 继续复用(下游逻辑不动)
    def _fmt_ts(s):
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        ms = int(round((s - int(s)) * 1000))
        if ms >= 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
    srt_path = os.path.join(workdir, "audio.srt")
    srt_lines = []
    for i, (start, end, text) in enumerate(all_segments, 1):
        srt_lines.append(f"{i}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{text.strip()}\n")
    open(srt_path, "w", encoding="utf-8").write("\n".join(srt_lines))
    return clean_srt_to_text(srt_path, os.path.join(workdir, "完整文字稿.txt"))


def main():
    if len(sys.argv) < 2:
        print("用法: python grab.py <链接或本地文件> [输出文件夹名] [语言] [--visual]")
        sys.exit(1)
    try:
        target, folder, lang, visual = parse_cli_args(sys.argv)
    except ValueError as exc:
        print(f"用法错误: {exc}", file=sys.stderr)
        sys.exit(1)
    workdir = os.path.join(DESKTOP, folder)
    os.makedirs(workdir, exist_ok=True)

    is_url = target.startswith("http")
    words = None
    if is_url:
        print("### 第1步: 尝试直接扒现成字幕（最快、免费）###", flush=True)
        words = try_subtitles(target, workdir, lang)
        if words:
            run_visual_if_requested(target, workdir, visual)
            print(f"\n[成功] 走字幕路成功! 完整文字稿.txt 已生成, 约 {words} 个词。", flush=True)
            print(f"位置: {workdir}", flush=True)
            return

    print("\n### 没有现成字幕（或是本地文件）, 转第2步: 用显卡听译 ###", flush=True)
    src = target if not is_url else None
    if src is None:
        # 需要先把媒体下下来才能听译
        print("### 下载媒体以便听译 ###", flush=True)
        sh([YTDLP, "-f", "bestaudio/best", "-o",
            os.path.join(workdir, "media.%(ext)s"), target])
        got = sorted(glob.glob(os.path.join(workdir, "media.*")))
        if not got:
            print("[失败] 媒体下载失败，无法听译。", flush=True)
            sys.exit(2)
        src = got[0]
    words = transcribe_with_gpu(src, workdir, lang)
    if words:
        run_visual_if_requested(target if is_url else src, workdir, visual)
        print(f"\n[成功] 听译完成! 完整文字稿.txt 约 {words} 个词。", flush=True)
        print(f"位置: {workdir}", flush=True)
    else:
        print("[失败] 听译失败。", flush=True)
        sys.exit(3)


if __name__ == "__main__":
    main()
