#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.json 관리 모듈 (v4 — 인바운드/아웃바운드 통합 스키마)
- 설정 로드/저장/백업
- 서버 데이터 검증 (ARS / AICC 구분)
- log_paths 를 {"inbound": [...], "outbound": [...]} 구조로 관리
- 레거시 스키마(문자열 리스트 log_paths, type 없음) 자동 마이그레이션
"""

import json
import copy
import shutil
import ipaddress
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent

# ── 상수 ───────────────────────────────────────────────────
SERVER_TYPES = ('ARS', 'AICC')
PURPOSES = ('inbound', 'outbound')

# 운영구분(환경): 운영(라이브) / 개발·QA(비운영). '' 는 미지정.
ENVIRONMENTS = ('운영', '개발/QA')

# ARS 로그 접근 방식: unc(네트워크 드라이브) / ssh(Windows OpenSSH). 기본 unc.
ACCESS_METHODS = ('unc', 'ssh')

# 날짜를 특정할 수 있는 플레이스홀더 (이 중 하나는 반드시 포함되어야 함)
#   AICC 예: api_{YYYY-MM-DD}.log
#   ARS  예: IS-IVR-{YYYY}-{MMDD}-{HH}.log
DATE_PLACEHOLDERS = ('{YYYY-MM-DD}', '{YYYYMMDD}', '{YYYY}', '{MM}', '{DD}', '{MMDD}')
# {HH}(시) 는 날짜 식별자가 아니라 보조 플레이스홀더
HOUR_PLACEHOLDER = '{HH}'


# ── 로드/저장/백업 ─────────────────────────────────────────
def load_config():
    """config.json 로드 (레거시 스키마는 자동 마이그레이션 후 1회 영속화)"""
    try:
        config_file = BASE_DIR / 'config.json'
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)

            config, changed = _migrate_config(config)
            if changed:
                logger.info("config.json 스키마 마이그레이션 적용 (ARS/AICC + inbound/outbound)")
                save_config(config)  # 백업 후 새 스키마로 저장
            return config
    except json.JSONDecodeError as e:
        logger.error(f"config.json 파싱 오류: {e}")
    except Exception as e:
        logger.error(f"설정 로드 오류: {e}")
    return None


def save_config(config_data):
    """config.json 저장 (백업 후)"""
    try:
        backup_config()
        config_file = BASE_DIR / 'config.json'
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        logger.info("설정 저장 완료")
        return True
    except Exception as e:
        logger.error(f"설정 저장 오류: {e}")
        return False


def backup_config():
    """config.json 백업"""
    try:
        config_file = BASE_DIR / 'config.json'
        if config_file.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = BASE_DIR / f'config.json.backup_{timestamp}'
            shutil.copy(config_file, backup_file)
            logger.info(f"설정 백업: {backup_file}")

            # 오래된 백업 정리 (직전 1개만 유지, 나머지 자동 삭제)
            backups = sorted(BASE_DIR.glob('config.json.backup_*'), reverse=True)
            for old_backup in backups[1:]:
                try:
                    old_backup.unlink()
                    logger.debug(f"오래된 백업 삭제: {old_backup}")
                except OSError as e:
                    logger.debug(f"백업 삭제 실패(무시): {old_backup} — {e}")

            return True
    except Exception as e:
        logger.error(f"백업 오류: {e}")
    return False


# ── 마이그레이션 ───────────────────────────────────────────
def _migrate_config(config):
    """
    레거시 스키마 → 신 스키마 변환.

    변환 규칙:
      1) 서버에 'type' 이 없으면 'AICC' 로 간주 (기존 서버는 전부 VGW/AICC)
      2) log_paths 가 문자열 리스트(레거시)면 → {"inbound": [], "outbound": [<기존>]}
         (기존 시스템은 아웃바운드 위주였으므로 전부 outbound 로 이전)
      3) log_paths 가 이미 dict 면 inbound/outbound 키 보강

    Returns:
        (config, changed): changed=True 면 디스크 재저장 필요
    """
    original = copy.deepcopy(config)

    servers = config.get('remote_servers', [])
    for server in servers:
        # 1) type 정규화
        if not server.get('type'):
            server['type'] = 'AICC'
        else:
            server['type'] = str(server['type']).upper()

        # 1-2) 운영구분(env) 키 보강 — 기존 서버는 전부 '미지정('')' 으로 둔다.
        server['env'] = normalize_env(server.get('env'))

        # 1-3) ARS 접근방식(access_method) 보강 — 기존 서버는 전부 'unc'(기본)
        server['access_method'] = normalize_access_method(server.get('access_method'))

        # 2~3) log_paths 정규화
        server['log_paths'] = normalize_log_paths(server.get('log_paths'))

    config['remote_servers'] = servers

    # 4) VGW 채널 모니터링 설정 블록 보강 (없으면 기본값, 비활성)
    if 'vgw_monitor' not in config or not isinstance(config.get('vgw_monitor'), dict):
        config['vgw_monitor'] = default_vgw_monitor()

    changed = (config != original)
    return config, changed


def default_vgw_monitor():
    """VGW 모니터 기본 설정 (비활성). server_id 는 사용자가 화면에서 지정."""
    return {
        'enabled': False,
        'poll_interval': 5,
        'endpoints': [
            {'name': 'VGW1', 'server_id': None,
             'inbound_port': 54001, 'outbound_port': 54002},
            {'name': 'VGW2', 'server_id': None,
             'inbound_port': 54003, 'outbound_port': 54005},
        ],
    }


def normalize_log_paths(log_paths):
    """
    log_paths 를 {"inbound": [...], "outbound": [...]} 형태로 정규화.

    - dict      → inbound/outbound 키 보강 후 반환
    - list(레거시) → 전부 outbound 로 간주
    - 그 외/None → 빈 구조
    """
    if isinstance(log_paths, dict):
        return {
            'inbound': list(log_paths.get('inbound') or []),
            'outbound': list(log_paths.get('outbound') or []),
        }
    if isinstance(log_paths, list):
        return {'inbound': [], 'outbound': list(log_paths)}
    return {'inbound': [], 'outbound': []}


def normalize_env(env):
    """운영구분 값 정규화. 허용값(ENVIRONMENTS) 외에는 ''(미지정) 으로."""
    e = (env or '').strip()
    return e if e in ENVIRONMENTS else ''


def normalize_access_method(v):
    """ARS 접근 방식 정규화. 'ssh' 만 ssh, 그 외/미지정은 'unc'(기본)."""
    return 'ssh' if str(v or '').strip().lower() == 'ssh' else 'unc'


# ── 검증 ───────────────────────────────────────────────────
def validate_ip_address(ip_str):
    """IP 주소 형식 검증"""
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False


def validate_server_data(data):
    """
    서버 데이터 유효성 검증. (errors, validated_data) 반환

    type 별 검증:
      - AICC: hostname 또는 ip 필요, SSH 접속(user/ssh_port) 기본값 보강
      - ARS : label(또는 hostname/ip) 필요, SSH 불필요 (UNC 경로로 접근)
    """
    errors = []

    server_type = (data.get('type') or 'AICC').upper()
    if server_type not in SERVER_TYPES:
        errors.append(f"type 은 {' 또는 '.join(SERVER_TYPES)} 여야 합니다")
        server_type = 'AICC'
    data['type'] = server_type

    if server_type == 'AICC':
        if not data.get('hostname') and not data.get('ip'):
            errors.append('AICC 서버는 hostname 또는 ip 가 필요합니다')
        if data.get('ip') and not validate_ip_address(data['ip']):
            errors.append('유효하지 않은 IP 주소')
        if not data.get('user'):
            data['user'] = 'loguser'
        if not data.get('ssh_port'):
            data['ssh_port'] = 22
    else:  # ARS
        # ARS 는 네트워크 드라이브(UNC)로 접근하므로 SSH 정보 불필요.
        # 식별자는 label 우선, 없으면 hostname/ip 사용.
        if not data.get('label') and not data.get('hostname') and not data.get('ip'):
            errors.append('ARS 서버는 label(또는 hostname/ip)이 필요합니다')
        if data.get('ip') and not validate_ip_address(data['ip']):
            errors.append('유효하지 않은 IP 주소')

    # 운영구분 정규화 (미지정 허용)
    data['env'] = normalize_env(data.get('env'))

    # ARS 접근방식 정규화 (unc/ssh)
    data['access_method'] = normalize_access_method(data.get('access_method'))

    # log_paths 정규화 (UI 가 list 로 보내든 dict 로 보내든 안전하게 dict 화)
    data['log_paths'] = normalize_log_paths(data.get('log_paths'))

    return errors, data


def validate_date_format(date_str):
    """YYYY-MM-DD 형식 검증"""
    if not date_str:
        return True
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def has_date_placeholder(path):
    """경로에 날짜 특정용 플레이스홀더가 하나라도 있는지"""
    return any(tok in path for tok in DATE_PLACEHOLDERS)


def validate_log_path(path):
    """
    로그 경로 플레이스홀더 검증. (ok, error_message) 반환.
    AICC/ARS 공통 — 날짜 플레이스홀더가 최소 1개 있어야 함.
    """
    if not path or not path.strip():
        return False, '경로를 입력하세요'
    if not has_date_placeholder(path):
        return False, (
            '경로에 날짜 플레이스홀더가 필요합니다. '
            '예) AICC: /path/api_{YYYY-MM-DD}.log  ·  '
            'ARS: \\\\host\\share\\IS-IVR-{YYYY}-{MMDD}-{HH}.log'
        )
    return True, None


# ── 조회 헬퍼 ──────────────────────────────────────────────
def get_log_paths(server, purpose=None):
    """
    서버의 로그 경로 리스트 반환.

    Args:
        server: 서버 dict
        purpose: 'inbound' | 'outbound' | None(둘 다 합쳐서)

    Returns:
        경로 문자열 리스트
    """
    lp = normalize_log_paths(server.get('log_paths'))
    if purpose in PURPOSES:
        return lp.get(purpose, [])
    return lp.get('inbound', []) + lp.get('outbound', [])


def get_enabled_servers(server_type=None, purpose=None, server_ids=None, env=None,
                        access_method=None):
    """
    활성화된 서버 목록 반환 (idx, server) 튜플 리스트.

    Args:
        server_type: 'ARS' | 'AICC' | None(전체)
        purpose: 'inbound' | 'outbound' | None
                 지정 시 해당 용도의 로그 경로가 있는 서버만 반환
        server_ids: 대상 서버 인덱스 리스트 (None 이면 전체)
        env: '운영' | '개발/QA' | None(전체)  운영구분 필터
        access_method: 'unc' | 'ssh' | None(전체)  ARS 접근방식 필터
    """
    config = load_config()
    if not config:
        return []

    servers = config.get('remote_servers', [])
    result = []

    for idx, server in enumerate(servers):
        if not server.get('enabled', True):
            continue
        if server_ids is not None and idx not in server_ids:
            continue
        if server_type and server.get('type', 'AICC').upper() != server_type.upper():
            continue
        if env and normalize_env(server.get('env')) != env:
            continue
        if access_method and normalize_access_method(server.get('access_method')) != access_method:
            continue
        if purpose and not get_log_paths(server, purpose):
            continue
        result.append((idx, server))

    return result


def get_server_label(server):
    """서버 표시용 식별자 (label > hostname > ip)"""
    return server.get('label') or server.get('hostname') or server.get('ip') or '(unknown)'


# ── VGW 모니터 설정 ────────────────────────────────────────
def get_vgw_monitor_config():
    """VGW 모니터 설정 반환 (없으면 기본값)."""
    config = load_config() or {}
    vm = config.get('vgw_monitor')
    if not isinstance(vm, dict):
        return default_vgw_monitor()
    return vm


def save_vgw_monitor_config(vm):
    """VGW 모니터 설정 저장(검증/정규화 후 config.json 반영)."""
    config = load_config() or {}
    endpoints = []
    for ep in (vm.get('endpoints') or []):
        try:
            sid = ep.get('server_id')
            sid = int(sid) if sid is not None and str(sid) != '' else None
        except (TypeError, ValueError):
            sid = None
        endpoints.append({
            'name': (ep.get('name') or 'VGW').strip(),
            'server_id': sid,
            'inbound_port': int(ep.get('inbound_port') or 0) or None,
            'outbound_port': int(ep.get('outbound_port') or 0) or None,
        })
    interval = int(vm.get('poll_interval') or 5)
    interval = max(1, min(interval, 60))   # 1~60초로 제한
    config['vgw_monitor'] = {
        'enabled': bool(vm.get('enabled')),
        'poll_interval': interval,
        'endpoints': endpoints,
    }
    ok = save_config(config)
    return ok, config['vgw_monitor']


def get_server_by_id(server_id):
    """인덱스로 서버 설정 반환 (VGW 모니터 SSH 대상 해석용). 없으면 None."""
    if server_id is None:
        return None
    config = load_config() or {}
    servers = config.get('remote_servers', [])
    try:
        idx = int(server_id)
    except (TypeError, ValueError):
        return None
    if 0 <= idx < len(servers):
        return servers[idx]
    return None
