# -*- coding: utf-8 -*-
"""Grain metric for mascot gifs: mean high-frequency energy on character
pixels (background excluded). Lower = smoother/cleaner."""
import subprocess
import sys

import numpy as np

FF = (r"C:/Users/Дмитрий/AppData/Local/Microsoft/WinGet/Packages/"
      r"Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
      r"ffmpeg-9.0.2-full_build/bin/ffmpeg.exe")
M = r"plugin/AIAssistant/resources/mascot"
BG = np.array([245.0, 245.0, 245.0])
W, H = 240, 320


def grain(gif):
    raw = subprocess.check_output(
        [FF, "-v", "error", "-i", gif, "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"])
    n = len(raw) // (W * H * 3)
    a = np.frombuffer(raw, dtype=np.uint8)[:n * W * H * 3].reshape(n, H, W, 3)
    vals = []
    for fr in a[::6]:
        f = fr.astype(np.float32)
        char_mask = np.abs(f - BG).max(axis=2) > 25   # not background
        core = np.zeros((H, W), dtype=bool)
        core[1:-1, 1:-1] = (char_mask[:-2, 1:-1] & char_mask[2:, 1:-1] &
                            char_mask[1:-1, :-2] & char_mask[1:-1, 2:] &
                            char_mask[1:-1, 1:-1])
        hp = (np.abs(4.0 * f[1:-1, 1:-1] -
                     (f[:-2, 1:-1] + f[2:, 1:-1] +
                      f[1:-1, :-2] + f[1:-1, 2:])).max(axis=2))
        vals.append(float(hp[core[1:-1, 1:-1]].mean()))
    return sum(vals) / len(vals), n


total = 0.0
for s in ("idle", "think", "happy"):
    g, n = grain(M + "/mascot_%s_light.gif" % s)
    total += g
    print("%-6s frames=%d grain=%.2f" % (s, n, g))
print("TOTAL %.2f" % total)
