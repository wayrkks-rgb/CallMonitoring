#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scenario_fetcher.py — 시나리오 서버(Windows)의 OUTPUT 폴더 dxml 을
로컬 캐시(scenario_cache/<env>/)로 당겨오는 모듈. (선택 — 3단계)

- OpenSSH 네이티브 사용 (기존 ssh_fetcher 패턴과 동일)
- 변경 감지: 원격 파일 목록+수정시각 서명을 비교해 바뀐 경우에만 전체 pull
- 전송: 폴더를 tar 스트림으로 1회에 받아 로컬에서 해제 (파일당 SSH 열지 않음)

환경 설정 예 (config.json 에 scenario_servers 추가):
  "scenario_servers": {
    "운영":     { "ssh": "scen-prod",  "output": "C:\\\\IS-IVR\\\\scenario\\\\output" },
    "개발·QA":  { "ssh": "scen-dev",   "output": "C:\\\\IS-IVR\\\\scenario\\\\output" }
  }
  ssh 값은 ~/.ssh/config 의 Host alias (Port 41 등은 alias 에 설정).
"""
import os
import subprocess
import tarfile
import tempfile
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_ROOT = os.path.join(BASE_DIR, "scenario_cache")

SSH_OPTS = ['-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=3', '-o', 'StrictHostKeyChecking=accept-new']


def _sig_path(env):
    return os.path.join(CACHE_ROOT, env, ".remote_sig")


def remote_signature(ssh_alias, output_dir):
    """
    원격 OUTPUT 폴더의 dxml 목록+크기+수정시각 서명. (변경 감지용)
    Windows OpenSSH 기본 셸(cmd)에서 PowerShell 로 조회.
    """
    ps = (f"Get-ChildItem -Path '{output_dir}\\*.dxml','{output_dir}\\*.xml' "
          f"-ErrorAction SilentlyContinue | "
          f"ForEach-Object {{ $_.Name + '|' + $_.Length + '|' + $_.LastWriteTimeUtc.Ticks }}")
    cmd = ['ssh'] + SSH_OPTS + [ssh_alias, 'powershell', '-NoProfile', '-Command', ps]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                           encoding='utf-8', errors='ignore')
        if r.returncode != 0:
            logger.error(f"원격 서명 조회 실패: {r.stderr.strip()}")
            return None
        return "\n".join(sorted(l.strip() for l in r.stdout.splitlines() if l.strip()))
    except Exception as e:
        logger.exception(f"원격 서명 오류: {e}")
        return None


def pull(env, ssh_alias, output_dir, force=False):
    """
    원격 OUTPUT → 로컬 캐시. 변경 없으면 스킵(force=True 면 무조건).
    Returns: (changed: bool, message: str)
    """
    dest = os.path.join(CACHE_ROOT, env)
    os.makedirs(dest, exist_ok=True)

    sig = remote_signature(ssh_alias, output_dir)
    if sig is None:
        return False, "원격 접속/조회 실패"

    sig_file = _sig_path(env)
    if not force and os.path.isfile(sig_file):
        with open(sig_file, 'r', encoding='utf-8') as f:
            if f.read() == sig:
                return False, "변경 없음"

    # tar 스트림으로 폴더 통째 수신 (Windows 10/Server 2019+ 내장 tar.exe)
    ps = f"cd /d {output_dir} && tar cf - *.dxml *.xml"
    cmd = ['ssh'] + SSH_OPTS + [ssh_alias, ps]
    try:
        with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as tf:
            tar_path = tf.name
            r = subprocess.run(cmd, stdout=tf, stderr=subprocess.PIPE, timeout=120)
        if r.returncode != 0:
            os.unlink(tar_path)
            return False, f"pull 실패: {r.stderr.decode('utf-8','ignore').strip()}"

        # 기존 캐시 비우고 새로 해제 (삭제된 시나리오 반영)
        for old in os.listdir(dest):
            if old.endswith(('.dxml', '.xml')):
                os.unlink(os.path.join(dest, old))
        with tarfile.open(tar_path) as t:
            t.extractall(dest)
        os.unlink(tar_path)

        with open(sig_file, 'w', encoding='utf-8') as f:
            f.write(sig)
        n = len([x for x in os.listdir(dest) if x.endswith(('.dxml', '.xml'))])
        logger.info(f"[{env}] 시나리오 {n}개 수신 완료")
        return True, f"{n}개 수신"
    except Exception as e:
        logger.exception(f"pull 오류: {e}")
        return False, str(e)


def poll_all(scenario_servers):
    """
    모든 환경을 순회하며 변경분만 pull. 배포 감지 스케줄러에서 호출.
    scenario_servers: {env: {"ssh":alias, "output":dir}}
    Returns: {env: (changed, message)}
    """
    result = {}
    for env, cfg in (scenario_servers or {}).items():
        result[env] = pull(env, cfg.get('ssh'), cfg.get('output'))
    return result


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) >= 4:
        env, alias, out = sys.argv[1], sys.argv[2], sys.argv[3]
        print(pull(env, alias, out, force='--force' in sys.argv))
    else:
        print("사용: python scenario_fetcher.py <env> <ssh_alias> <output_dir> [--force]")
