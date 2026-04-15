"""
decode_partial_qr.py
====================
Decodes QR codes where the right edge (including the top-right finder pattern)
is partially cut off by a monitor bezel, frame edge, or crop boundary.

Works even when the QR/monitor is tilted relative to the camera.

Strategy:
  1. Direct decode — fast path for fully visible QRs.
  2. Axis-aligned reconstruction — find TL finder, draw missing TR finder
     at expected horizontal offset. Works for upright QRs.
  3. Rotated reconstruction — use TL + BL finders to compute the exact
     rotated QR geometry, draw the TR finder at the correct tilted position,
     then perspective-warp to flat before decoding. Works for tilted monitors.

Usage:
    python decode_partial_qr.py <image_path>

Or as a library:
    from decode_partial_qr import decode_partial_qr
    result = decode_partial_qr("frame.png")

Dependencies:
    pip install pyzbar opencv-contrib-python-headless
    apt install libzbar0   # Linux only
"""

import cv2
import numpy as np
from pyzbar import pyzbar
import sys

# The 7x7 finder pattern (1=black, 0=white)
FINDER_PATTERN = np.array([
    [1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, 0, 0, 0, 1],
    [1, 0, 1, 1, 1, 0, 1],
    [1, 0, 1, 1, 1, 0, 1],
    [1, 0, 1, 1, 1, 0, 1],
    [1, 0, 0, 0, 0, 0, 1],
    [1, 1, 1, 1, 1, 1, 1],
], dtype=np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
# FINDER PATTERN DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def find_square_finders(gray):
    """
    Find all large square contours (candidates for finder pattern outer rings).
    Returns list of (x, y, w, h) sorted by size descending.
    """
    _, binary = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.05 * peri, True)
        if len(approx) == 4:
            x, y, cw, ch = cv2.boundingRect(approx)
            ar = cw / ch if ch else 0
            if 0.7 < ar < 1.3 and 20 < cw < gray.shape[1] // 2:
                candidates.append((x, y, cw, ch))

    return sorted(candidates, key=lambda s: -(s[2] * s[3]))


def find_tl_bl_finders(gray):
    """
    Identify the TL (top-left) and BL (bottom-left) finder patterns.
    Returns (tl, bl) as (x, y, w, h) tuples, or (tl, None) if only one found.
    """
    squares = find_square_finders(gray)
    if not squares:
        return None, None

    tl = min(squares, key=lambda s: s[0] + s[1])
    tl_size = tl[2]

    bl_candidates = [
        s for s in squares
        if s != tl
        and abs(s[2] - tl_size) < tl_size * 0.3
        and abs(s[0] - tl[0]) < tl_size * 2
        and s[1] > tl[1] + tl_size * 3
    ]

    # Pick by largest area (avoids mistaking the inner ring for the outer 7x7 square)
    bl = max(bl_candidates, key=lambda s: s[2] * s[3]) if bl_candidates else None
    return tl, bl


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY A: AXIS-ALIGNED RECONSTRUCTION
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_axis_aligned(img, tl_finder, version):
    """
    Reconstruct the missing TR finder assuming the QR is upright (no tilt).
    """
    x0, y0, fw, fh = tl_finder
    module_size = fw / 7.0
    h, w = img.shape[:2]
    n_modules = (version - 1) * 4 + 21

    tr_x0 = int(x0 + (n_modules - 7) * module_size)
    tr_y0 = y0
    tr_right = tr_x0 + int(7 * module_size)
    pad_needed = max(0, tr_right - w) + int(module_size * 5)

    canvas = cv2.copyMakeBorder(img, 0, 0, 0, pad_needed,
                                 cv2.BORDER_CONSTANT, value=(255, 255, 255))

    for row in range(7):
        for col in range(7):
            px0 = tr_x0 + int(col * module_size)
            py0 = tr_y0 + int(row * module_size)
            px1 = tr_x0 + int((col + 1) * module_size)
            py1 = tr_y0 + int((row + 1) * module_size)
            if px1 > w - 2:
                color = (0, 0, 0) if FINDER_PATTERN[row, col] == 1 else (255, 255, 255)
                cv2.rectangle(canvas, (px0, py0), (px1, py1), color, -1)

    return canvas


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY B: ROTATED RECONSTRUCTION + PERSPECTIVE WARP
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_rotated(img, tl_finder, bl_finder, verbose=False):
    """
    Use TL + BL finder positions to compute the exact rotated QR geometry.
    Reconstructs the TR finder at the correct tilted position, then
    perspective-warps the QR flat before decoding.
    """
    x_tl, y_tl, fw_tl, fh_tl = tl_finder
    x_bl, y_bl, fw_bl, fh_bl = bl_finder
    h, w = img.shape[:2]

    # Use the outer top-left corner of TL finder and outer bottom-left of BL finder
    # These are the true corners of the QR module grid
    qr_tl = np.float32([x_tl, y_tl])
    qr_bl = np.float32([x_bl, y_bl + fh_bl])

    left_vec = qr_bl - qr_tl
    lx, ly = left_vec
    right_vec = np.float32([ly, -lx])
    right_unit = right_vec / np.linalg.norm(right_vec)
    down_unit = left_vec / np.linalg.norm(left_vec)
    module_size = fw_tl / 7.0

    for version in range(3, 8):
        n_modules = (version - 1) * 4 + 21
        expected_side = n_modules * module_size

        qr_tr = qr_tl + expected_side * right_unit
        qr_br = qr_bl + expected_side * right_unit

        if verbose:
            missing = qr_tr[0] - w
            print(f"  v{version}: TR at x={qr_tr[0]:.0f}, missing={missing:.0f}px")

        pad_r = max(0, int(qr_tr[0] - w) + int(module_size * 6))
        canvas = cv2.copyMakeBorder(img, 0, 0, 0, pad_r,
                                     cv2.BORDER_CONSTANT, value=(255, 255, 255))

        tr_origin = qr_tl + (n_modules - 7) * module_size * right_unit

        for row in range(7):
            for col in range(7):
                p00 = tr_origin + col * module_size * right_unit + row * module_size * down_unit
                p10 = p00 + module_size * right_unit
                p01 = p00 + module_size * down_unit
                p11 = p00 + module_size * right_unit + module_size * down_unit
                pts = np.array([p00, p10, p11, p01], dtype=np.int32)
                if pts[:, 0].max() > w - 2:
                    color = (0, 0, 0) if FINDER_PATTERN[row, col] == 1 else (255, 255, 255)
                    cv2.fillPoly(canvas, [pts], color)

        size = int(n_modules * module_size)
        src_pts = np.float32([qr_tl, qr_tr, qr_br, qr_bl])
        dst_pts = np.float32([[0, 0], [size, 0], [size, size], [0, size]])
        M = cv2.getPerspectiveTransform(src_pts, dst_pts)
        flat = cv2.warpPerspective(canvas, M, (size, size),
                                    flags=cv2.INTER_LANCZOS4,
                                    borderValue=(255, 255, 255))

        flat_gray = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)

        for binarized in [
            cv2.threshold(flat_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
            cv2.threshold(flat_gray, 120, 255, cv2.THRESH_BINARY)[1],
            cv2.adaptiveThreshold(flat_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 15, 4),
        ]:
            padded = cv2.copyMakeBorder(binarized, 20, 20, 20, 20,
                                         cv2.BORDER_CONSTANT, value=255)
            result = pyzbar.decode(padded)
            if result:
                return result[0].data.decode("utf-8")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DECODE FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def decode_partial_qr(image_input, verbose=False):
    """
    Decode a QR code that is partially cut off on the right side.

    Args:
        image_input: file path (str) or numpy BGR image array
        verbose:     print debug info

    Returns:
        str: decoded QR content, or None if decoding failed
    """
    if isinstance(image_input, str):
        img = cv2.imread(image_input)
    else:
        img = image_input.copy()

    if img is None:
        raise ValueError("Could not load image")

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Step 1: direct decode
    result = pyzbar.decode(gray)
    if result:
        if verbose:
            print("Decoded directly (no reconstruction needed)")
        return result[0].data.decode("utf-8")

    # Step 2: find finder patterns
    tl_finder, bl_finder = find_tl_bl_finders(gray)
    if tl_finder is None:
        if verbose:
            print("Could not locate any finder patterns")
        return None

    module_size = tl_finder[2] / 7.0
    if verbose:
        print(f"TL finder: {tl_finder}, module_size={module_size:.2f}px")
        print(f"BL finder: {bl_finder}")

    # Step 3A: axis-aligned reconstruction (upright QR)
    if verbose:
        print("\n[Strategy A] Axis-aligned reconstruction...")

    for version in range(2, 9):
        n_modules = (version - 1) * 4 + 21
        if tl_finder[0] + n_modules * module_size < w + module_size * 0.3:
            continue

        canvas = reconstruct_axis_aligned(img, tl_finder, version)
        canvas_gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)

        for proc in [
            canvas_gray,
            cv2.adaptiveThreshold(canvas_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 15, 4),
        ]:
            result = pyzbar.decode(proc)
            if result:
                if verbose:
                    print(f"Decoded with axis-aligned v{version} reconstruction")
                return result[0].data.decode("utf-8")

    # Step 3B: rotated reconstruction (tilted QR/monitor)
    if bl_finder is not None:
        if verbose:
            print("\n[Strategy B] Rotated reconstruction using TL+BL finders...")
        result = reconstruct_rotated(img, tl_finder, bl_finder, verbose=verbose)
        if result:
            if verbose:
                print("Decoded with rotated reconstruction + perspective warp")
            return result

    if verbose:
        print("All strategies failed")
    return None


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python decode_partial_qr.py <image_path>")
        sys.exit(1)

    result = decode_partial_qr(path, verbose=True)
    print(f"\n{'Decoded: ' + result if result else 'Failed to decode'}")
