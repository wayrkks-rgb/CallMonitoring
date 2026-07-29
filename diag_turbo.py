# -*- coding: utf-8 -*-
"""
diag_turbo.py — 가속·최신화 모듈 적용 전 자가 진단

확인 항목
  1. scenario_boot._HEAVY 함수들이 (env, entry) 형태인지        ← 요청하신 확인 사항
  2. turbo 메모이즈가 실제로 재바인딩됐는지
  3. 엔트리 단위 서명이 동작하는지 (도달 파일 수)
  4. 파싱 캐시 히트율 / 속도 개선폭
  5. 원격 접속 · 매니페스트 · tar 사용 가능 여부

사용:
    python diag_turbo.py                 # 로컬 항목만
    python diag_turbo.py --env 운영      # 엔트리 서명·워밍까지
    python diag_turbo.py --remote        # 원격 접속까지 점검
"""
import os
import sys
import time
import inspect
import argparse

OK, NG, WARN = "  [OK]  ", "  [실패]", "  [주의]"


def hr(t):
    print("\n" + "=" * 64)
    print(" " + t)
    print("=" * 64)


# ── 1. _HEAVY 시그니처 ──────────────────────────────────────
def check_signatures():
    hr("1. scenario_boot._HEAVY 함수 시그니처")
    try:
        import scenario_store as S
        import scenario_boot
    except Exception as e:
        print(NG, "모듈 임포트 실패:", e)
        return False
    allok = True
    for name in scenario_boot._HEAVY:
        fn = getattr(S, name, None)
        if not callable(fn):
            print(WARN, f"{name:20} 없음 (스킵)")
            continue
        base = getattr(fn, "__wrapped__", fn)
        base = getattr(base, "__wrapped_original__", base)
        try:
            sig = inspect.signature(base)
        except (TypeError, ValueError):
            print(WARN, f"{name:20} 시그니처 확인 불가")
            continue
        params = list(sig.parameters)
        pos = [p for p, v in sig.parameters.items()
               if v.kind in (v.POSITIONAL_ONLY, v.POSITIONAL_OR_KEYWORD)]
        good = len(pos) >= 2 and pos[0] == "env" and pos[1] == "entry"
        single = len(pos) == 1 and pos[0] == "env"
        if good:
            print(OK, f"{name:20} ({', '.join(params)})")
        elif single:
            print(WARN, f"{name:20} ({', '.join(params)})  ← entry 없음. "
                        "turbo 는 폴더 전체 서명으로 자동 폴백 (동작엔 문제 없음)")
        else:
            print(NG, f"{name:20} ({', '.join(params)})  ← 예상과 다름. 알려주세요")
            allok = False
    return allok


# ── 2. turbo 재바인딩 ───────────────────────────────────────
def check_turbo():
    hr("2. turbo 메모이즈 재바인딩")
    try:
        import scenario_turbo as T
        import scenario_parser
        import scenario_flow
    except Exception as e:
        print(NG, "임포트 실패:", e)
        return False
    T.install()
    p_ok = hasattr(scenario_parser.parse_page, "__wrapped_original__")
    c_ok = hasattr(scenario_flow.build_core_flow, "__wrapped_original__")
    print(OK if p_ok else NG, "scenario_parser.parse_page   메모이즈", p_ok)
    print(OK if c_ok else NG, "scenario_flow.build_core_flow 메모이즈", c_ok)
    # from-import 캡처본까지 바뀌었는지
    leaked = []
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        f = getattr(mod, "parse_page", None)
        if callable(f) and not hasattr(f, "__wrapped_original__"):
            leaked.append(getattr(mod, "__name__", "?"))
    if leaked:
        print(WARN, "원본 참조가 남은 모듈:", ", ".join(leaked))
    else:
        print(OK, "모든 모듈이 메모이즈 버전을 참조")
    return p_ok and c_ok


# ── 3. 속도 개선폭 ──────────────────────────────────────────
def check_speed(folder):
    hr("3. 파싱 캐시 속도 (폴더: %s)" % folder)
    import glob
    files = sorted(glob.glob(os.path.join(folder, "*.dxml")) +
                   glob.glob(os.path.join(folder, "*.xml")))
    if not files:
        print(WARN, "시나리오 파일 없음 — 스킵")
        return
    mb = sum(os.path.getsize(f) for f in files) / 1048576
    import scenario_turbo as T
    import scenario_flow
    T.install()
    T._mem.clear()
    T.STATS.update(hit_mem=0, hit_disk=0, miss=0)
    t = time.time()
    for f in files:
        scenario_flow.build_core_flow(f)
    cold = time.time() - t
    t = time.time()
    for _ in range(3):
        for f in files:
            scenario_flow.build_core_flow(f)
    warm = (time.time() - t) / 3
    print(f"       파일 {len(files)}개 / {mb:.1f} MB")
    print(f"       최초 1회전 : {cold:.2f}초")
    print(f"       캐시 후    : {warm:.3f}초  (x{cold/max(warm,1e-6):.0f})")
    print(f"       캐시 통계  : {T.stats()}")


# ── 4. 엔트리 단위 서명 ─────────────────────────────────────
def check_entry_scope(env):
    hr("4. 엔트리 단위 캐시 범위 (env=%s)" % env)
    try:
        import scenario_store as S
        import scenario_turbo as T
    except Exception as e:
        print(NG, "임포트 실패:", e)
        return
    try:
        roots = S.get_menu_roots(env).get("roots", [])
    except Exception as e:
        print(NG, "메뉴 루트 조회 실패:", e)
        return
    if not roots:
        print(WARN, "메뉴 루트 없음")
        return
    total = len(S._get_env(env)["files"])
    print(f"       전체 시나리오 {total}개")
    for r in roots[:8]:
        try:
            reach = T.reachable_files(env, r)
            sig = T.entry_signature(env, r)
            pct = len(reach) / total * 100 if total else 0
            print(OK, f"{r:32} 도달 {len(reach):4}개 ({pct:4.1f}%)  sig={sig}")
        except Exception as e:
            print(NG, f"{r:32} {e}")
    print("\n       → 배포로 파일 1개가 바뀌면, 그 파일에 도달하는 엔트리만 재빌드됩니다.")


# ── 5. 원격 점검 ────────────────────────────────────────────
def check_remote():
    hr("5. 원격 서버 점검")
    try:
        import scenario_deploy as DEP
    except Exception as e:
        print(NG, "임포트 실패:", e)
        return
    cfg = DEP.load_cfg()
    if not cfg.get("ssh"):
        print(NG, "config.json 의 scenario_deploy.ssh 미설정")
        return
    alias = cfg["ssh"]
    r = DEP._ps(alias, "(Get-Command tar -ErrorAction SilentlyContinue).Source", timeout=30)
    tar = (r.stdout or "").strip()
    print(OK if tar else NG, "tar.exe :", tar or "없음 (Server 2019 기본 포함이어야 함)")
    r = DEP._ps(alias, "$PSVersionTable.PSVersion.ToString()", timeout=30)
    print(OK, "PowerShell:", (r.stdout or "").strip() or "?")

    for slot in ("old", "new"):
        d = DEP._remote_dir(cfg, slot)
        t = time.time()
        info = DEP.remote_manifest(alias, d, verify=cfg.get("verify", "hash"))
        el = time.time() - t
        nm = DEP.SLOT_NAME[slot]
        if not info.get("ok"):
            print(NG, f"{nm}: {info.get('error')}")
            continue
        has_hash = any(len(v) >= 4 for v in info["manifest"].values())
        print(OK, f"{nm}: {info['files']}개 / {info['bytes']/1048576:.0f} MB / "
                  f"{el:.1f}초 / 해시 {'포함' if has_hash else '없음'}")
        print(f"       {d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env")
    ap.add_argument("--folder")
    ap.add_argument("--remote", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    check_signatures()
    check_turbo()

    folder = a.folder
    if not folder and a.env:
        try:
            import scenario_store as S
            folder = os.path.join(S.CACHE_ROOT, a.env)
        except Exception:
            folder = None
    if folder and os.path.isdir(folder):
        check_speed(folder)
    if a.env:
        check_entry_scope(a.env)
    if a.remote:
        check_remote()
    print("\n진단 완료.\n")


if __name__ == "__main__":
    main()
