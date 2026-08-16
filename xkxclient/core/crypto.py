from __future__ import annotations

"""D1 密码加密：固定算法、无需主密码（便捷优先，安全适中）。

明文仅运行时在内存中解密，不写盘。算法对密码明文不可逆性要求不高，
仅防直接明文泄漏；密钥硬编码固定。
"""

import base64
import hashlib

_KEY = hashlib.sha256(b"EasyBXb::account::v1").digest()


def _xor(data: bytes) -> bytes:
    return bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(data))


def encrypt_password(plain: str) -> str:
    """加密密码 → 可写盘字符串。空串原样返回。"""
    if not plain:
        return ""
    raw = _xor(plain.encode("utf-8"))
    return base64.b64encode(raw).decode("ascii")


def decrypt_password(enc: str) -> str:
    """解密密码 → 明文。空串或非法串原样返回。"""
    if not enc:
        return ""
    try:
        raw = base64.b64decode(enc.encode("ascii"))
        return _xor(raw).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return enc
