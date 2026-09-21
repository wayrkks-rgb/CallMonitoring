# -*- coding: utf-8 -*-
"""
list_routes.py — 이 폴더의 app.py 가 실제로 만들어 내는 주소 목록

'/version 이 not found' 처럼 주소가 없을 때, 원인은 딱 셋 중 하나다.
  (1) 파일이 예전 것이다            → 주소가 목록에 없다
  (2) 파일은 최신인데 재기동을 안 했다 → 주소가 목록에 있다
  (3) 블루프린트 import 가 실패해서 통째로 빠졌다 → 아래 '등록 실패'에 뜬다

이 스크립트는 웹 서버를 띄우지 않고 app.py 를 그대로 불러와 주소표를
찍는다. psutil 같은 부가 패키지가 없어도 돌아간다.

사용법 (app.py 와 같은 폴더에서):
    python list_routes.py
    python list_routes.py version      # 특정 주소만 찾기
"""
import os
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def main():
    needle = (sys.argv[1] if len(sys.argv) > 1 else '').lower()

    print(f"검사 폴더 : {BASE}")
    print(f"파이썬    : {sys.executable}")
    print(f"버전      : {sys.version.split()[0]}\n")

    # ── 블루프린트가 하나씩 정상 import 되는지 먼저 확인 ──
    # app.py 는 import 를 감싸지 않으므로, 하나라도 실패하면 앱 자체가
    # 안 뜬다. 어디서 막히는지 이름을 집어 준다.
    print("블루프린트 import 점검")
    mods = [
        ('routes.search', 'search_bp'),
        ('routes.servers', 'servers_bp'),
        ('routes.system', 'system_bp'),
        ('routes.analysis', 'analysis_bp'),
        ('routes.monitor', 'monitor_bp'),
        ('routes.topology', 'topology_bp'),
        ('routes.precheck', 'precheck_bp'),
        ('routes.topology_screen', 'topology_screen_bp'),
        ('scenario_deploy_routes', 'deploy_bp'),
    ]
    failed = []
    for mod_name, bp_name in mods:
        try:
            mod = __import__(mod_name, fromlist=[bp_name])
            getattr(mod, bp_name)
            print(f"  OK    {mod_name}")
        except Exception as e:
            failed.append((mod_name, e))
            print(f"  ★실패 {mod_name}  —  {type(e).__name__}: {e}")

    if failed:
        print("\n  ★ 위 모듈이 import 되지 않습니다.")
        print("    app.py 는 이 import 를 감싸지 않으므로, 이 상태면 웹 서버가")
        print("    아예 뜨지 않거나 해당 주소들이 통째로 사라집니다.")
        print("    보통은 필요한 패키지가 없어서입니다 (예: pip install psutil)")

    # ── 실제 주소표 ──
    print("\n주소표 (app.py 를 불러와 확인)")
    try:
        import app as app_module
        rules = sorted(app_module.app.url_map.iter_rules(), key=lambda r: str(r))
    except Exception as e:
        print(f"  ★ app.py 를 불러오지 못했습니다: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 2

    shown = 0
    for r in rules:
        line = str(r)
        if needle and needle not in line.lower():
            continue
        methods = ','.join(sorted(m for m in r.methods if m not in ('HEAD', 'OPTIONS')))
        print(f"  {line:<45} [{methods}]  → {r.endpoint}")
        shown += 1

    if needle:
        print()
        if shown:
            print(f"  ▶ '{needle}' 주소가 '있습니다'.")
            print("    그런데 브라우저에서 not found 라면, 지금 돌고 있는 서버가")
            print("    이 파일들로 다시 시작되지 않은 것입니다 → 웹 서버를 완전히")
            print("    종료했다가 다시 실행하세요.")
        else:
            print(f"  ▶ '{needle}' 주소가 '없습니다'.")
            print("    이 폴더의 파일이 예전 것이거나, 위 import 점검에서")
            print("    실패한 모듈에 그 주소가 들어 있습니다.")
    else:
        print(f"\n  총 {shown}개 주소")

    return 0


if __name__ == '__main__':
    sys.exit(main())
