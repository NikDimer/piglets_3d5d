# -*- coding: utf-8 -*-
"""Mascot background removal v4: exact bg color + flood + temporal propagation.

Background is one specific color (pure black + codec noise <= BG_EXACT).
For each frame the flood fill is seeded from:
  a) exact-black pixels on the frame border, AND
  b) pixels that were background in the PREVIOUS frame and are exact-black
     now — this erases background pockets that get enclosed when, e.g., the
     hand touches the face, and the flood then spreads to every connected
     black pixel around the seed.
Dark character parts are a different color and never match.
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
BG_EXACT = 12     # background color: pure black with compression noise
BG_DARK = 50      # 'dark' pass: reaches into trapped pockets (temporal seed only)
HALO_LO = 10      # halo band -> partial alpha, only in the 1px next to bg
HALO_HI = 50
FEATHER_PASSES = 1


def read_frames(video):
    cmd = [FF, "-v", "error", "-i", video, "-f", "rawvideo",
           "-pix_fmt", "rgba", "-"]
    raw = subprocess.check_output(cmd)
    n = len(raw) // (W * H * 4)
    arr = np.frombuffer(raw[:n * W * H * 4], dtype=np.uint8).reshape(n, H, W, 4)
    return arr.copy()


def _flood(mask, seeds=None):
    """Flood-fill the True region of mask starting from border True pixels
    plus optional extra seed pixels (previous-frame background propagation)."""
    bg = np.zeros((H, W), dtype=bool)
    dq = deque()
    for x in range(W):
        for y in (0, H - 1):
            if mask[y, x] and not bg[y, x]:
                bg[y, x] = True
                dq.append((y, x))
    for y in range(H):
        for x in (0, W - 1):
            if mask[y, x] and not bg[y, x]:
                bg[y, x] = True
                dq.append((y, x))
    if seeds is not None:
        ys, xs = np.nonzero(seeds & mask & ~bg)
        for y, x in zip(ys.tolist(), xs.tolist()):
            bg[y, x] = True
            dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        if y > 0 and mask[y - 1, x] and not bg[y - 1, x]:
            bg[y - 1, x] = True; dq.append((y - 1, x))
        if y < H - 1 and mask[y + 1, x] and not bg[y + 1, x]:
            bg[y + 1, x] = True; dq.append((y + 1, x))
        if x > 0 and mask[y, x - 1] and not bg[y, x - 1]:
            bg[y, x - 1] = True; dq.append((y, x - 1))
        if x < W - 1 and mask[y, x + 1] and not bg[y, x + 1]:
            bg[y, x + 1] = True; dq.append((y, x + 1))
    return bg


def _dilate(mask):
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def background_alpha(rgb, prev_bg=None):
    """Exact-color background removal with temporal propagation.

    Pass 1 (strict): flood over exact background color from the border and
    from pixels that were background in the previous frame.
    Pass 2 (dark): enclosed pockets keep codec-noise pixels slightly above
    the strict color, so we additionally flood over 'dark' (< BG_DARK)
    pixels seeded ONLY where the previous frame was background — this
    reaches into the trapped pocket without ever touching interior dark
    details (eyes/mouth were never background in any frame).
    Returns (alpha, bg_mask) — bg_mask feeds the next frame.
    """
    brightness = rgb.max(axis=2)
    exact_bg = brightness < BG_EXACT
    bg = _flood(exact_bg, seeds=prev_bg)

    if prev_bg is not None:
        dark = brightness < BG_DARK
        seeds2 = prev_bg & dark
        if seeds2.any():
            bg2 = _flood(dark, seeds=seeds2)
            bg = bg | bg2

    alpha = np.where(bg, 0.0, 1.0)

    halo_zone = _dilate(bg) & ~bg
    halo = halo_zone & (brightness < HALO_HI)
    # brightness ~10 -> fully background, ~50 -> fully character
    ramp = (brightness.astype(np.float32) - HALO_LO) / float(HALO_HI - HALO_LO)
    alpha[halo] = np.clip(ramp[halo], 0.0, 1.0)

    # diagnostics: leftover exact-black pockets not reached by the flood
    pockets = exact_bg & ~bg
    if pockets.any():
        sys.stderr.write("  warn: %d black px in closed pockets\n"
                         % int(pockets.sum()))
    return alpha, bg


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
    prev_bg = None
    for i, fr in enumerate(frames):
        rgb = fr[:, :, :3]
        alpha, bg = background_alpha(rgb, prev_bg=prev_bg)
        prev_bg = bg
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
