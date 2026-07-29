#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VGW 모니터 진단 스크립트 (단독 실행)

목적: 웹앱/스레드 복잡성 없이, 설정된 VGW 엔드포인트에 실제로 붙어
      raw 스트림과 파싱 결과를 눈으로 확인한다.

사용:
    python diag_vgw.py                 # 첫 엔드포인트 inbound, 15초
    python diag_vgw.py VGW1 outbound   # 지정 엔드포인트/방향
    python diag_vgw.py VGW2 inbound 30 # 30초 동안 수집

전제: config.json 의 vgw_monitor.endpoints[].server_id 가 지정되어 있어야 함
      (SSH 로 붙을 대상 서버). 서버정보는 화면/설정에서 미리 넣어두세요.
"""

import sys
import time
import subprocess

from config_manager import get_vgw_monitor_config, get_server_by_id
from vgw_monitor import VgwCollector, VgwStreamParser, STATUS_LABELS


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else None
    direction = sys.argv[2] if len(sys.argv) > 2 else 'inbound'
    duration = int(sys.argv[3]) if len(sys.argv) > 3 else 15

    vm = get_vgw_monitor_config()
    eps = vm.get('endpoints', [])
    if not eps:
        print("[!] vgw_monitor.endpoints 가 비어 있습니다. 설정을 먼저 하세요.")
        return

    ep = None
    if name:
        ep = next((e for e in eps if e.get('name') == name), None)
    ep = ep or eps[0]

    port = ep.get('inbound_port') if direction == 'inbound' else ep.get('outbound_port')
    sid = ep.get('server_id')
    interval = int(vm.get('poll_interval', 5) or 5)

    print(f"== VGW 진단 ==")
    print(f"엔드포인트: {ep.get('name')} / {direction} / port={port} / server_id={sid} / interval={interval}s")

    server = get_server_by_id(sid)
    if not server:
        print(f"[!] server_id={sid} 서버 설정을 찾을 수 없습니다. (config.json remote_servers 확인)")
        return
    if not port:
        print(f"[!] {direction} 포트가 설정되지 않았습니다.")
        return

    # 수집기의 명령 구성 로직 재사용
    dummy = VgwCollector(lambda: vm, get_server_by_id)
    cmd = dummy._build_ssh_cmd(server, int(port), interval)
    # 표시용: 원격 명령/타겟만 (민감정보 최소 노출)
    print(f"SSH 타겟: {cmd[-2]}")
    print(f"원격 명령: {cmd[-1]}")
    print(f"--- {duration}초 동안 수집 (raw 앞부분 + 파싱 스냅샷) ---\n")

    parser = VgwStreamParser()
    raw_shown = 0
    snaps = 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='ignore', bufsize=1)
    t0 = time.time()
    try:
        for line in proc.stdout:
            if raw_shown < 60:
                print("RAW|", line.rstrip())
                raw_shown += 1
            for snap in parser.feed(line):
                snaps += 1
                cnt = {}
                for ch in snap['channels']:
                    cnt[ch['status']] = cnt.get(ch['status'], 0) + 1
                labeled = {STATUS_LABELS.get(k, k): v for k, v in cnt.items()}
                print(f"\n[스냅샷 {snaps}] time={snap['time']} "
                      f"busy={snap['busy']} register={snap['register']} total={snap['total']}")
                print(f"  파싱 채널수={len(snap['channels'])} 상태분포={labeled}")
                busy = [(c['num'], c['calltime']) for c in snap['channels'] if c['status'] == 'B']
                if busy:
                    print(f"  통화중(B): {busy[:10]}{' ...' if len(busy) > 10 else ''}")
            if time.time() - t0 > duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        err = ''
        try:
            err = (proc.stderr.read() or '').strip()
        except Exception:
            pass
        if err:
            print(f"\n[stderr] {err[:500]}")

    print(f"\n== 완료: 스냅샷 {snaps}개 수신 ==")
    if snaps == 0:
        print("스냅샷 0개 — 점검사항:")
        print("  1) SSH 로 해당 서버 접속이 되는지 (키 인증)")
        print("  2) 원격에서 bash 및 /dev/tcp 사용 가능한지")
        print("  3) telnet 127.0.0.1 <port> 로 mon 이 실제로 응답하는지")
        print("  4) mon 명령 형식('mon start N')이 맞는지")


if __name__ == '__main__':
    main()
