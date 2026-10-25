#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 音乐加密文件解密器（macOS / 纯 Python 标准库）

支持：
  * PC 版 musicex 封装   —— .mflac / .mgg（你 iQmc 目录里的就是这种）
  * PC 版旧封装          —— 尾部内嵌 ekey 的 .mflac / .mgg
  * Android QTag         —— 尾部内嵌 ekey，可完全离线解密
  * 经典 QMCv1           —— .qmcflac / .qmc0 / .qmc3 / .qmcogg / .tkm

原理：
  1. 读取文件尾部封装，拿到 media_mid 与 media_filename；
  2. 从本机 QQ 音乐客户端读取登录态（AutoLoginUserInfo 里的 uin + authst）；
  3. 调用 QQ 音乐 CgiGetEVkey 接口换取 ekey（每个文件一次，结果会缓存）；
  4. ekey 经 TC-TEA 解出主密钥，再用 QMCv2 的 RC4/Map 流密码逐段解密；
  5. 校验解密结果的音频文件头（fLaC / OggS / ID3 ...），确认真的是音频才落盘。

只用你自己账号的登录态、解你自己下载的文件，不联网上传任何东西。

用法：
  python3 qmc2dec.py "输入目录或文件" -o "输出目录"
  python3 qmc2dec.py --self-test          # 自检内置算法向量
"""

import argparse
import base64
import json
import os
import plistlib
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MASK32 = 0xFFFFFFFF
MASK64 = 0xFFFFFFFFFFFFFFFF

# TC-TEA 参数（腾讯魔改 TEA，16 轮，CBC 带盐）
TEA_DELTA = 0x9E3779B9
TEA_ROUNDS = 16
TEA_SALT_LEN = 2
TEA_ZERO_LEN = 7

# 由 tan(106 + i*0.1)*100 取整得到的 8 字节 "simple key"（f32 语义）
EKEY_SIMPLE_KEY = bytes.fromhex("695646382b20150b")

# "QQMusic EncV2,Key:" 的 base64，以及双层 TEA 的两个固定密钥
ENC_V2_PREFIX = base64.b64decode("UVFNdXNpYyBFbmNWMixLZXk6")
ENC_V2_KEY1 = bytes.fromhex("3338365a4a592140232a24255e262928")
ENC_V2_KEY2 = bytes.fromhex("2a2a232128232425265e6131635a2c54")

# QMCv1 静态密钥表
V1_KEY_SIZE = 128
V1_OFFSET_BOUNDARY = 0x7FFF
V1_STATIC_KEY = bytes([
    0xc3, 0x4a, 0xd6, 0xca, 0x90, 0x67, 0xf7, 0x52, 0xd8, 0xa1, 0x66, 0x62, 0x9f, 0x5b, 0x09, 0x00,
    0xc3, 0x5e, 0x95, 0x23, 0x9f, 0x13, 0x11, 0x7e, 0xd8, 0x92, 0x3f, 0xbc, 0x90, 0xbb, 0x74, 0x0e,
    0xc3, 0x47, 0x74, 0x3d, 0x90, 0xaa, 0x3f, 0x51, 0xd8, 0xf4, 0x11, 0x84, 0x9f, 0xde, 0x95, 0x1d,
    0xc3, 0xc6, 0x09, 0xd5, 0x9f, 0xfa, 0x66, 0xf9, 0xd8, 0xf0, 0xf7, 0xa0, 0x90, 0xa1, 0xd6, 0xf3,
    0xc3, 0xf3, 0xd6, 0xa1, 0x90, 0xa0, 0xf7, 0xf0, 0xd8, 0xf9, 0x66, 0xfa, 0x9f, 0xd5, 0x09, 0xc6,
    0xc3, 0x1d, 0x95, 0xde, 0x9f, 0x84, 0x11, 0xf4, 0xd8, 0x51, 0x3f, 0xaa, 0x90, 0x3d, 0x74, 0x47,
    0xc3, 0x0e, 0x74, 0xbb, 0x90, 0xbc, 0x3f, 0x92, 0xd8, 0x7e, 0x11, 0x13, 0x9f, 0x23, 0x95, 0x5e,
    0xc3, 0x00, 0x09, 0x5b, 0x9f, 0x62, 0x66, 0xa1, 0xd8, 0x52, 0xf7, 0x67, 0x90, 0xca, 0xd6, 0x4a,
])

# QMCv2 RC4 参数
RC4_FIRST_SEGMENT = 0x80
RC4_SEGMENT = 0x1400
RC4_STREAM_SIZE = RC4_SEGMENT + 512

# QQ 音乐接口
API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"

# 支持的扩展名 -> 期望的输出后缀（仅用于提示；实际以后缀嗅探为准）
KNOWN_EXT = {
    "mflac", "mflac0", "mflach", "mgg", "mgg0", "mgg1", "mggl", "mmp4",
    "qmcflac", "qmcogg", "qmc0", "qmc2", "qmc3", "qmc4", "qmc6", "qmc8",
    "tkm", "bkcmp3", "bkcm4a", "bkcflac", "bkcwav", "bkcape", "bkcogg", "bkcwma",
}

MAGIC_EXT = [
    (b"fLaC", "flac"),
    (b"OggS", "ogg"),
    (b"ID3", "mp3"),
    (b"RIFF", "wav"),
    (b"ftyp", "m4a"),
]


class DecryptError(Exception):
    pass


# ---------------------------------------------------------------------------
# TC-TEA
# ---------------------------------------------------------------------------

def _tea_ecb_decrypt(block, k):
    y = (block >> 32) & MASK32
    z = block & MASK32
    s = (TEA_DELTA * TEA_ROUNDS) & MASK32
    for _ in range(TEA_ROUNDS):
        z = (z - ((((y << 4) + k[2]) & MASK32) ^ ((y + s) & MASK32) ^ ((y >> 5) + k[3]))) & MASK32
        y = (y - ((((z << 4) + k[0]) & MASK32) ^ ((z + s) & MASK32) ^ ((z >> 5) + k[1]))) & MASK32
        s = (s - TEA_DELTA) & MASK32
    return (y << 32) | z


def tea_cbc_decrypt(ciphertext, key16):
    """tc_tea 的 CBC 解密；返回去掉填充后的明文。"""
    if len(ciphertext) < 10 or len(ciphertext) % 8 != 0:
        raise DecryptError("TEA 密文长度非法: %d" % len(ciphertext))
    k = struct.unpack(">IIII", key16)
    iv1 = iv2 = 0
    out = bytearray()
    for i in range(0, len(ciphertext), 8):
        block = int.from_bytes(ciphertext[i:i + 8], "big")
        dec = _tea_ecb_decrypt((block ^ iv2) & MASK64, k)
        out += ((dec ^ iv1) & MASK64).to_bytes(8, "big")
        iv1, iv2 = block, dec
    pad = out[0] & 0x07
    start = 1 + pad + TEA_SALT_LEN
    end = len(out) - TEA_ZERO_LEN
    if end <= start or any(out[end:]):
        raise DecryptError("TEA 解密结果校验失败")
    return bytes(out[start:end])


# ---------------------------------------------------------------------------
# ekey -> 主密钥
# ---------------------------------------------------------------------------

def _b64_decode(s):
    if isinstance(s, str):
        s = s.encode("ascii", "ignore")
    s = s.strip().strip(b"\x00")
    s = b"".join(s.split())
    if not s:
        raise DecryptError("ekey 为空")
    s += b"=" * (-len(s) % 4)
    try:
        return base64.b64decode(s)
    except Exception as exc:
        raise DecryptError("ekey base64 解码失败: %s" % exc)


def _ekey_v1(decoded):
    """单层：8 字节头 + TEA 密文。失败时按裸密钥处理。"""
    if len(decoded) < 8:
        return decoded
    header, body = decoded[:8], decoded[8:]
    if not body:
        return header
    tea_key = bytearray()
    for sk, hk in zip(EKEY_SIMPLE_KEY, header):
        tea_key.append(sk)
        tea_key.append(hk)
    try:
        plain = tea_cbc_decrypt(body, bytes(tea_key))
    except DecryptError:
        return decoded          # 接口直接返回的裸密钥
    return header + plain


def ekey_to_key(ekey):
    decoded = _b64_decode(ekey)
    if decoded.startswith(ENC_V2_PREFIX):
        blob = decoded[len(ENC_V2_PREFIX):]
        stage = tea_cbc_decrypt(blob, ENC_V2_KEY1)
        stage = tea_cbc_decrypt(stage, ENC_V2_KEY2)
        cut = stage.find(b"\x00")
        if cut != -1:
            stage = stage[:cut]
        decoded = _b64_decode(stage)
    return _ekey_v1(decoded)


# ---------------------------------------------------------------------------
# QMCv1 / QMCv2 流密码
# ---------------------------------------------------------------------------

def _xor(a, b):
    n = len(a)
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(n, "little")


def _v1_transform(data, offset, key):
    """QMC 静态表变换：offset 超过 0x7FFF 后按 0x7FFF 回绕。"""
    out = bytearray(data)
    total, i, pos = len(out), 0, offset
    while i < total:
        if pos <= V1_OFFSET_BOUNDARY:
            take = min(0x8000 - pos, total - i)
            phase = pos % V1_KEY_SIZE
        else:
            r = pos % V1_OFFSET_BOUNDARY
            take = min(V1_OFFSET_BOUNDARY - r, total - i)
            phase = r % V1_KEY_SIZE
        if phase:
            base = key[phase:] + key[:phase]
        else:
            base = key
        pad = (base * ((take + V1_KEY_SIZE - 1) // V1_KEY_SIZE))[:take]
        out[i:i + take] = _xor(bytes(out[i:i + take]), pad)
        i += take
        pos += take
    return bytes(out)


def qmc1_decrypt_file(data):
    return _v1_transform(data, 0, V1_STATIC_KEY)


def key_compress(long_key):
    """128 字节以内的密钥压缩成 128 字节映射表。"""
    n = len(long_key)
    out = bytearray(V1_KEY_SIZE)
    for i in range(V1_KEY_SIZE):
        idx = (i * i + 71214) % n
        k = long_key[idx]
        sh = (idx + 4) % 8
        out[i] = ((k << sh) | (k >> sh)) & 0xFF
    return bytes(out)


class MapCipher:
    def __init__(self, key):
        self.key = key_compress(key)

    def decrypt(self, data, offset=0):
        return _v1_transform(data, offset, self.key)


def _hash_key(key):
    h = 1
    for v in key:
        if v == 0:
            continue
        nxt = (h * v) & MASK32
        if nxt == 0 or nxt <= h:
            break
        h = nxt
    return h


def _segment_key(seg_id, seed, h):
    if seed == 0:
        return 0
    denom = (seg_id + 1) * seed
    return int(h / float(denom) * 100.0)


class _RC4:
    """魔改 RC4：状态长度 = 密钥长度。"""

    def __init__(self, key):
        n = len(key)
        st = [i & 0xFF for i in range(n)]
        j = 0
        for i in range(n):
            j = (j + st[i] + key[i % n]) % n
            st[i], st[j] = st[j], st[i]
        self.st = st
        self.i = 0
        self.j = 0
        self.n = n

    def next(self):
        n = self.n
        self.i = (self.i + 1) % n
        self.j = (self.j + self.st[self.i]) % n
        self.st[self.i], self.st[self.j] = self.st[self.j], self.st[self.i]
        return self.st[(self.st[self.i] + self.st[self.j]) % n]


class Rc4Cipher:
    def __init__(self, key):
        self.key = bytes(key)
        rc4 = _RC4(self.key)
        self.h = _hash_key(self.key)
        self.stream = bytes(rc4.next() for _ in range(RC4_STREAM_SIZE))

    def _block(self, out, start, length, pos):
        n = len(self.key)
        seg, off = pos // RC4_SEGMENT, pos % RC4_SEGMENT
        skip = _segment_key(seg, self.key[seg % n], self.h) & 0x1FF
        pad = self.stream[skip + off:skip + off + length]
        out[start:start + length] = _xor(bytes(out[start:start + length]), pad)

    def decrypt(self, data, offset=0):
        out = bytearray(data)
        total, i, pos = len(out), 0, offset
        n = len(self.key)
        if pos < RC4_FIRST_SEGMENT:
            take = min(RC4_FIRST_SEGMENT - pos, total)
            for j in range(take):
                o = pos + j
                idx = _segment_key(o, self.key[o % n], self.h) % n
                out[j] ^= self.key[idx]
            i, pos = take, pos + take
        while i < total:
            take = min(RC4_SEGMENT - (pos % RC4_SEGMENT), total - i)
            self._block(out, i, take, pos)
            i += take
            pos += take
        return bytes(out)


class V1Cipher:
    def decrypt(self, data, offset=0):
        return _v1_transform(data, offset, V1_STATIC_KEY)


def make_cipher(master_key):
    if not master_key:
        raise DecryptError("主密钥为空")
    if len(master_key) <= 300:
        return MapCipher(master_key)
    return Rc4Cipher(master_key)


# ---------------------------------------------------------------------------
# 文件尾部封装解析
# ---------------------------------------------------------------------------

def _u32le(buf, off):
    return struct.unpack("<I", buf[off:off + 4])[0]


def _u32be(buf):
    return struct.unpack(">I", buf)[0]


def _utf16(buf, off, maxlen):
    raw = buf[off:off + maxlen]
    chars = []
    for i in range(0, len(raw) - 1, 2):
        ch = struct.unpack("<H", raw[i:i + 2])[0]
        if ch == 0:
            break
        chars.append(ch)
    return "".join(chr(c) for c in chars)


def parse_footer(data):
    """返回 (footer_size, kind, ekey, extra)。"""
    tail = data[-1024:]

    # 1) PC musicex：... [size][ver=1]["musicex\0"]
    if data.endswith(b"musicex\x00"):
        if len(data) < 24:
            raise DecryptError("musicex 封装过短")
        size = _u32le(data, len(data) - 16)
        ver = _u32le(data, len(data) - 12)
        if ver != 1:
            raise DecryptError("不支持的 musicex 版本: %d" % ver)
        meta_end = len(data) - 16
        meta_start = meta_end - (size - 16)
        if meta_start < 0:
            raise DecryptError("musicex 长度非法")
        meta = data[meta_start:meta_end]
        if len(meta) < 172:
            raise DecryptError("musicex 元数据不完整")
        mid = _utf16(meta, 12, 60)
        fname = _utf16(meta, 72, 100)
        if not mid:
            raise DecryptError("musicex 中未找到 media_mid")
        return size, "musicex", None, {"mid": mid, "filename": fname}

    # 2) Android QTag：[csv][size BE]["QTag"]，csv = ekey,rid,ver
    if data.endswith(b"QTag"):
        size = _u32be(data[-8:-4])
        payload = data[-8 - size:-8]
        parts = payload.decode("utf-8", "replace").split(",")
        if len(parts) != 3:
            raise DecryptError("QTag 内容格式异常")
        ekey, _rid, ver = parts
        if ver != "2":
            raise DecryptError("不支持的 QTag 版本: %s" % ver)
        return size + 8, "QTag", ekey, {}

    # 3) Android STag：只有媒体信息，没有密钥
    if data.endswith(b"STag"):
        size = _u32be(data[-8:-4])
        parts = data[-8 - size:-8].decode("utf-8", "replace").split(",")
        mid = parts[2] if len(parts) == 3 else ""
        return size + 8, "STag", None, {"mid": mid}

    # 4) PC 旧封装：末尾 4 字节 = ekey 长度
    size = _u32le(data, len(data) - 4)
    if 0 < size <= 0x500 and len(data) > size + 4:
        raw = data[-4 - size:-4].split(b"\x00")[0]
        if raw and all(chr(c) in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=" for c in raw):
            return size + 4, "PcV1", raw.decode("ascii"), {}

    # 5) 没有封装 → 经典 QMCv1
    return 0, "V1", None, {}


# ---------------------------------------------------------------------------
# 本机登录态 & 取 ekey
# ---------------------------------------------------------------------------

PLIST_PATH = Path.home() / (
    "Library/Containers/com.tencent.QQMusicMac/Data/Library/"
    "Preferences/com.tencent.QQMusicMac.plist"
)
CACHE_PATH = Path.home() / "Library/Caches/qqmusic-decrypt/ekey_cache.json"


def read_credentials():
    if not PLIST_PATH.exists():
        raise DecryptError(
            "找不到 QQ 音乐配置：%s\n    → 请先打开 QQ 音乐并登录一次" % PLIST_PATH
        )
    try:
        plist = plistlib.load(open(PLIST_PATH, "rb"))
    except Exception as exc:
        raise DecryptError("读取 QQ 音乐配置失败: %s" % exc)

    blob = plist.get("AutoLoginUserInfo")
    if not blob:
        raise DecryptError("配置里没有登录信息（AutoLoginUserInfo）\n    → 请在 QQ 音乐里登录后再试")

    try:
        inner = plistlib.loads(blob) if isinstance(blob, bytes) else blob
        objects = inner["$objects"]
    except Exception as exc:
        raise DecryptError("解析登录信息失败: %s" % exc)

    uin = authst = None
    for obj in objects:
        if isinstance(obj, dict) and "strAuthst" in obj:
            def resolve(value):
                if isinstance(value, plistlib.UID):
                    value = objects[value.data]
                return value
            authst = resolve(obj.get("strAuthst"))
            uin = resolve(obj.get("strUserAccount"))
            if uin is None and "nCurrUseId" in obj:
                uin = obj["nCurrUseId"]
            break

    if not authst or not uin:
        raise DecryptError("登录信息不完整（uin/authst 缺失）\n    → 请在 QQ 音乐里重新登录一次")
    return str(uin), str(authst)


def _load_cache():
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(cache):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(CACHE_PATH)
    except Exception:
        pass


def fetch_ekey(uin, authst, filename, mid, retries=3):
    cache = _load_cache()
    ck = "%s|%s" % (mid, filename)
    if ck in cache:
        return cache[ck]

    body = {
        "comm": {"authst": authst, "ct": "19", "cv": "1859", "uin": uin, "tmeLoginType": "3"},
        "req_1": {
            "module": "music.vkey.GetEVkey",
            "method": "CgiGetEVkey",
            "param": {
                "filename": [filename], "guid": "10000", "songmid": [mid],
                "songtype": [1], "uin": uin, "loginflag": 1, "platform": "20", "ctx": 1,
            },
        },
    }
    payload = json.dumps(body).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(API_URL, data=payload, headers={
                "Content-Type": "application/json", "User-Agent": UA, "Referer": "https://y.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read().decode("utf-8", "replace"))
            break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    else:
        raise DecryptError("请求 QQ 音乐接口失败: %s" % last)

    info = (((result.get("req_1") or {}).get("data") or {}).get("midurlinfo") or [{}])[0]
    code = info.get("result", -1)
    ekey = info.get("ekey", "") or ""
    if code == 104003:
        raise DecryptError("该曲目需要 VIP 权限，当前账号无权获取密钥")
    if code == 104005:
        raise DecryptError("登录态已失效，请重新打开 QQ 音乐并确认已登录")
    if not ekey:
        raise DecryptError("服务器未返回 ekey（result=%s），可能是权限或地区限制" % code)

    cache[ck] = ekey
    _save_cache(cache)
    return ekey


# ---------------------------------------------------------------------------
# 单文件解密
# ---------------------------------------------------------------------------

def sniff_ext(data):
    for magic, ext in MAGIC_EXT:
        if data.startswith(magic):
            return ext
    if data[:4] == b"fLaC":
        return "flac"
    if len(data) > 3 and data[0] == 0xFF and data[1] in (0xFB, 0xF3, 0xF2, 0xFA):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "m4a"
    return None


def decrypt_bytes(data, uin_auth=None, manual_ekey=None):
    footer_size, kind, ekey, extra = parse_footer(data)
    body = data[:len(data) - footer_size] if footer_size else data

    if kind == "V1":
        plain = qmc1_decrypt_file(body)
        return plain, sniff_ext(plain) or "bin", "V1"

    if kind == "STag":
        raise DecryptError("STag 格式缺少密钥信息，暂不支持")

    if not ekey:
        if manual_ekey:
            ekey = manual_ekey
        else:
            if not uin_auth:
                raise DecryptError("文件不含 ekey，且未提供登录态")
            fname = extra.get("filename") or ""
            if not fname.lower().endswith(tuple("." + e for e in KNOWN_EXT)):
                fname = (fname or extra["mid"]) + ".mflac"
            ekey = fetch_ekey(uin_auth[0], uin_auth[1], fname, extra["mid"])

    master = ekey_to_key(ekey)
    plain = make_cipher(master).decrypt(body)

    ext = sniff_ext(plain)
    if not ext:
        raise DecryptError("解密后不是可识别的音频（密钥可能不对）")
    return plain, ext, kind


def unique_path(directory, stem, ext, force=False):
    path = directory / ("%s.%s" % (stem, ext))
    if force:
        return path
    i = 1
    while path.exists():
        path = directory / ("%s (%d).%s" % (stem, i, ext))
        i += 1
    return path


def process(in_path, out_dir, uin_auth, manual_ekey=None, verbose=False, force=False):
    data = in_path.read_bytes()
    plain, ext, kind = decrypt_bytes(data, uin_auth, manual_ekey)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = unique_path(out_dir, in_path.stem, ext, force)
    out_path.write_bytes(plain)
    if verbose:
        print("     封装=%s  输出=%.1f MB" % (kind, len(plain) / 1048576.0))
    return out_path, ext


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def self_test():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  [PASS] " if cond else "  [FAIL] ") + name)
        ok = ok and cond

    check("simple key 常量", EKEY_SIMPLE_KEY.hex() == "695646382b20150b")

    # 128 字节映射表 vs 已知 RC4 主密钥长度分支
    check("MapCipher 构造", len(MapCipher(bytes(16)).key) == 128)

    # RC4 流密码自洽：解密两次应为原文（流密码 XOR 对称）
    c = Rc4Cipher(bytes(range(256)) * 2)
    blob = bytes(range(256)) * 40
    once = c.decrypt(blob)
    twice = Rc4Cipher(bytes(range(256)) * 2).decrypt(once)
    check("RC4 往返", twice == blob)
    check("RC4 确实改变数据", once != blob)

    # V1 变换自洽
    v = V1Cipher()
    check("V1 往返", v.decrypt(v.decrypt(blob)) == blob)

    print("自检结果：%s" % ("全部通过" if ok else "存在失败"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def collect_inputs(targets):
    files = []
    for target in targets:
        p = Path(target).expanduser()
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower().lstrip(".") in KNOWN_EXT:
                    files.append(child)
        elif p.is_file():
            files.append(p)
        else:
            print("[!] 路径不存在：%s" % p)
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description="QQ 音乐加密文件解密器（macOS）")
    parser.add_argument("inputs", nargs="*", help="加密文件或目录")
    parser.add_argument("-o", "--output", default=str(Path.home() / "Desktop/Decoded_Music"),
                        help="输出目录（默认 ~/Desktop/Decoded_Music）")
    parser.add_argument("-e", "--ekey", default=None, help="手动指定 ekey")
    parser.add_argument("--no-clobber", action="store_true",
                        help="不覆盖同名文件，自动加 (1)(2) 后缀")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细信息")
    parser.add_argument("--self-test", action="store_true", help="运行内置自检")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if not args.inputs:
        parser.print_help()
        return 2

    files = collect_inputs(args.inputs)
    if not files:
        print("[!] 没有找到可处理的加密文件")
        return 1

    out_dir = Path(args.output).expanduser()
    uin_auth = None
    ok = fail = 0
    failures = []

    print("输入：%d 个文件" % len(files))
    print("输出：%s\n" % out_dir)

    for idx, path in enumerate(files, 1):
        # 每个文件单独 try，避免一个失败中断整批
        try:
            if uin_auth is None:
                try:
                    uin_auth = read_credentials()
                except DecryptError as exc:
                    uin_auth = False
                    if args.verbose:
                        print("  （无法读取登录态：%s）" % exc)
            creds = uin_auth or None
            out_path, ext = process(path, out_dir, creds, args.ekey,
                                    args.verbose, not args.no_clobber)
            ok += 1
            print("[%d/%d] OK  %s -> %s" % (idx, len(files), path.name, out_path.name))
        except DecryptError as exc:
            fail += 1
            failures.append((path.name, str(exc)))
            print("[%d/%d] 失败 %s：%s" % (idx, len(files), path.name, exc))
        except Exception as exc:  # 兜底，保证批处理不中断
            fail += 1
            failures.append((path.name, repr(exc)))
            print("[%d/%d] 异常 %s：%r" % (idx, len(files), path.name, exc))

    print()
    print("转换完成，一共转换了 %d 首。" % ok)
    if fail:
        print("失败 %d 个：" % fail)
        for name, why in failures:
            print("  - %s：%s" % (name, why))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
