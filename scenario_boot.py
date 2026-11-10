# -*- coding: utf-8 -*-
"""
scenario_boot.py — scenario_store 속도 개선 부트 모듈 (비침투)
topology 뷰어 첫 로딩 1~2분 문제 해결. scenario_store.py 무수정.
  ① 디스크 캐시(pickle, 폴더 mtime 서명) → 재시작해도 재빌드 안 함
  ② 백그라운드 워밍 → 첫 사용자 대기 제거
사용(app.py 맨 아래, app.run 전):
  import scenario_boot; scenario_boot.install(); scenario_boot.warm_async()
캐시 위치: 환경변수 SCENARIO_CACHE_DIR, 없으면 <최상위>/.scenario_cache_boot
"""
import os
import glob
import time
import pickle
import logging
import hashlib
import threading
import functools

import scenario_store as S

logger = logging.getLogger(__name__)

_CACHE_DIR = os.environ.get(
    "SCENARIO_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scenario_cache_boot"))

_installed = False
_lock = threading.Lock()

# 같은 것을 두 번 만들지 않기 위한 작업별 잠금.
# 백그라운드 워밍과 사용자의 요청이 동시에 같은 트리를 만들면 시간이 두 배로
# 든다. 먼저 시작한 쪽이 끝날 때까지 나머지는 기다렸다가 캐시를 읽는다.
_keylocks = {}
_keylock_guard = threading.Lock()

# 준비 상태 — 화면에서 "구성도 준비 중 n/m" 을 보여 주기 위한 값
STATUS = {"state": "idle", "env": "", "entry": "",
          "done": 0, "total": 0, "started": 0.0, "elapsed": 0.0}


def _keylock(key):
    with _keylock_guard:
        lk = _keylocks.get(key)
        if lk is None:
            lk = _keylocks[key] = threading.Lock()
        return lk


def status():
    """현재 준비 상태 (얕은 복사)."""
    st = dict(STATUS)
    if st["state"] == "warming" and st["started"]:
        st["elapsed"] = round(time.time() - st["started"], 1)
    return st


def _folder_sig(env):
    try:
        files = S._scan_files(env)
        return hashlib.md5(repr(S._signature(files)).encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "nosig"


def _disk_cache(fn, tag):
    @functools.wraps(fn)
    def wrapper(env, entry=None, *args, **kwargs):
        try:
            sig = _folder_sig(env)
            key_src = f"{tag}|{env}|{entry}|{args}|{sorted(kwargs.items())}|{sig}"
            h = hashlib.md5(key_src.encode("utf-8")).hexdigest()
            dp = os.path.join(_CACHE_DIR, f"{tag}_{h}.pkl")
            if os.path.isfile(dp):
                try:
                    return pickle.load(open(dp, "rb"))
                except Exception:
                    pass
            # 같은 작업을 동시에 두 번 만들지 않는다
            with _keylock(dp):
                if os.path.isfile(dp):
                    try:
                        return pickle.load(open(dp, "rb"))
                    except Exception:
                        pass
                t0 = time.time()
                result = fn(env, entry, *args, **kwargs)
                took = time.time() - t0
                if took > 3:
                    logger.info("구성도 생성 %s [%s] %s — %.1f초",
                                tag, env, os.path.basename(str(entry) or ""), took)
                if not (isinstance(result, dict) and result.get("error")):
                    try:
                        os.makedirs(_CACHE_DIR, exist_ok=True)
                        pickle.dump(result, open(dp, "wb"))
                    except Exception:
                        pass
                return result
        except Exception:
            return fn(env, entry, *args, **kwargs)
    return wrapper


_HEAVY = ["get_tree_doc", "build_locator", "get_bizflow",
          "get_coreflow", "get_menu_summary", "get_detail"]


def install():
    global _installed
    with _lock:
        if _installed:
            return
        for name in _HEAVY:
            fn = getattr(S, name, None)
            if callable(fn):
                setattr(S, name, _disk_cache(fn, name))
        _installed = True
    return True


def _prune_stale():
    """지금 서명과 무관한 옛 캐시 파일 정리 (시나리오를 덮어쓰면 계속 쌓인다)."""
    try:
        sigs = {_folder_sig(e) for e in _safe_envs()}
    except Exception:
        return
    keep = 0
    for f in glob.glob(os.path.join(_CACHE_DIR, "*.pkl")):
        try:
            if os.path.getmtime(f) < time.time() - 14 * 86400:
                os.unlink(f)
            else:
                keep += 1
        except OSError:
            pass


def _warm_env(env):
    try:
        roots = S.get_menu_roots(env).get("roots", [])
    except Exception:
        roots = []
    jobs = (("get_tree_doc", {}),
            ("build_locator", {}),
            ("get_bizflow", {"mode": "summary"}),
            ("get_bizflow", {"mode": "detail"}))
    STATUS.update({"state": "warming", "env": env, "entry": "",
                   "done": 0, "total": len(roots) * len(jobs)})
    logger.info("구성도 준비 시작 — [%s] 진입점 %d개", env, len(roots))
    for i, entry in enumerate(roots, 1):
        STATUS["entry"] = os.path.basename(str(entry))
        for fn_name, kw in jobs:
            fn = getattr(S, fn_name, None)
            if callable(fn):
                try:
                    fn(env, entry, **kw)
                except Exception as e:
                    logger.debug("구성도 준비 건너뜀 %s/%s: %s", entry, fn_name, e)
            STATUS["done"] += 1
        if i % 5 == 0 or i == len(roots):
            logger.info("구성도 준비 %d/%d — [%s]", i, len(roots), env)


def warm(pages_by_env=None):
    if not _installed:
        install()
    t0 = time.time()
    STATUS.update({"state": "warming", "started": t0, "done": 0, "total": 0})
    _prune_stale()
    envs = list(pages_by_env.keys()) if pages_by_env else _safe_envs()
    for env in envs:
        if pages_by_env:
            for entry in pages_by_env[env]:
                for fn_name, kw in (("get_tree_doc", {}), ("build_locator", {}),
                                    ("get_bizflow", {"mode": "summary"})):
                    fn = getattr(S, fn_name, None)
                    if callable(fn):
                        try:
                            fn(env, entry, **kw)
                        except Exception:
                            pass
        else:
            _warm_env(env)
    STATUS.update({"state": "done", "entry": "",
                   "elapsed": round(time.time() - t0, 1)})
    logger.info("구성도 준비 완료 — %.1f초 (환경 %d개)",
                time.time() - t0, len(envs))


def _safe_envs():
    try:
        return S.list_envs()
    except Exception:
        return []


def warm_async(pages_by_env=None):
    if not _installed:
        install()
    t = threading.Thread(target=warm, args=(pages_by_env,), daemon=True)
    t.start()
    return t


def clear_disk_cache():
    import shutil
    try:
        shutil.rmtree(_CACHE_DIR)
    except Exception:
        pass