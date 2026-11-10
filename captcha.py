# -*- coding: utf-8 -*-
"""
数字动态图片验证码 —— 纯标准库实现（zlib + struct 手写 PNG）

为什么不用 Pillow：
    Pillow 只发 cp311+ 的 wheel，且体积数十 MB；通用离线包要覆盖 py3.8~3.12
    与三大平台，引入 Pillow 会让依赖矩阵和包体积都失控。这里用标准库直接
    拼 PNG 字节流，零依赖、跨平台、单张图 < 2KB。

对外只暴露：
    new_captcha(width=132, height=44) -> (code, png_bytes)
"""
import random
import struct
import zlib

# 5x7 点阵数字
_GLYPHS = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
}


def _chunk(typ, data):
    return (struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))


def _png(width, height, rows):
    """rows: 每行是 [(r,g,b), ...] 的列表。"""
    raw = bytearray()
    for row in rows:
        raw += b"\x00"                      # filter type 0 (None)
        for px in row:
            raw += bytes(px)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit RGB
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))


def _rand_color(rng, lo, hi):
    return (rng.randint(lo, hi), rng.randint(lo, hi), rng.randint(lo, hi))


def new_captcha(width=132, height=44, digits=4):
    """生成验证码。返回 (code, png_bytes)。

    code 是 4 位数字字符串；答案由调用方存进会话，用完即弃。
    """
    rng = random.Random()
    code = "".join(str(rng.randint(0, 9)) for _ in range(digits))
    scale = 4
    gw, gh = 5 * scale, 7 * scale        # 单字 20x28
    gap = (width - digits * gw) // (digits + 1)
    base_y = max(0, (height - gh) // 2)

    # 画布：淡色底
    bg = _rand_color(rng, 232, 248)
    canvas = [[bg for _ in range(width)] for _ in range(height)]

    # 干扰线：3~5 条随机折线
    for _ in range(rng.randint(3, 5)):
        c = _rand_color(rng, 120, 200)
        y0 = rng.randint(0, height - 1)
        y1 = rng.randint(0, height - 1)
        step = 1
        for x in range(width):
            t = x / float(width - 1)
            y = int(y0 + (y1 - y0) * t) + rng.randint(-1, 1)
            if 0 <= y < height:
                canvas[y][x] = c
            if step and rng.random() < 0.02:
                pass

    # 噪点
    for _ in range(rng.randint(60, 120)):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        canvas[y][x] = _rand_color(rng, 150, 220)

    # 逐个数字绘制（每个字独立 y 抖动 + 独立颜色）
    for i, ch in enumerate(code):
        glyph = _GLYPHS[ch]
        x_off = gap + i * (gw + gap)
        y_off = base_y + rng.randint(-4, 4)
        color = _rand_color(rng, 10, 90)       # 深色，保证可读
        for gy, bits in enumerate(glyph):
            for gx, bit in enumerate(bits):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px = x_off + gx * scale + dx
                        py = y_off + gy * scale + dy
                        if 0 <= px < width and 0 <= py < height:
                            canvas[py][px] = color

    return code, _png(width, height, canvas)


if __name__ == "__main__":
    code, png = new_captcha()
    print("code =", code, "| png bytes =", len(png))
