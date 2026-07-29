#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""서버 관리 라우트 — CRUD, 로그 경로 관리(inbound/outbound), SSH 키 등록

v4 변경점:
  - 서버에 type(ARS/AICC) / label 지원
  - 로그 경로를 purpose(inbound/outbound)별로 등록/삭제
  - 식별/경로 조회는 config_manager 헬퍼로 통일
"""

from flask import Blueprint, request, jsonify
import logging
import re
import base64
from pathlib import Path

from config_manager import (
    load_config, save_config, validate_server_data,
    normalize_log_paths, normalize_env, normalize_access_method, validate_log_path,
    get_server_label, PURPOSES,
)
import ars_auth_store

logger = logging.getLogger(__name__)

servers_bp = Blueprint('servers', __name__)


# ── 서버 CRUD ─────────────────────────────────────────────

@servers_bp.route('/servers', methods=['GET'])
def get_servers():
    """서버 목록 조회"""
    try:
        config = load_config()
        if not config:
            return jsonify({'success': False, 'error': '설정 로드 실패'})

        servers = config.get('remote_servers', [])
        server_list = []

        for idx, server in enumerate(servers):
            server_list.append({
                'id': idx,
                'type': server.get('type', 'AICC'),
                'env': server.get('env', ''),
                'access_method': server.get('access_method', 'unc'),
                'label': server.get('label', ''),
                'display_name': get_server_label(server),
                'hostname': server.get('hostname', ''),
                'ip': server.get('ip', ''),
                'user': server.get('user', 'loguser'),
                'ssh_port': server.get('ssh_port', 22),
                'enabled': server.get('enabled', True),
                'log_paths': normalize_log_paths(server.get('log_paths')),
            })

        return jsonify({'success': True, 'servers': server_list})

    except Exception as e:
        logger.exception(f"서버 목록 조회 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


@servers_bp.route('/servers', methods=['POST'])
def add_server():
    """서버 추가 (type: ARS | AICC)"""
    try:
        data = request.get_json()

        errors, validated = validate_server_data(data)
        if errors:
            return jsonify({'success': False, 'error': ', '.join(errors)})

        config = load_config()
        if not config:
            return jsonify({'success': False, 'error': '설정 로드 실패'})

        server_type = validated['type']

        # 공통 필드
        new_server = {
            'type': server_type,
            'env': validated.get('env', ''),
            'access_method': validated.get('access_method', 'unc'),
            'label': validated.get('label', ''),
            'hostname': validated.get('hostname', ''),
            'ip': validated.get('ip', ''),
            'enabled': True,
            'log_paths': validated['log_paths'],  # 정규화된 dict
        }
        # AICC 전용: SSH 접속 정보
        if server_type == 'AICC':
            new_server['user'] = validated.get('user', 'loguser')
            new_server['ssh_port'] = validated.get('ssh_port', 22)
            new_server['ssh_key_path'] = validated.get('ssh_key_path')

        # 중복 검사 (hostname / ip / label)
        servers = config.get('remote_servers', [])
        for server in servers:
            if new_server['hostname'] and server.get('hostname') == new_server['hostname']:
                return jsonify({'success': False, 'error': f'중복 호스트명: {new_server["hostname"]}'})
            if new_server['ip'] and server.get('ip') == new_server['ip']:
                return jsonify({'success': False, 'error': f'중복 IP: {new_server["ip"]}'})
            if new_server['label'] and server.get('label') == new_server['label']:
                return jsonify({'success': False, 'error': f'중복 라벨: {new_server["label"]}'})

        servers.append(new_server)
        config['remote_servers'] = servers

        if save_config(config):
            logger.info(f"서버 추가: [{server_type}] {get_server_label(new_server)}")
            return jsonify({'success': True, 'message': '서버 추가 완료', 'server': new_server})
        else:
            return jsonify({'success': False, 'error': '설정 저장 실패'})

    except Exception as e:
        logger.exception(f"서버 추가 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


@servers_bp.route('/servers/<int:server_id>', methods=['PUT'])
def update_server(server_id):
    """서버 수정"""
    try:
        data = request.get_json()

        config = load_config()
        if not config:
            return jsonify({'success': False, 'error': '설정 로드 실패'})

        servers = config.get('remote_servers', [])
        if server_id < 0 or server_id >= len(servers):
            return jsonify({'success': False, 'error': '서버를 찾을 수 없음'})

        target = servers[server_id]

        # 전달된 필드만 갱신
        if 'type' in data and data['type']:
            t = str(data['type']).upper()
            if t in ('ARS', 'AICC'):
                target['type'] = t
        if 'env' in data:
            target['env'] = normalize_env(data.get('env'))
        if 'access_method' in data:
            target['access_method'] = normalize_access_method(data.get('access_method'))
        for field in ('label', 'hostname', 'ip', 'user', 'ssh_port', 'enabled', 'ssh_key_path'):
            if field in data:
                target[field] = data[field]

        # log_paths 가 명시적으로 전달된 경우에만 갱신 (경로 추가/삭제는 전용 라우트 사용 권장)
        if 'log_paths' in data:
            target['log_paths'] = normalize_log_paths(data['log_paths'])
        else:
            target['log_paths'] = normalize_log_paths(target.get('log_paths'))

        servers[server_id] = target
        config['remote_servers'] = servers

        if save_config(config):
            return jsonify({'success': True, 'message': '서버 수정 완료'})
        else:
            return jsonify({'success': False, 'error': '설정 저장 실패'})

    except Exception as e:
        logger.exception(f"서버 수정 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


@servers_bp.route('/servers/<int:server_id>', methods=['DELETE'])
def delete_server(server_id):
    """서버 삭제"""
    try:
        config = load_config()
        if not config:
            return jsonify({'success': False, 'error': '설정 로드 실패'})

        servers = config.get('remote_servers', [])
        if server_id < 0 or server_id >= len(servers):
            return jsonify({'success': False, 'error': '서버를 찾을 수 없음'})

        deleted_server = servers.pop(server_id)
        config['remote_servers'] = servers

        if save_config(config):
            logger.info(f"서버 삭제: {get_server_label(deleted_server)}")
            return jsonify({'success': True, 'message': '서버 삭제 완료'})
        else:
            return jsonify({'success': False, 'error': '설정 저장 실패'})

    except Exception as e:
        logger.exception(f"서버 삭제 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


# ── 로그 경로 관리 (purpose: inbound | outbound) ──────────

def _validate_purpose(purpose):
    """purpose 값 검증. (ok, normalized_or_none)"""
    p = (purpose or '').strip().lower()
    if p not in PURPOSES:
        return False, None
    return True, p


@servers_bp.route('/servers/<int:server_id>/log-paths', methods=['GET'])
def get_server_log_paths(server_id):
    """
    서버의 로그 경로 조회.
    ?purpose=inbound|outbound 지정 시 해당 용도만, 미지정 시 전체 dict 반환.
    """
    try:
        config = load_config()
        if not config:
            return jsonify({'success': False, 'error': '설정 로드 실패'})

        servers = config.get('remote_servers', [])
        if server_id < 0 or server_id >= len(servers):
            return jsonify({'success': False, 'error': '서버를 찾을 수 없음'})

        server = servers[server_id]
        log_paths = normalize_log_paths(server.get('log_paths'))

        purpose = request.args.get('purpose')
        if purpose:
            ok, p = _validate_purpose(purpose)
            if not ok:
                return jsonify({'success': False, 'error': "purpose 는 inbound 또는 outbound"})
            log_paths = {p: log_paths.get(p, [])}

        return jsonify({
            'success': True,
            'log_paths': log_paths,
            'server': {
                'type': server.get('type', 'AICC'),
                'display_name': get_server_label(server),
                'hostname': server.get('hostname', ''),
                'ip': server.get('ip', ''),
                'label': server.get('label', ''),
            }
        })

    except Exception as e:
        logger.exception(f"로그 경로 조회 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


@servers_bp.route('/servers/<int:server_id>/log-paths', methods=['POST'])
def add_log_path(server_id):
    """서버에 로그 경로 추가 (purpose 필수)"""
    try:
        data = request.get_json()
        purpose = data.get('purpose')
        new_path = (data.get('path') or '').strip()

        ok, p = _validate_purpose(purpose)
        if not ok:
            return jsonify({'success': False, 'error': 'purpose 를 선택하세요 (inbound 또는 outbound)'})

        ok_path, err = validate_log_path(new_path)
        if not ok_path:
            return jsonify({'success': False, 'error': err})

        config = load_config()
        if not config:
            return jsonify({'success': False, 'error': '설정 로드 실패'})

        servers = config.get('remote_servers', [])
        if server_id < 0 or server_id >= len(servers):
            return jsonify({'success': False, 'error': '서버를 찾을 수 없음'})

        log_paths = normalize_log_paths(servers[server_id].get('log_paths'))

        if new_path in log_paths[p]:
            return jsonify({'success': False, 'error': f'이미 존재하는 경로 ({p})'})

        log_paths[p].append(new_path)
        servers[server_id]['log_paths'] = log_paths
        config['remote_servers'] = servers

        if save_config(config):
            logger.info(f"로그 경로 추가[{p}]: {new_path} → 서버 {server_id}")
            return jsonify({'success': True, 'message': '경로 추가 완료', 'purpose': p, 'path': new_path})
        else:
            return jsonify({'success': False, 'error': '설정 저장 실패'})

    except Exception as e:
        logger.exception(f"로그 경로 추가 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


@servers_bp.route('/servers/<int:server_id>/log-paths', methods=['DELETE'])
def delete_log_path(server_id):
    """서버에서 로그 경로 삭제 (purpose 필수)"""
    try:
        data = request.get_json()
        purpose = data.get('purpose')
        path_to_delete = (data.get('path') or '').strip()

        ok, p = _validate_purpose(purpose)
        if not ok:
            return jsonify({'success': False, 'error': 'purpose 를 선택하세요 (inbound 또는 outbound)'})
        if not path_to_delete:
            return jsonify({'success': False, 'error': '경로를 입력하세요'})

        config = load_config()
        if not config:
            return jsonify({'success': False, 'error': '설정 로드 실패'})

        servers = config.get('remote_servers', [])
        if server_id < 0 or server_id >= len(servers):
            return jsonify({'success': False, 'error': '서버를 찾을 수 없음'})

        log_paths = normalize_log_paths(servers[server_id].get('log_paths'))

        # 정확 일치 우선, 없으면 앞뒤 공백 차이는 관용 매칭 (기존 오염 데이터 대비)
        if path_to_delete in log_paths[p]:
            target = path_to_delete
        else:
            target = next((x for x in log_paths[p]
                           if x.strip() == path_to_delete.strip()), None)
        if target is None:
            return jsonify({'success': False, 'error': f'경로를 찾을 수 없음 ({p})'})

        log_paths[p].remove(target)
        servers[server_id]['log_paths'] = log_paths
        config['remote_servers'] = servers

        if save_config(config):
            logger.info(f"로그 경로 삭제[{p}]: {path_to_delete} ← 서버 {server_id}")
            return jsonify({'success': True, 'message': '경로 삭제 완료'})
        else:
            return jsonify({'success': False, 'error': '설정 저장 실패'})

    except Exception as e:
        logger.exception(f"로그 경로 삭제 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


# ── SSH 키 등록 (AICC 전용) ───────────────────────────────

@servers_bp.route('/servers/register-key', methods=['POST'])
def register_server_key():
    """SSH 키 자동 등록 (paramiko 사용, AICC 서버용)"""
    try:
        try:
            import paramiko
        except ImportError:
            return jsonify({
                'success': False,
                'error': 'paramiko 미설치. pip install paramiko 실행 후 재시도하세요.'
            })

        data = request.get_json()
        ip = (data.get('ip') or '').strip()
        user = (data.get('user') or 'loguser').strip()
        password = (data.get('password') or '').strip()
        port = int(data.get('ssh_port') or 22)
        label = (data.get('hostname') or ip).strip()

        if not ip:
            return jsonify({'success': False, 'error': 'IP 주소를 입력하세요'})
        if not password:
            return jsonify({'success': False, 'error': '패스워드를 입력하세요'})

        # 키 생성 및 등록
        ssh_dir = Path.home() / '.ssh'
        ssh_dir.mkdir(mode=0o700, exist_ok=True)

        safe_label = re.sub(r'[^\w\-]', '_', label)
        private_key_path = ssh_dir / f'id_rsa_{safe_label}'
        public_key_path = ssh_dir / f'id_rsa_{safe_label}.pub'

        key = paramiko.RSAKey.generate(bits=4096)
        key.write_private_key_file(str(private_key_path))
        private_key_path.chmod(0o600)

        pub_key_str = f"ssh-rsa {key.get_base64()} log-analyzer@{safe_label}"
        public_key_path.write_text(pub_key_str + '\n', encoding='utf-8')

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=ip, port=port, username=user, password=password,
            timeout=15, allow_agent=False, look_for_keys=False
        )

        # ── 서버에 공개키 등록 (OS 별 분기)
        platform_hint = (data.get('platform') or '').lower()
        is_windows = (platform_hint == 'windows') or ((data.get('type') or '').upper() == 'ARS')

        if is_windows:
            # Windows OpenSSH 관리자 계정 전용 경로:
            #   개인 ~/.ssh/authorized_keys 는 '관리자'에겐 무시된다.
            #   C:\ProgramData\ssh\administrators_authorized_keys 에 등록 + icacls 로
            #   Administrators/SYSTEM 만 권한을 남겨야 sshd 가 인정한다.
            #   따옴표 문제 회피 위해 PowerShell -EncodedCommand(UTF-16LE base64) 사용.
            key_line = pub_key_str.replace("'", "''")
            ps = (
                "$ErrorActionPreference='Stop';"
                "$d='C:\\ProgramData\\ssh';"
                "if(-not(Test-Path $d)){New-Item -ItemType Directory -Path $d -Force|Out-Null};"
                "$f=Join-Path $d 'administrators_authorized_keys';"
                f"$key='{key_line}';"
                "if(-not(Test-Path $f)){New-Item -ItemType File -Path $f -Force|Out-Null};"
                "$ex=@(Get-Content -LiteralPath $f -ErrorAction SilentlyContinue);"
                "if($ex -notcontains $key){Add-Content -LiteralPath $f -Value $key};"
                "icacls $f /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F'|Out-Null;"
                "Write-Output 'REG_OK'"
            )
            enc = base64.b64encode(ps.encode('utf-16-le')).decode('ascii')
            stdin, stdout, stderr = client.exec_command(
                f'powershell -NoProfile -NonInteractive -EncodedCommand {enc}')
            out = stdout.read().decode('utf-8', 'ignore')
            err = stderr.read().decode('utf-8', 'ignore')
            stdout.channel.recv_exit_status()
            if 'REG_OK' not in out:
                client.close()
                return jsonify({'success': False,
                                'error': f'키 등록 실패(Windows): {(err or out)[:250]}'})
        else:
            commands = [
                'mkdir -p ~/.ssh && chmod 700 ~/.ssh',
                f'echo "{pub_key_str}" >> ~/.ssh/authorized_keys',
                'chmod 600 ~/.ssh/authorized_keys',
                'sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys'
            ]
            for cmd in commands:
                stdin, stdout, stderr = client.exec_command(cmd)
                stdout.channel.recv_exit_status()

        client.close()
        logger.info(f"SSH 키 등록 완료: {user}@{ip} (windows={is_windows})")

        # ── config.json 의 해당 서버에 ssh_key_path 자동 저장
        srv_type = 'ARS' if is_windows else 'AICC'
        config = load_config()
        if config:
            servers = config.get('remote_servers', [])
            found = False
            for server in servers:
                if (server.get('ip') == ip) or (server.get('hostname') == label and label != ip):
                    server['type'] = srv_type
                    if is_windows:
                        server['access_method'] = 'ssh'   # ARS-SSH 확정
                    server['ssh_key_path'] = str(private_key_path)
                    server['log_paths'] = normalize_log_paths(server.get('log_paths'))
                    found = True
                    logger.info(f"기존 서버에 ssh_key_path 업데이트: {ip}")
                    break

            if not found:
                new_server = {
                    'type': srv_type,
                    'access_method': 'ssh' if is_windows else 'unc',
                    'label': '',
                    'hostname': label if label != ip else '',
                    'ip': ip,
                    'user': user,
                    'ssh_port': port,
                    'ssh_key_path': str(private_key_path),
                    'enabled': True,
                    'log_paths': {'inbound': [], 'outbound': []}
                }
                servers.append(new_server)
                config['remote_servers'] = servers
                logger.info(f"config.json 에 서버 신규 추가: {ip}")

            save_config(config)

        return jsonify({
            'success': True,
            'message': 'SSH 키 등록 완료. 이후 패스워드 없이 접속됩니다.',
            'key_path': str(private_key_path)
        })

    except Exception as e:
        logger.exception(f"키 등록 오류: {e}")
        error_msg = str(e)
        if 'Authentication' in error_msg:
            error_msg = '패스워드 인증 실패. 사용자명/패스워드를 확인하세요.'
        return jsonify({'success': False, 'error': error_msg})


# ── ARS 공용 계정 (DPAPI) ─────────────────────────────────
# ARS 전대 공통 계정을 웹에서 등록/조회/삭제.
# 비밀번호는 DPAPI 로 암호화되어 ars_auth.json 에 저장되며, 응답으로 절대 내려보내지 않음.

@servers_bp.route('/ars-auth', methods=['GET'])
def get_ars_auth():
    """ARS 계정 등록 상태 조회 (user/scope 만, 비번은 반환 안 함)"""
    try:
        data = ars_auth_store.load_ars_auth()
        return jsonify({
            'success': True,
            'registered': True,
            'user': data.get('user', ''),
            'scope': data.get('scope', 'user'),
        })
    except ars_auth_store.ArsAuthError:
        return jsonify({'success': True, 'registered': False})
    except Exception as e:
        logger.exception(f"ARS 계정 조회 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


@servers_bp.route('/ars-auth', methods=['POST'])
def set_ars_auth():
    """ARS 공용 계정 등록 (DPAPI 암호화 저장 + 즉시 복호 검증)"""
    try:
        if not ars_auth_store._HAS_WIN32CRYPT:
            return jsonify({
                'success': False,
                'error': 'win32crypt(pywin32) 미설치. pywin32 반입/설치 후 다시 시도하세요.'
            })

        data = request.get_json(force=True, silent=True) or {}
        user = (data.get('user') or '').strip()
        password = (data.get('password') or '')
        machine_scope = bool(data.get('machine_scope', False))

        if not user:
            return jsonify({'success': False, 'error': '계정명을 입력하세요'})
        if not password:
            return jsonify({'success': False, 'error': '비밀번호를 입력하세요'})

        # 저장 (DPAPI 암호화)
        ars_auth_store.save_ars_auth(user, password, machine_scope=machine_scope)

        # 즉시 복호 검증 (현재 = Flask 구동 계정에서 복호되는지)
        try:
            creds = ars_auth_store.get_ars_credentials()
            verified = (creds['user'] == user and creds['password'] == password)
        except ars_auth_store.ArsAuthError as e:
            return jsonify({'success': False, 'error': f'저장은 됐으나 복호 검증 실패: {e}'})

        if not verified:
            return jsonify({'success': False, 'error': '복호 검증 불일치'})

        logger.info(f"ARS 계정 등록 완료: {user} (scope={'machine' if machine_scope else 'user'})")
        return jsonify({
            'success': True,
            'message': 'ARS 계정이 저장되었습니다 (복호 검증 성공)',
            'user': user,
            'scope': 'machine' if machine_scope else 'user',
        })

    except ars_auth_store.ArsAuthError as e:
        return jsonify({'success': False, 'error': str(e)})
    except Exception as e:
        logger.exception(f"ARS 계정 등록 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


@servers_bp.route('/ars-auth', methods=['DELETE'])
def delete_ars_auth():
    """ARS 계정 정보 삭제 (ars_auth.json 제거)"""
    try:
        f = ars_auth_store.ARS_AUTH_FILE
        if f.exists():
            f.unlink()
            logger.info("ARS 계정 정보 삭제됨")
        return jsonify({'success': True, 'message': 'ARS 계정 정보가 삭제되었습니다'})
    except Exception as e:
        logger.exception(f"ARS 계정 삭제 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})
