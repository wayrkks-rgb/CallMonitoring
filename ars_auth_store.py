#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARS 인증정보 저장소 (ars_auth.json)
- 비밀번호는 Windows DPAPI 로 암호화하여 저장 (평문 저장 금지)
- win32crypt 의존을 이 모듈 한 곳에 격리

ars_auth.json 구조:
{
  "user": "ivradmin",
  "password_enc": "<base64(DPAPI blob)>",
  "scope": "user" | "machine"
}

보안 메모:
- scope="user": 암호화한 "그 사용자 계정"에서만 복호 가능 (권장, 가장 안전)
- scope="machine": 같은 머신의 모든 계정이 복호 가능 (서비스가 LocalSystem 일 때 운영 편의)
- 어느 쪽이든 app 고정 entropy 로 묶어 다른 앱이 함부로 복호하지 못하게 함
"""

import json
import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import win32crypt
    _HAS_WIN32CRYPT = True
except ImportError:
    win32crypt = None
    _HAS_WIN32CRYPT = False

BASE_DIR = Path(__file__).parent
ARS_AUTH_FILE = BASE_DIR / 'ars_auth.json'

# app 고정 entropy (비밀이라기보단 "이 앱 전용" 바인딩 용도)
_ENTROPY = b'vgw-ars-logmon-v4-dpapi'

# DPAPI 플래그
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_CRYPTPROTECT_LOCAL_MACHINE = 0x4


class ArsAuthError(Exception):
    """ARS 인증정보 처리 오류"""


# ── DPAPI 암/복호 ──────────────────────────────────────────
def encrypt_password(plain, machine_scope=False):
    """평문 비밀번호 → base64(DPAPI blob)"""
    if not _HAS_WIN32CRYPT:
        raise ArsAuthError('win32crypt 미설치 (pywin32 반입 필요)')
    flags = _CRYPTPROTECT_UI_FORBIDDEN
    if machine_scope:
        flags |= _CRYPTPROTECT_LOCAL_MACHINE
    blob = win32crypt.CryptProtectData(
        plain.encode('utf-8'), 'ars_auth', _ENTROPY, None, None, flags
    )
    return base64.b64encode(blob).decode('ascii')


def decrypt_password(password_enc):
    """base64(DPAPI blob) → 평문 비밀번호"""
    if not _HAS_WIN32CRYPT:
        raise ArsAuthError('win32crypt 미설치 (pywin32 반입 필요)')
    try:
        blob = base64.b64decode(password_enc)
    except Exception as e:
        raise ArsAuthError(f'password_enc base64 디코드 실패: {e}')
    try:
        _desc, data = win32crypt.CryptUnprotectData(
            blob, _ENTROPY, None, None, _CRYPTPROTECT_UI_FORBIDDEN
        )
    except Exception as e:
        raise ArsAuthError(
            f'DPAPI 복호 실패: {e}. '
            f'암호화한 계정과 현재(서비스) 계정이 다르거나, '
            f'scope=user 인데 다른 계정에서 복호 시도했을 수 있습니다.'
        )
    return data.decode('utf-8')


# ── 파일 입출력 ────────────────────────────────────────────
def save_ars_auth(user, plain_password, machine_scope=False, path=None):
    """사용자/평문비번을 받아 암호화 후 ars_auth.json 저장"""
    target = Path(path) if path else ARS_AUTH_FILE
    enc = encrypt_password(plain_password, machine_scope=machine_scope)
    payload = {
        'user': user,
        'password_enc': enc,
        'scope': 'machine' if machine_scope else 'user',
    }
    with open(target, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info(f"ars_auth 저장: {target} (user={user}, scope={payload['scope']})")
    return target


def load_ars_auth(path=None):
    """ars_auth.json 로드 (복호화 안 함, 원본 dict 반환)"""
    target = Path(path) if path else ARS_AUTH_FILE
    if not target.exists():
        raise ArsAuthError(
            f'{target} 없음. set_ars_auth.py 로 ARS 계정을 먼저 등록하세요.'
        )
    with open(target, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not data.get('user') or not data.get('password_enc'):
        raise ArsAuthError('ars_auth.json 형식 오류 (user/password_enc 누락)')
    return data


def get_ars_credentials(path=None):
    """복호화된 {'user':..., 'password':...} 반환 (연결 매니저가 사용)"""
    data = load_ars_auth(path)
    return {
        'user': data['user'],
        'password': decrypt_password(data['password_enc']),
    }
