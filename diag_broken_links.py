# -*- coding: utf-8 -*-
"""
diag_broken_links.py — 끊어진 이동 전수 점검

존재하지 않는 시나리오나 블록으로 가는 이동을 찾는다. 이런 이동이 운영에
나가면 통화가 그 지점에서 멈추거나 오류로 끝난다.

시나리오 DIFF 는 '이번 변경으로 새로 생긴 것' 만 경고한다. 이 스크립트는
폴더 전체를 한 번에 점검해서, 이미 운영 중인 시나리오에 숨어 있는 것까지
찾아낸다.

사용법:
    python diag_broken_links.py <시나리오폴더>
    python diag_broken_links.py <과거폴더> <운영폴더>     # 새로 생긴 것만 따로

주의:
    스크립트에서 계산되는 동적 이동(app.sNextPage 등)은 정적으로 알 수 없어
    점검 대상에서 빠진다. 공통 시나리오가 다른 폴더에 있으면 '파일 없음' 으로
    잡힐 수 있으니 목록을 보고 판단한다.
"""
import os
import sys

import scenario_deploy_diff as D


def _print(items, title):
    print(f"\n{title} — {len(items)}건")
    if not items:
        return
    by_kind = {"page": "파일 없음", "node": "블록 없음"}
    for x in items:
        tgt = x["target"] + (f" / {x['target_node']}" if x.get("target_node") else "")
        print(f"  [{by_kind.get(x['kind'], x['kind'])}] {x['file']}  {x['seq']}  "
              f"{x.get('label') or ''}  →  {tgt}")


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    exts = [".xml", ".dxml"]

    if len(argv) == 2:
        folder = argv[1]
        if not os.path.isdir(folder):
            print(f"폴더가 없습니다: {folder}")
            return 2
        snap = D.snapshot_folder(folder, exts)
        items = D.find_broken_refs(snap)
        blocks = sum(len(f["blocks"]) for f in snap.values())
        print(f"점검 대상 : {folder}")
        print(f"시나리오 {len(snap)}개 · 블록 {blocks}개")
        _print(items, "끊어진 이동")
        return 1 if items else 0

    old, new = argv[1], argv[2]
    r = D.diff_folders(old, new, exts=exts)
    b = r.get("broken", {})
    print(f"과거 : {old}\n운영 : {new}")
    _print(b.get("new", []), "이번 변경으로 새로 생긴 끊어진 이동 (배포 전 수정 필요)")
    _print(b.get("existing", []), "원래부터 있던 끊어진 이동")
    return 1 if b.get("new") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
