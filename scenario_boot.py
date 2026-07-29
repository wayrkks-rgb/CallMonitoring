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
import pickle
import hashlib
import threading
import functools

import scenario_store as S

_CACHE_DIR = os.environ.get(
    "SCENARIO_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scenario_cache_boot"))

_installed = False
_lock = threading.Lock()


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
            result = fn(env, entry, *args, **kwargs)
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


def _warm_env(env):
    try:
        roots = S.get_menu_roots(env).get("roots", [])
    except Exception:
        roots = []
    for entry in roots:
        for fn_name, kw in (("get_tree_doc", {}),
                            ("build_locator", {}),
                            ("get_bizflow", {"mode": "summary"}),
                            ("get_bizflow", {"mode": "detail"})):
            fn = getattr(S, fn_name, None)
            if not callable(fn):
                continue
            try:
                fn(env, entry, **kw)
            except Exception:
                pass


def warm(pages_by_env=None):
    if not _installed:
        install()
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