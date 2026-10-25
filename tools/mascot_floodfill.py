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
from PIL import Image

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
DENOISE_PASSES = 2
# slicer 3D-viewport background: theme.json viewport_background + hardcoded
# QColor(245,245,245) in SteSlicerApplication.py (#F5F5F5)
BG_FILL = (245.0, 245.0, 245.0)
DITHER = sys.argv[1] if len(sys.argv) > 1 else "none"


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


def box_blur(rgb):
    """Center-weighted 3x3 blur, DENOISE_PASSES times. The h264 sources are
    ~80 kbit/s and full of luma/chroma speckle; without this the 256-color
    GIF quantizer amplifies the noise into visible grain."""
    out = rgb.astype(np.float32)
    for _ in range(DENOISE_PASSES):
        p = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        out = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] +
               p[1:-1, 2:] + 4.0 * out) / 8.0
    return out


def _dilate(mask):
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def bg_mask(rgb, prev_bg=None):
    """Exact-color background mask with temporal propagation.

    Pass 1 (strict): flood over exact background color from the border and
    from pixels that were background in the previous frame.
    Pass 2 (dark): enclosed pockets keep codec-noise pixels slightly above
    the strict color, so we additionally flood over 'dark' (< BG_DARK)
    pixels seeded ONLY where the previous frame was background — this
    reaches into the trapped pocket without ever touching interior dark
    details (eyes/mouth were never background in any frame).
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
    return bg


def alpha_from_mask(rgb, bg):
    """Alpha channel for a frame given its background mask (halo ramp on
    the 1px band next to the background)."""
    brightness = rgb.max(axis=2)
    alpha = np.where(bg, 0.0, 1.0)

    halo_zone = _dilate(bg) & ~bg
    halo = halo_zone & (brightness < HALO_HI)
    # brightness ~10 -> fully background, ~50 -> fully character
    ramp = (brightness.astype(np.float32) - HALO_LO) / float(HALO_HI - HALO_LO)
    alpha[halo] = np.clip(ramp[halo], 0.0, 1.0)
    return alpha


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
    # loop as a palindrome: 0..N-1 then N-2..1. The source animations never
    # return to their start pose, so a plain loop teleports the character at
    # the wrap (a 4-10x frame-to-frame jump that reads as a glitch flash).
    # With the reversed tail every playback transition is between adjacent
    # source frames, so the loop is perfectly seamless.
    seq = list(range(len(frames))) + list(range(len(frames) - 2, 0, -1))
    frames = frames[seq]
    print(u"%s: %d frames (palindrome loop)" % (name, len(frames)))
    denoised = [box_blur(fr[:, :, :3]) for fr in frames]

    # The gif loops, so the temporal chain must be closed: frame 0 is seeded
    # from the LAST frame's mask, not from None. Without this the first
    # frames keep enclosed black pocket pixels which flash every time the
    # player wraps the animation. Iterate until the mask chain is stable.
    masks = [None] * len(denoised)
    prev_bg = None
    for _ in range(4):
        changed = False
        for i, rgb in enumerate(denoised):
            bg = bg_mask(rgb, prev_bg=prev_bg)
            if masks[i] is None or not np.array_equal(masks[i], bg):
                changed = True
            masks[i] = bg
            prev_bg = bg
        prev_bg = masks[-1]
        if not changed:
            break

    # Global union pass: a dark pixel that was background in ANY frame is
    # background in EVERY frame where it is dark and connected to the union.
    # The temporal chain alone cannot reach pockets that travel with the
    # hand and close near the loop seam; the union can. Character pixels
    # (eyes/mouth) were never background in any frame, so they are safe.
    union = np.zeros((H, W), dtype=bool)
    for m in masks:
        union |= m
    for i, rgb in enumerate(denoised):
        brightness = rgb.max(axis=2)
        erasable = (brightness < BG_EXACT) | (union & (brightness < BG_DARK))
        masks[i] = _flood(erasable, seeds=union & erasable)

    # leftovers: exact-black pixels no pass ever reached — these are the
    # character's own dark details (eyes/mouth); sizes must be similar in
    # every frame, otherwise some frame still carries bg leftovers
    leftovers = [int(((rgb.max(axis=2) < BG_EXACT) & ~masks[i]).sum())
                 for i, rgb in enumerate(denoised)]
    sys.stderr.write("  leftover dark px per frame: min=%d max=%d frame0=%d\n"
                     % (min(leftovers), max(leftovers), leftovers[0]))

    out = np.empty_like(frames)
    for i, fr in enumerate(frames):
        rgb = denoised[i]
        alpha = alpha_from_mask(rgb, masks[i])
        alpha = feather_alpha(alpha)
        rgb = defringe(rgb, alpha)
        out[i, :, :, :3] = rgb
        out[i, :, :, 3] = (alpha * 255).astype(np.uint8)

    # normalize the character scale across states: the sources draw the
    # chameleon at different zooms (think ~35% smaller than idle), so crop
    # every state to the character's own union bbox and refit it into the
    # frame — all states then show the same-size mascot
    solid = out[:, :, :, 3] > 24
    ys, xs = np.nonzero(solid.any(axis=0))
    pad = 6
    y0, y1 = max(0, int(ys.min()) - pad), min(H, int(ys.max()) + 1 + pad)
    x0, x1 = max(0, int(xs.min()) - pad), min(W, int(xs.max()) + 1 + pad)
    ch, cw = y1 - y0, x1 - x0
    scale = min(W / float(cw), H / float(ch))
    nw, nh = max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))
    ox, oy = (W - nw) // 2, (H - nh) // 2
    fitted = np.zeros((len(out), H, W, 4), dtype=np.uint8)
    for i in range(len(out)):
        img = Image.fromarray(out[i, y0:y1, x0:x1], mode="RGBA")
        fitted[i, oy:oy + nh, ox:ox + nw] = np.asarray(
            img.resize((nw, nh), Image.LANCZOS))
    out = fitted
    print("  refit: crop %dx%d -> %dx%d (frame fill %.0f%%)"
          % (cw, ch, nw, nh, 100.0 * (nw * nh) / (W * H)))
    rgba = out.tobytes()

    # webm with alpha
    cmd = [FF, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % (W, H),
           "-r", "10", "-i", "-",
           "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
           "-auto-alt-ref", "0", "-an", OUT_WEBM.format(name=name)]
    subprocess.run(cmd, input=rgba, check=True)
    print(u"  webm ok")

    # gif over the slicer background color: composited in numpy so the bg is
    # exactly BG_FILL; 256-color palette + no bayer dither keeps flat areas
    # flat (bayer's regular pattern reads as grain at these sizes)
    alpha = out[:, :, :, 3:4].astype(np.float32) / 255.0
    comp = (out[:, :, :, :3].astype(np.float32) * alpha +
            np.array(BG_FILL, dtype=np.float32) * (1.0 - alpha))
    gif_rgb = np.clip(comp, 0, 255).astype(np.uint8).tobytes()

    cmd = [FF, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
           "-r", "10", "-i", "-",
           "-filter_complex",
           "[0:v]split[s0][s1];[s0]palettegen=max_colors=256[p];"
           "[s1][p]paletteuse=dither=%s" % DITHER,
           OUT_GIF.format(name=name)]
    subprocess.run(cmd, input=gif_rgb, check=True)
    print(u"  gif ok")


def main():
    for name, n in ((u"idle", 6), (u"happy", 9), (u"think", 10)):
        process(name, n)
    print("ALL DONE")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
