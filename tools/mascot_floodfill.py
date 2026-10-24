# -*- coding: utf-8 -*-
"""Mascot background removal v2: border-connected flood fill.

For every frame: mark 'dark' pixels (all RGB channels < threshold), flood-fill
from dark pixels on the frame border, and cut only that connected background
away. Dark zones INSIDE the character (eyes, mouth) survive because they are
not reachable from the border. Edges are feathered slightly.
"""
import subprocess
import sys
from collections import deque

import numpy as np

FF = (r"C:/Users/Дмитрий/AppData/Local/Microsoft/WinGet/Packages/"
      r"Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
      r"ffmpeg-9.0.2-full_build/bin/ffmpeg.exe")
SRC = u"C:/Users/Дмитрий/Downloads/1023({n}).gif.mp4"
OUT_WEBM = (r"C:/Users/Дмитрий/Documents/GitHub/piglets_3d5d/plugin/"
            r"AIAssistant/web/assets/mascot_{name}_alpha.webm")
OUT_GIF = (r"C:/Users/Дмитрий/Documents/GitHub/piglets_3d5d/plugin/"
           r"AIAssistant/resources/mascot/mascot_{name}_light.gif")

W, H = 240, 320
DARK = 72          # pixel is 'dark' if all channels below this
FEATHER_PASSES = 1  # fewer blur passes: keep thin dark features crisp


def read_frames(video):
    cmd = [FF, "-v", "error", "-i", video, "-f", "rawvideo",
           "-pix_fmt", "rgba", "-"]
    raw = subprocess.check_output(cmd)
    n = len(raw) // (W * H * 4)
    arr = np.frombuffer(raw[:n * W * H * 4], dtype=np.uint8).reshape(n, H, W, 4)
    return arr.copy()


def flood_background(rgb):
    """Mask (True = background) of dark pixels connected to the frame border."""
    dark = (rgb[:, :, 0] < DARK) & (rgb[:, :, 1] < DARK) & (rgb[:, :, 2] < DARK)
    bg = np.zeros((H, W), dtype=bool)
    dq = deque()
    for x in range(W):
        for y in (0, H - 1):
            if dark[y, x] and not bg[y, x]:
                bg[y, x] = True
                dq.append((y, x))
    for y in range(H):
        for x in (0, W - 1):
            if dark[y, x] and not bg[y, x]:
                bg[y, x] = True
                dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        if y > 0 and dark[y - 1, x] and not bg[y - 1, x]:
            bg[y - 1, x] = True; dq.append((y - 1, x))
        if y < H - 1 and dark[y + 1, x] and not bg[y + 1, x]:
            bg[y + 1, x] = True; dq.append((y + 1, x))
        if x > 0 and dark[y, x - 1] and not bg[y, x - 1]:
            bg[y, x - 1] = True; dq.append((y, x - 1))
        if x < W - 1 and dark[y, x + 1] and not bg[y, x + 1]:
            bg[y, x + 1] = True; dq.append((y, x + 1))
    return bg


def feather_alpha(alpha):
    """Light blur only (no erosion): keeps thin dark fingers/toes intact."""
    a = alpha.astype(np.float32)
    e = a
    for _ in range(FEATHER_PASSES):
        b = e.copy()
        b[1:-1, 1:-1] = (e[1:-1, 1:-1] + e[:-2, 1:-1] + e[2:, 1:-1] +
                         e[1:-1, :-2] + e[1:-1, 2:])
        b[1:-1, 1:-1] /= 5.0
        e = b
    return np.clip(e, 0.0, 1.0)


def defringe(rgb, alpha):
    """Unmix black background contamination from semi-transparent edge pixels.

    observed = a*color + (1-a)*black  =>  color = observed / a
    """
    a = np.maximum(alpha, 0.25)[..., None]
    out = rgb.astype(np.float32) / a
    # apply only where alpha is partial or pixel is suspiciously dark
    partial = (alpha > 0.02) & (alpha < 0.98)
    dark_edge = rgb.max(axis=2) < 90
    apply_mask = (partial | dark_edge) & (alpha > 0.02)
    rgb_out = rgb.astype(np.float32)
    rgb_out[apply_mask] = out[apply_mask]
    return np.clip(rgb_out, 0, 255).astype(np.uint8)


def process(name, n):
    frames = read_frames(SRC.format(n=n))
    print(u"%s: %d frames" % (name, len(frames)))
    out = np.empty_like(frames)
    for i, fr in enumerate(frames):
        rgb = fr[:, :, :3]
        bg = flood_background(rgb)
        alpha = np.where(bg, 0.0, 1.0).astype(np.float32)
        alpha = feather_alpha(alpha)
        rgb = defringe(rgb, alpha)
        out[i, :, :, :3] = rgb
        out[i, :, :, 3] = (alpha * 255).astype(np.uint8)
    rgba = out.tobytes()

    # webm with alpha
    cmd = [FF, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % (W, H),
           "-r", "10", "-i", "-",
           "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
           "-auto-alt-ref", "0", "-an", OUT_WEBM.format(name=name)]
    subprocess.run(cmd, input=rgba, check=True)
    print(u"  webm ok")

    # light-bg gif (#F7F6F2)
    cmd = [FF, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % (W, H),
           "-r", "10", "-i", "-",
           "-filter_complex",
           "[0:v]format=rgba[fg];color=c=0xF7F6F2:s=%dx%d:r=10[bg];"
           "[bg][fg]overlay=shortest=1,split[s0][s1];"
           "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer"
           % (W, H),
           OUT_GIF.format(name=name)]
    subprocess.run(cmd, input=rgba, check=True)
    print(u"  gif ok")


def main():
    for name, n in ((u"idle", 6), (u"happy", 9), (u"think", 10)):
        process(name, n)
    print("ALL DONE")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
