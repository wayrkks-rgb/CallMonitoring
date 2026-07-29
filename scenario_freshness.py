# -*- coding: utf-8 -*-
"""
scenario_freshness.py — 업무 FLOW 뷰어 자동 최신화

뷰어가 로컬 캐시만 읽으면 배포 후 화면이 옛 시나리오를 보여준다.
그렇다고 요청마다 원격을 보면 뷰어를 못 쓴다(요청 1건이 파일 수십 개를 읽음).

절충: **원격 확인은 하되, 값싸게 그리고 드물게.**
    - 확인 비용: 매니페스트만 조회 → 전송 ~40KB / 1~3초
    - 빈도 제한: throttle 초 이내 재확인은 원격 접속 없이 통과
    - 변경 시에만: 델타 수집 → 뷰어 캐시 미러 → '바뀐 파일에 연결된' 캐시만 무효화

install_auto_refresh(app) 를 쓰면 /api/topology/* 요청 진입 시 자동 적용된다.
"""
import os
import time
import shutil
import threading
import logging

import scenario_deploy as DEP
import scenario_deploy_diff as DIFF

logger = logging.getLogger(__name__)

DEFAULT_THROTTLE = 60          # 초 — 이 시간 안의 재확인은 원격 접속 생략

_lock = threading.Lock()
_last = {"at": 0.0, "sig": None, "updated_at": None, "checking": False}


def _viewer_env():
    return DEP.load_cfg().get("viewer_env") or ""


def _invalidate(env, changed_files):
    """
    바뀐 파일에 '도달 가능한' 엔트리의 캐시만 버린다.
    scenario_turbo.scope_boot_cache() 가 적용돼 있으면 엔트리 서명이 자동으로
    달라지므로 별도 삭제 없이도 재빌드된다. 여기서는 메모리 캐시만 정리.
    """
    try:
        import scenario_store as S
        S._cache.pop(env, None)
        changed = {c.lower() for c in changed_files}
        for path in list(S._page_cache):
            if os.path.basename(path).lower() in changed:
                S._page_cache.pop(path, None)
    except Exception:
        logger.exception("캐시 무효화 실패")

    # turbo 미적용(폴더 전체 서명) 환경이면 디스크 캐시를 비워야 반영된다
    try:
        import scenario_turbo
        if getattr(scenario_turbo, "_boot_scoped", False):
            return
    except Exception:
        pass
    try:
        import scenario_boot
        scenario_boot.clear_disk_cache()
    except Exception:
        pass


def ensure_fresh(env=None, throttle=DEFAULT_THROTTLE, force=False):
    """
    뷰어 진입 시 호출. 원격이 바뀌었으면 로컬을 최신화한다.
    Returns: {checked, updated, changed_files, sec, reason}
    """
    cfg = DEP.load_cfg()
    env = env or cfg.get("viewer_env")
    if not cfg.get("ssh") or not env:
        return {"checked": False, "updated": False, "reason": "미설정"}

    now = time.time()
    with _lock:
        if _last["checking"]:
            return {"checked": False, "updated": False, "reason": "확인 중"}
        if not force and (now - _last["at"]) < throttle:
            return {"checked": False, "updated": False, "reason": "최근 확인됨",
                    "age": round(now - _last["at"]), "updated_at": _last["updated_at"]}
        _last["checking"] = True

    t0 = time.time()
    try:
        rdir = DEP._remote_dir(cfg, "new")
        info = DEP.remote_manifest(cfg["ssh"], rdir, verify=cfg.get("verify", "hash"))
        if not info.get("ok"):
            return {"checked": True, "updated": False, "error": info.get("error")}

        st = DEP.load_state()
        st.setdefault("slots", {})
        prev = (st["slots"].get("new") or {}).get("manifest") or {}
        man = info["manifest"]
        changed = [man[k][0] for k, v in man.items() if prev.get(k) != v]
        removed = [prev[k][0] for k in prev if k not in man]

        if not changed and not removed and prev:
            with _lock:
                _last["at"] = time.time()
                _last["sig"] = info["sig"]
            return {"checked": True, "updated": False, "sec": round(time.time() - t0, 1),
                    "reason": "변경 없음", "updated_at": _last["updated_at"]}

        # 수집 (전체/델타 판단은 _sync_slot 이 담당)
        r = DEP._sync_slot(cfg["ssh"], "new", rdir, st, verify=cfg.get("verify", "hash"))
        if not r.get("ok"):
            return {"checked": True, "updated": False, "error": r.get("error")}
        DEP._save_state(st)

        # 뷰어 캐시 미러 + 무효화
        DEP._mirror_viewer(cfg)
        _invalidate(env, changed + removed)

        with _lock:
            _last["at"] = time.time()
            _last["sig"] = info["sig"]
            _last["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # 백그라운드 재워밍 (사용자 대기 없이)
        try:
            import scenario_turbo
            import scenario_store as S
            scenario_turbo.precompile_async(os.path.join(S.CACHE_ROOT, env))
        except Exception:
            pass
        try:
            import scenario_boot
            scenario_boot.warm_async()
        except Exception:
            pass

        return {"checked": True, "updated": True, "mode": r["mode"],
                "changed_files": changed[:50], "changed_count": len(changed),
                "removed_count": len(removed),
                "sec": round(time.time() - t0, 1), "updated_at": _last["updated_at"]}
    except Exception as e:
        logger.exception("최신화 실패")
        return {"checked": True, "updated": False, "error": str(e)}
    finally:
        with _lock:
            _last["checking"] = False


def ensure_fresh_async(env=None, throttle=DEFAULT_THROTTLE):
    """비차단 최신화 — 화면은 즉시 뜨고, 갱신은 뒤에서 진행."""
    t = threading.Thread(target=ensure_fresh, args=(env, throttle), daemon=True)
    t.start()
    return {"checked": False, "updated": False, "reason": "백그라운드 확인 시작"}


def last_status():
    return {"last_check": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_last["at"]))
                           if _last["at"] else None),
            "last_update": _last["updated_at"], "sig": _last["sig"]}


def install_auto_refresh(app, throttle=DEFAULT_THROTTLE, blocking=False, prefix="/api/topology"):
    """
    Flask 앱에 자동 최신화 훅 설치.
      blocking=False (기본) : 화면은 즉시 응답, 갱신은 백그라운드 → 다음 조회부터 최신
      blocking=True         : 최신화를 기다린 뒤 응답 (항상 최신 보장, 첫 조회 1~3초 추가)
    """
    @app.before_request
    def _fresh_hook():
        from flask import request
        if not request.path.startswith(prefix):
            return None
        env = request.args.get("env") or _viewer_env()
        if not env:
            return None
        try:
            if blocking:
                ensure_fresh(env, throttle=throttle)
            else:
                if time.time() - _last["at"] >= throttle and not _last["checking"]:
                    ensure_fresh_async(env, throttle)
        except Exception:
            pass
        return None
    logger.info(f"scenario_freshness: 자동 최신화 훅 설치 ({prefix}, "
                f"{'동기' if blocking else '백그라운드'}, throttle={throttle}s)")
    return True
