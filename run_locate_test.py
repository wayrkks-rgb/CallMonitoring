# -*- coding: utf-8 -*-
"""
run_locate_test.py — 사내에서 바로 실행하는 진단 + 폴백 검증 스크립트.

문제: locate_blocks 가 세부 블록(예 00000160)을 found:false 로 반환.
원인: 업무 요약 트리(scenario_flow.build_core_flow)가 세부/예외 블록을 제외.
해결: block_index(전체 블록 인덱스)로 폴백.

사용법(사내, scenario_store.py 와 같은 폴더에서):
    python run_locate_test.py                       # 기본: 운영/00000160
    python run_locate_test.py 운영 00000160 고객인증_SMS인증.dxml
    python run_locate_test.py 운영 00000160 고객인증_SMS인증.dxml  /실제/시나리오/운영폴더

필요 파일: scenario_store.py (기존), block_index.py (이번 제공), phase.py(선택)
"""
import sys, os, json


def main():
    env = sys.argv[1] if len(sys.argv) > 1 else "운영"
    seq = sys.argv[2] if len(sys.argv) > 2 else "00000160"
    page = sys.argv[3] if len(sys.argv) > 3 else "고객인증_SMS인증.dxml"
    folder = sys.argv[4] if len(sys.argv) > 4 else None  # 시나리오 폴더 직접 지정(선택)

    print("=" * 70)
    print(f" 진단 대상: env={env}  seq={seq}  page={page}")
    print("=" * 70)

    import scenario_store as store

    # 시나리오 폴더 추정 (folder 미지정 시 CACHE_ROOT/env)
    if folder is None:
        base = getattr(store, "CACHE_ROOT", None)
        folder = os.path.join(base, env) if base else None
    print(f"[경로] 시나리오 폴더 = {folder}")
    print(f"[경로] 폴더 존재     = {os.path.isdir(folder) if folder else False}")

    # ── 진단1: 기존 locate_blocks (page 有/無) ──────────────────────
    print("\n── [기존] locate_blocks ──────────────────────────────")
    r1 = store.locate_blocks(env, [seq], page=page)["results"][0]
    print(f"  page={page:<28} found={r1['found']}  matches={len(r1['matches'])}")
    r2 = store.locate_blocks(env, [seq])["results"][0]  # page 없이
    print(f"  page=(없음)                       found={r2['found']}  matches={len(r2['matches'])}")
    if r2["matches"]:
        print("  → 저장된 실제 page 값:", [m.get("page") for m in r2["matches"]][:5])

    # ── 진단2: 전체 블록 인덱스 폴백 ────────────────────────────────
    print("\n── [폴백] block_index (전체 블록) ───────────────────")
    try:
        import block_index as BI
    except Exception as e:
        print("  block_index import 실패:", e)
        return
    if not (folder and os.path.isdir(folder)):
        print("  시나리오 폴더를 못 찾아 폴백 생략. 4번째 인자로 폴더 지정하세요.")
        return
    idx = BI.get_index(folder)
    print(f"  인덱싱된 블록ID 종수 = {len(idx)}")
    rf = BI.locate(seq, page=page, index=idx)
    print(f"  found={rf['found']}  matches={len(rf['matches'])}")
    if rf["matches"]:
        m = rf["matches"][0]
        print("  → 업무위치:",
              " › ".join(str(x) for x in [m.get("page"), m.get("step_title"),
                                          m.get("substep")] if x))
        print(json.dumps(m, ensure_ascii=False, indent=2))

    # ── 결론 ────────────────────────────────────────────────────────
    print("\n── 결론 ─────────────────────────────────────────────")
    if r1["found"]:
        print("  기존 locate_blocks 로 이미 해결됨(요약 트리에 존재).")
    elif rf.get("found"):
        print("  ✅ 폴백(block_index)으로 해결. 어댑터에 폴백을 연결하면 로그 전 블록 커버.")
    else:
        print("  ❌ 폴백도 실패 → 경로/파일 확인 필요(폴더에 해당 .xml 존재?).")


if __name__ == "__main__":
    main()
