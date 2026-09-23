# -*- coding: utf-8 -*-
"""
diag_servers.py — config.json 서버 등록 상태 점검 / 정리

'서버를 지웠는데 계속 중복이라고 나온다', '등록했는데 정보가 빠져 있다' 처럼
설정이 꼬였을 때, 실제 config.json 에 뭐가 들어 있는지 있는 그대로 보여주고
필요하면 정리한다. 웹 서버를 끄고 실행하는 것을 권장한다.

사용법:
    python diag_servers.py                 # 현황만 출력 (변경 없음)
    python diag_servers.py --dedupe        # 중복 항목 병합
    python diag_servers.py --drop-empty    # 로그 경로도 접속정보도 없는 껍데기 삭제
"""
import os
import sys
import json
import argparse
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def _hr(t=""):
    print("\n" + "=" * 72)
    if t:
        print(f" {t}")
        print("=" * 72)


def _ident(s):
    """중복 판정 키 — 값이 있는 것만. 비어 있는 필드는 키가 되지 않는다."""
    keys = []
    for field in ("ip", "hostname", "label"):
        v = (s.get(field) or "").strip()
        if v:
            keys.append((field, v))
    return keys


def _completeness(s):
    """더 완전한 항목을 고르기 위한 점수 (병합 시 기준)."""
    from config_manager import normalize_log_paths
    lp = normalize_log_paths(s.get("log_paths"))
    score = len(lp.get("inbound", [])) * 10 + len(lp.get("outbound", [])) * 10
    for field in ("env", "label", "hostname", "ip", "user", "ssh_key_path"):
        if (s.get(field) or "").strip():
            score += 1
    return score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dedupe", action="store_true",
                    help="같은 IP/호스트/라벨을 가진 항목을 하나로 병합")
    ap.add_argument("--drop-empty", action="store_true",
                    help="로그 경로가 하나도 없는 항목 삭제 "
                         "(예전 키 등록이 만들어 둔 항목 — 서버 추가를 '중복'으로 막는 원인)")
    args = ap.parse_args()

    from config_manager import (load_config, save_config, normalize_log_paths,
                                get_server_label)

    cfg_path = os.path.join(BASE, "config.json")
    print(f"config.json : {cfg_path}")
    print(f"              {'있음' if os.path.exists(cfg_path) else '★ 없음 ★'}")
    if os.path.exists(cfg_path):
        print(f"              {os.path.getsize(cfg_path):,}바이트 · "
              f"수정 {__import__('datetime').datetime.fromtimestamp(os.path.getmtime(cfg_path)):%Y-%m-%d %H:%M:%S}")

    # 백업/손상본 현황 — '설정 로드 실패'가 났던 적이 있는지 바로 보인다
    from pathlib import Path
    backups = sorted(Path(BASE).glob("config.json.backup_*"), reverse=True)
    corrupts = sorted(Path(BASE).glob("config.json.corrupt_*"), reverse=True)
    print(f"백업        : {len(backups)}벌"
          + (f"  (최신 {backups[0].name})" if backups else "  ★ 없음"))
    if corrupts:
        print(f"손상 이력   : ★ {len(corrupts)}건 — {', '.join(c.name for c in corrupts[:3])}")
        print("              (동시 쓰기로 깨졌던 흔적입니다. 백업에서 자동 복구됩니다)")

    config = load_config()
    if not config:
        print("\n★ 설정을 읽지 못했고 쓸 수 있는 백업도 없습니다.")
        print("  config.json 을 직접 열어 JSON 문법을 확인하거나,")
        print("  config.json.corrupt_* / config.json.backup_* 중 정상인 것을")
        print("  config.json 으로 복사하세요.")
        return
    servers = config.get("remote_servers", [])

    # ── 1) 전체 목록 ───────────────────────────────────────
    _hr(f"1. 등록된 서버 {len(servers)}대")
    for i, s in enumerate(servers):
        lp = normalize_log_paths(s.get("log_paths"))
        am = s.get("access_method") or "unc"
        need_conn = (s.get("type") == "AICC") or am == "ssh"
        print(f"\n  [{i}] {get_server_label(s)}   {s.get('type', 'AICC')} / {am.upper()}"
              f"   {s.get('env') or '운영구분 미지정'}"
              f"   {'사용' if s.get('enabled', True) else '★ 비활성 ★'}")
        print(f"      label={s.get('label') or '-'}  hostname={s.get('hostname') or '-'}"
              f"  ip={s.get('ip') or '-'}")
        if need_conn:
            key = s.get("ssh_key_path")
            key_state = "-"
            if key:
                key_state = f"{key}  {'(파일 있음)' if os.path.exists(key) else '★ 파일 없음 ★'}"
            print(f"      user={s.get('user') or '-'}  port={s.get('ssh_port') or 22}")
            print(f"      key ={key_state}")
        print(f"      로그 경로: 인바운드 {len(lp['inbound'])} · 아웃바운드 {len(lp['outbound'])}")
        for p in lp["inbound"]:
            print(f"        IN  {p}")
        for p in lp["outbound"]:
            print(f"        OUT {p}")

        problems = []
        if need_conn and not (s.get("hostname") or s.get("ip")):
            problems.append("접속 대상(hostname/ip)이 없음 → SSH 연결 불가")
        if need_conn and not s.get("ssh_key_path"):
            problems.append("ssh_key_path 없음 → 키 인증 불가")
        if s.get("type") == "ARS" and not lp["inbound"]:
            problems.append("인바운드 경로 없음 → 색인 대상에서 제외됨")
        for p in problems:
            print(f"      ★ {p}")

    # ── 2) 중복 ────────────────────────────────────────────
    _hr("2. 중복 판정 (서버 추가가 막히는 원인)")
    groups = defaultdict(list)
    for i, s in enumerate(servers):
        for field, v in _ident(s):
            groups[(field, v)].append(i)
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dups:
        print("  항목끼리 겹치는 값은 없습니다.")
    for (field, v), idxs in sorted(dups.items()):
        names = ", ".join(f"[{i}] {get_server_label(servers[i])}" for i in idxs)
        print(f"  ★ {field} = {v}  →  {names}")

    # ★ 여기가 핵심 ★
    # 위는 '이미 등록된 것끼리' 겹치는지만 본다. 정작 사용자가 겪는 건
    # '추가하려는 값이 기존 항목과 겹쳐서 막히는' 상황이다. 항목이 하나만
    # 있으면 위에선 중복이 아니지만, 같은 값으로 추가하면 막힌다.
    print("\n  서버를 '추가'할 때 막히는 값 (이 값으로는 새로 추가할 수 없습니다):")
    blockers = sorted(groups.keys())
    if not blockers:
        print("    (없음 — 등록된 서버가 없습니다)")
    for field, v in blockers:
        owners = ", ".join(f"[{i}] {get_server_label(servers[i])}" for i in groups[(field, v)])
        print(f"    {field:<9} {v:<22} ← {owners}")
    print("\n    최신 코드에서는 막히지 않고 그 항목이 '갱신'됩니다.")
    print("    '중복'이라는 오류가 뜬다면 예전 routes/servers.py 가 돌고 있는 것입니다")
    print("    → 브라우저에서 /version 을 열어 확인하세요.")

    # ── 3) 껍데기 ──────────────────────────────────────────
    # 로그 경로가 하나도 없으면 검색에도 색인에도 쓰이지 않는다. 그런데
    # 서버 추가 때는 IP/호스트명이 겹친다고 막아서는 원인이 된다.
    # (예전 키 등록이 만들어 둔 항목이 여기 해당한다 — 키는 있고 경로는 없다)
    empties = []
    for i, s in enumerate(servers):
        lp = normalize_log_paths(s.get("log_paths"))
        if not lp["inbound"] and not lp["outbound"]:
            empties.append(i)
    _hr("3. 로그 경로가 없는 항목")
    if not empties:
        print("  없음")
    else:
        print("  아래 항목은 로그 경로가 없어 검색·색인에 쓰이지 않습니다.")
        print("  그런데 같은 IP/호스트명으로 서버를 '추가'하면 이 항목과 겹쳐")
        print("  '중복'으로 막힙니다 — 2번에 중복이 안 떠도 그렇습니다.")
        print("  (예전 버전의 SSH 키 등록이 자동으로 만들어 둔 항목입니다)\n")
        for i in empties:
            s = servers[i]
            key = "키 있음" if s.get("ssh_key_path") else "키 없음"
            print(f"  ★ [{i}] {get_server_label(s)}   "
                  f"ip={s.get('ip') or '-'}  hostname={s.get('hostname') or '-'}  {key}")

    # ── 4) 정리 ────────────────────────────────────────────
    if not (args.dedupe or args.drop_empty):
        if dups or empties:
            _hr("정리하려면")
            if dups:
                print("  python diag_servers.py --dedupe       # 중복 병합")
            if empties:
                print("  python diag_servers.py --drop-empty   # 껍데기 삭제")
        return

    _hr("4. 정리 실행")
    keep = list(range(len(servers)))

    if args.dedupe:
        # 같은 식별자를 공유하는 항목끼리 묶어, 가장 완전한 하나만 남기고
        # 나머지의 로그 경로는 합친다.
        merged_into = {}
        for (field, v), idxs in sorted(dups.items()):
            alive = [i for i in idxs if i in keep]
            if len(alive) < 2:
                continue
            best = max(alive, key=lambda i: _completeness(servers[i]))
            base = normalize_log_paths(servers[best].get("log_paths"))
            for i in alive:
                if i == best:
                    continue
                lp = normalize_log_paths(servers[i].get("log_paths"))
                for purpose in ("inbound", "outbound"):
                    for p in lp[purpose]:
                        if p not in base[purpose]:
                            base[purpose].append(p)
                # 남는 쪽에 없는 접속정보는 채워 넣는다
                for field2 in ("env", "label", "hostname", "ip", "user",
                               "ssh_port", "ssh_key_path"):
                    if not servers[best].get(field2) and servers[i].get(field2):
                        servers[best][field2] = servers[i][field2]
                keep.remove(i)
                merged_into[i] = best
            servers[best]["log_paths"] = base
            print(f"  병합: {field}={v} → [{best}] {get_server_label(servers[best])} 로 통합")
        if not merged_into:
            print("  병합할 중복 없음")

    if args.drop_empty:
        dropped = 0
        for i in empties:
            if i in keep:
                print(f"  삭제: [{i}] {get_server_label(servers[i])}")
                keep.remove(i)
                dropped += 1
        if not dropped:
            print("  삭제할 껍데기 없음")

    new_servers = [servers[i] for i in sorted(keep)]
    if len(new_servers) == len(servers):
        print("\n  변경 사항 없음 — 저장하지 않습니다")
        return

    config["remote_servers"] = new_servers
    if save_config(config):
        print(f"\n  저장 완료: {len(servers)}대 → {len(new_servers)}대")
        print("  (직전 config.json 은 config.json.backup_* 로 백업돼 있습니다)")
        print("  ※ 서버 번호가 바뀌었으므로 웹 화면을 새로고침하세요")
    else:
        print("\n  ★ 저장 실패 — 쓰기 권한을 확인하세요")


if __name__ == "__main__":
    main()
