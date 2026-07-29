# -*- coding: utf-8 -*-
"""
scenario_turbo.py — 뷰어 최초/재조회 속도 가속기 (기존 모듈 무수정, 비침투)

문제
  ① 같은 페이지 반복 파싱
     build_core_flow() 는 호출마다 parse_page() 를 새로 부르고,
     BizFlow.build() 는 서브 시나리오로 재귀하며 이를 다시 부른다.
     공용 유틸 페이지(_std_*, mci_hostcomm 등)는 수십 회 재파싱된다.
  ② 캐시 무효화 범위가 과도
     scenario_boot._folder_sig 는 '폴더 전체' 서명이라,
     배포로 파일 1개만 바뀌어도 모든 엔트리의 무거운 캐시가 전부 날아간다.

해결
  ① parse_page / build_core_flow 를 전역 메모이즈(메모리+디스크).
     디스크 캐시는 '파일 내용' 기준이라 재수집(재전송) 후에도 그대로 재사용된다.
  ② scenario_boot 의 서명을 '엔트리에서 도달 가능한 파일들'로 축소.
     → W42 가 바뀌면 W42 를 참조하는 엔트리만 재빌드, 나머지는 캐시 유지.

사용 (app.py, scenario_boot.install() 보다 먼저):
    import scenario_turbo
    scenario_turbo.install()            # ① 메모이즈
    import scenario_boot
    scenario_boot.install()             # 기존 디스크 캐시
    scenario_turbo.scope_boot_cache()   # ② 엔트리 단위 무효화로 교체
    scenario_boot.warm_async()
"""
import os
import sys
import pickle
import hashlib
import threading
import functools
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARSE_CACHE_DIR = os.environ.get(
    "SCENARIO_PARSE_CACHE", os.path.join(BASE_DIR, ".parse_cache"))

_lock = threading.Lock()
_mem = {}                 # (fn, path, size, mtime) -> result
_installed = False
_boot_scoped = False
STATS = {"hit_mem": 0, "hit_disk": 0, "miss": 0}


def _stamp(path):
    st = os.stat(path)
    return (st.st_size, int(st.st_mtime))


def _disk_path(tag, path, stamp):
    key = hashlib.md5(f"{tag}|{os.path.basename(path).lower()}|{stamp[0]}|{stamp[1]}"
                      .encode("utf-8")).hexdigest()
    return os.path.join(PARSE_CACHE_DIR, f"{tag}_{key}.pkl")


def _memoize(fn, tag, use_disk=True):
    @functools.wraps(fn)
    def wrapper(path, *a, **kw):
        try:
            stamp = _stamp(path)
        except OSError:
            return fn(path, *a, **kw)
        key = (tag, os.path.abspath(path), stamp[0], stamp[1])
        with _lock:
            if key in _mem:
                STATS["hit_mem"] += 1
                return _mem[key]
        dp = _disk_path(tag, path, stamp) if use_disk else None
        if dp and os.path.isfile(dp):
            try:
                with open(dp, "rb") as f:
                    r = pickle.load(f)
                with _lock:
                    _mem[key] = r
                STATS["hit_disk"] += 1
                return r
            except Exception:
                pass
        r = fn(path, *a, **kw)
        STATS["miss"] += 1
        with _lock:
            _mem[key] = r
        if dp:
            try:
                os.makedirs(PARSE_CACHE_DIR, exist_ok=True)
                with open(dp, "wb") as f:
                    pickle.dump(r, f, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception:
                pass
        return r
    wrapper.__wrapped_original__ = fn
    return wrapper


def _rebind(orig, new, name):
    """`from x import f` 로 참조를 캡처한 모듈들까지 전부 교체."""
    n = 0
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            if getattr(mod, name, None) is orig:
                setattr(mod, name, new)
                n += 1
        except Exception:
            continue
    return n


def install():
    """parse_page / build_core_flow 전역 메모이즈."""
    global _installed
    with _lock:
        if _installed:
            return False
    import scenario_parser
    import scenario_flow

    orig_parse = getattr(scenario_parser.parse_page, "__wrapped_original__",
                         scenario_parser.parse_page)
    new_parse = _memoize(orig_parse, "parse")
    scenario_parser.parse_page = new_parse
    n1 = _rebind(orig_parse, new_parse, "parse_page")

    orig_cf = getattr(scenario_flow.build_core_flow, "__wrapped_original__",
                      scenario_flow.build_core_flow)
    new_cf = _memoize(orig_cf, "coreflow")
    scenario_flow.build_core_flow = new_cf
    n2 = _rebind(orig_cf, new_cf, "build_core_flow")

    with _lock:
        _installed = True
    logger.info(f"scenario_turbo: parse_page {n1}곳, build_core_flow {n2}곳 재바인딩")
    return True


# ══════════════════════════════════════════════════════════
# 엔트리 단위 캐시 무효화
# ══════════════════════════════════════════════════════════
def reachable_files(env, entry, max_files=400):
    """entry 에서 TargetPage 로 도달 가능한 시나리오 파일 목록(파일명)."""
    import scenario_store as S
    c = S._get_env(env)
    stem_map = c["stem_map"]

    def stem(n):
        return os.path.splitext(os.path.basename(n))[0].lower()

    seen, queue, out = set(), [stem(entry)], []
    folder = S._dir_of(c)
    while queue and len(out) < max_files:
        st = queue.pop()
        if st in seen or st not in stem_map:
            continue
        seen.add(st)
        fname = stem_map[st]
        out.append(fname)
        try:
            g = S._parse_cached(os.path.join(folder, fname))
        except Exception:
            continue
        for n in g["nodes"]:
            tp = n.get("target_page")
            if tp and stem(tp) not in seen:
                queue.append(stem(tp))
    return out


def entry_signature(env, entry):
    """엔트리에서 도달 가능한 파일들만의 서명."""
    import scenario_store as S
    try:
        c = S._get_env(env)
        folder = S._dir_of(c)
        parts = []
        for f in sorted(reachable_files(env, entry)):
            try:
                st = os.stat(os.path.join(folder, f))
                parts.append(f"{f}|{st.st_size}|{int(st.st_mtime)}")
            except OSError:
                continue
        return hashlib.md5("\n".join(parts).encode()).hexdigest()[:16]
    except Exception:
        return None


def scope_boot_cache():
    """
    scenario_boot 의 폴더 전체 서명 → 엔트리 도달범위 서명으로 교체.
    (scenario_boot.install() 이후에 호출)
    """
    try:
        import scenario_boot
    except Exception:
        return False
    orig = scenario_boot._folder_sig

    def scoped(env, entry=None):
        if entry:
            s = entry_signature(env, entry)
            if s:
                return "e:" + s
        return orig(env)

    # _disk_cache 래퍼가 _folder_sig(env) 로만 부르므로, entry 를 받도록 재작성
    def _scoped_disk_cache(fn, tag):
        @functools.wraps(fn)
        def wrapper(env, entry=None, *args, **kwargs):
            try:
                sig = scoped(env, entry)
                key_src = f"{tag}|{env}|{entry}|{args}|{sorted(kwargs.items())}|{sig}"
                h = hashlib.md5(key_src.encode("utf-8")).hexdigest()
                dp = os.path.join(scenario_boot._CACHE_DIR, f"{tag}_{h}.pkl")
                if os.path.isfile(dp):
                    try:
                        with open(dp, "rb") as f:
                            return pickle.load(f)
                    except Exception:
                        pass
                result = fn(env, entry, *args, **kwargs)
                if not (isinstance(result, dict) and result.get("error")):
                    try:
                        os.makedirs(scenario_boot._CACHE_DIR, exist_ok=True)
                        with open(dp, "wb") as f:
                            pickle.dump(result, f)
                    except Exception:
                        pass
                return result
            except Exception:
                return fn(env, entry, *args, **kwargs)
        return wrapper

    import scenario_store as S
    for name in scenario_boot._HEAVY:
        fn = getattr(S, name, None)
        if not callable(fn):
            continue
        base = getattr(fn, "__wrapped__", fn)      # boot 래퍼 벗기고 원본에 다시 감기
        setattr(S, name, _scoped_disk_cache(base, name))
    scenario_boot._folder_sig = scoped
    global _boot_scoped
    _boot_scoped = True
    logger.info("scenario_turbo: boot 캐시를 엔트리 단위로 축소")
    return True


def prune_parse_cache(keep_days=14):
    """오래된 파싱 캐시 정리."""
    import time
    if not os.path.isdir(PARSE_CACHE_DIR):
        return 0
    cut = time.time() - keep_days * 86400
    n = 0
    for f in os.listdir(PARSE_CACHE_DIR):
        p = os.path.join(PARSE_CACHE_DIR, f)
        try:
            if os.path.getmtime(p) < cut:
                os.unlink(p)
                n += 1
        except OSError:
            pass
    return n


# ══════════════════════════════════════════════════════════
# 병렬 선컴파일 (멀티코어 활용)
# ══════════════════════════════════════════════════════════
def _parse_worker(path):
    """워커 프로세스: 파싱 결과를 '디스크 캐시'에 적재한다."""
    try:
        install()
        import scenario_parser
        import scenario_flow
        scenario_parser.parse_page(path)
        scenario_flow.build_core_flow(path)
        return 1
    except Exception:
        return 0


def precompile(folder, workers=None, verbose=True):
    """
    폴더 내 모든 시나리오를 병렬 파싱해 디스크 캐시를 미리 채운다.
    이후 본 프로세스의 워밍/조회는 캐시 히트라 대폭 빨라진다.

    ※ Windows 는 프로세스 spawn 시 __main__ 을 재임포트하므로,
      Flask 앱 안에서 직접 부르지 말고 precompile_async() 로 별도 프로세스에서 실행할 것.
    """
    import glob
    import time as _t
    from concurrent.futures import ProcessPoolExecutor

    files = []
    for ext in (".dxml", ".xml"):
        files += glob.glob(os.path.join(folder, "*" + ext))
    files = sorted(set(files))
    if not files:
        return {"files": 0}
    w = workers or max(2, min(12, int((os.cpu_count() or 4) * 0.75)))
    t0 = _t.time()
    ok = 0
    try:
        with ProcessPoolExecutor(max_workers=w) as ex:
            for r in ex.map(_parse_worker, files, chunksize=4):
                ok += r
    except Exception:
        logger.exception("병렬 선컴파일 실패 — 직렬로 대체")
        for f in files:
            ok += _parse_worker(f)
    el = round(_t.time() - t0, 1)
    if verbose:
        logger.info(f"선컴파일 {ok}/{len(files)}개 · {el}초 · 워커 {w}")
    return {"files": len(files), "ok": ok, "sec": el, "workers": w}


def precompile_async(folder, workers=None):
    """별도 프로세스로 선컴파일 실행 (Flask 앱에서 안전하게 호출 가능)."""
    import subprocess
    args = [sys.executable, os.path.join(BASE_DIR, "scenario_turbo.py"),
            "--precompile", folder]
    if workers:
        args += ["--workers", str(workers)]
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x08000000   # DETACHED | NO_WINDOW
        subprocess.Popen(args, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, cwd=BASE_DIR, **kw)
        return True
    except Exception:
        logger.exception("선컴파일 프로세스 기동 실패")
        return False


def stats():
    tot = sum(STATS.values()) or 1
    return dict(STATS, hit_rate=round((STATS["hit_mem"] + STATS["hit_disk"]) / tot * 100, 1),
                mem_entries=len(_mem))


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--precompile", metavar="FOLDER")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--prune", type=int, metavar="DAYS")
    a = ap.parse_args()
    if a.precompile:
        print(precompile(a.precompile, workers=a.workers))
    if a.prune is not None:
        print("정리:", prune_parse_cache(a.prune), "개")
