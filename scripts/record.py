#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
录"电脑正在播放的声音"（内录/系统声音回环），用于抓不到链接的视频（微信视频号、
付费/加密页面、任何只能在屏幕上播的视频）。

用法:  python record.py <输出文件夹名>
  - 一跑就开始录，录到你把这个进程停掉为止（停止=转文字的信号）。
  - 边录边写盘（raw pcm），所以哪怕两三小时、中途被强杀，也不会整段丢。
  - 停止后会自动把 pcm 转成 录音.wav；若被强杀没转成，按 SKILL.md 里的一行命令手动转。

配套:  录完用 grab.py 把 录音.wav 转文字(中文加 zh):
  python grab.py "<桌面路径>/<文件夹>/录音.wav" "<文件夹>" zh
"""
import sys, os, time
import numpy as np
import soundcard as sc
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DESKTOP = r"C:\Users\Administrator\Desktop"
SR = 16000          # 16k 采样，够识别用，文件小
CHUNK = SR // 2     # 每 0.5 秒写一次盘


def main():
    if len(sys.argv) < 2:
        print("用法: python record.py <输出文件夹名>")
        sys.exit(1)
    folder = sys.argv[1]
    workdir = os.path.join(DESKTOP, folder)
    os.makedirs(workdir, exist_ok=True)
    pcm_path = os.path.join(workdir, "录音.pcm")
    wav_path = os.path.join(workdir, "录音.wav")

    spk = sc.default_speaker()
    mic = sc.get_microphone(spk.name, include_loopback=True)
    print(f"=== 开始录“电脑正在播放的声音” ===", flush=True)
    print(f"设备: {mic.name}", flush=True)
    print(f"边录边存到: {pcm_path}", flush=True)
    print(f"把视频从头播到尾；播完就停止本进程，之后自动转 wav。", flush=True)

    start = time.time()
    f = open(pcm_path, "wb")
    try:
        with mic.recorder(samplerate=SR, channels=1) as rec:
            last_report = 0
            while True:
                data = rec.record(numframes=CHUNK)          # float32 [-1,1]
                pcm = (np.clip(data[:, 0], -1, 1) * 32767).astype(np.int16)
                f.write(pcm.tobytes())
                f.flush()
                elapsed = int(time.time() - start)
                if elapsed - last_report >= 30:              # 每30秒报一次
                    last_report = elapsed
                    print(f"…已录 {elapsed//60} 分 {elapsed%60} 秒", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        f.close()
        _pcm_to_wav(pcm_path, wav_path)
        total = int(time.time() - start)
        print(f"=== 录制结束，共 {total//60} 分 {total%60} 秒 ===", flush=True)
        print(f"录音文件: {wav_path}", flush=True)
        print(f"下一步转文字: grab.py \"{wav_path}\" \"{folder}\" zh", flush=True)


def _pcm_to_wav(pcm_path, wav_path):
    """把 raw pcm(s16le/16k/单声道) 包成标准 wav。"""
    import subprocess
    if not os.path.exists(pcm_path) or os.path.getsize(pcm_path) == 0:
        return
    subprocess.run(["ffmpeg", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1",
                    "-i", pcm_path, wav_path],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
