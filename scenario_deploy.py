# -*- coding: utf-8 -*-
"""
scenario_deploy.py — 배포 전/후 시나리오 수집 + 변경내용 리포트 (온디맨드)

원격 (Windows Server 2019, OpenSSH):
    C:\\TEMP\\시나리오\\운영\\OUTPUT\\*.dxml    ← 신규 배포 (여러 번 재배포 가능)
    C:\\TEMP\\시나리오\\과거\\OUTPUT\\*.dxml    ← 직전 배포완료 건 (비교 기준선)

■ 수집 전략 (400개 / 150MB 기준 실측 반영)
    - 매니페스트(파일명·크기·수정시각)만 먼저 조회      → 약 1초, 전송량 ~30KB
    - 매니페스트가 이전과 같으면 전송 0                  → 재클릭 즉시 응답
    - 바뀐 파일만 스테이징 후 tar.gz 로 1회 전송(델타)   → 보통 수 MB 이하
    - 변경 파일이 많으면(임계 초과) 전체 재수집          → gzip 22:1, 150MB→약 7MB
    - 로컬 스냅샷은 (크기,mtime) 캐시로 증분 파싱        → 바뀐 파일만 재파싱

■ 뷰어와의 관계
    업무 FLOW 뷰어는 항상 로컬 캐시만 읽는다(원격 접속 없음).
    원격 접속은 "변경내용 확인" / "최신 가져오기" 를 누른 순간에만 발생한다.
"""
import os
import json
import time
import base64
import shutil
import tarfile
import tempfile
import subprocess
import datetime
import hashlib
import logging

import scenario_deploy_diff as DIFF

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_ROOT = os.path.join(BASE_DIR, "deploy_cache")       # 운영/ 과거/
STATE_PATH = os.path.join(BASE_DIR, "deploy_state.json")
REPORT_DIR = os.path.join(BASE_DIR, "deploy_reports")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

SSH_OPTS = ['-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=3', '-o', 'StrictHostKeyChecking=accept-new']

# 변경 파일이 이 수(또는 이 비율)를 넘으면 델타보다 전체가 유리
DELTA_MAX_FILES = 120
DELTA_MAX_RATIO = 0.35

DEFAULTS = {
    "ssh": "",
    "base": r"C:\TEMP\시나리오",
    "new_dir": "운영",
    "old_dir": "과거",
    "output_dir": "OUTPUT",
    "viewer_env": "",       # 지정 시 운영본을 scenario_cache/<env>/ 로 미러
    "verify": "hash",       # hash=내용해시까지 확인(권장) / fast=크기+수정시각만
}
SLOT_NAME = {"old": "과거", "new": "운영"}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update((json.load(f).get("scenario_deploy") or {}))
    except Exception:
        pass
    return cfg


def save_cfg(patch):
    """
    시나리오 경로/접속 설정 저장 (config.json 의 scenario_deploy 블록).
    DEFAULTS 에 있는 키만 반영하고, verify 는 허용값으로 정규화한다.
    반환: (ok, cfg, error)
    """
    from config_manager import load_config, save_config

    cfg = load_cfg()
    for k in DEFAULTS:
        if k in (patch or {}):
            cfg[k] = str(patch[k] if patch[k] is not None else "").strip()
    if cfg.get("verify") not in ("hash", "fast"):
        cfg["verify"] = "hash"
    if not cfg.get("base"):
        return False, cfg, "시나리오 기준 경로(base)를 입력하세요"
    if not cfg.get("new_dir") or not cfg.get("old_dir"):
        return False, cfg, "운영/과거 폴더명을 입력하세요"

    full = load_config()
    if full is None:
        return False, cfg, "config.json 을 읽을 수 없습니다"
    full["scenario_deploy"] = cfg
    if not save_config(full):
        return False, cfg, "config.json 저장 실패"
    return True, cfg, None


def _remote_dir(cfg, slot):
    parts = [cfg["base"].rstrip("\\"), cfg["new_dir"] if slot == "new" else cfg["old_dir"]]
    if cfg.get("output_dir"):
        parts.append(cfg["output_dir"])
    return "\\".join(parts)


def _local_dir(slot):
    d = os.path.join(CACHE_ROOT, SLOT_NAME[slot])
    os.makedirs(d, exist_ok=True)
    return d


def _snapcache(slot):
    return os.path.join(CACHE_ROOT, f".snap_{slot}.pkl")


# ══════════════════════════════════════════════════════════
# SSH / PowerShell  (UTF-16LE -EncodedCommand → 한글 경로 안전)
# ══════════════════════════════════════════════════════════
def _q(s):
    """PowerShell 작은따옴표 리터럴 이스케이프."""
    return str(s).replace("'", "''")


def _ps(alias, script, timeout=300):
    prefix = ("$ErrorActionPreference='SilentlyContinue';"
              "$ProgressPreference='SilentlyContinue';"
              "[Console]::OutputEncoding=[Text.Encoding]::UTF8;")
    enc = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    cmd = ['ssh'] + SSH_OPTS + [alias, 'powershell', '-NoProfile',
                                '-NonInteractive', '-EncodedCommand', enc]
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='ignore', timeout=timeout)


def remote_manifest(alias, remote_dir, verify="hash"):
    """
    원격 폴더의 시나리오 파일 목록 → {파일명소문자: [원본명, 크기, ticks, md5?]}

    verify="hash" (기본)
        파일 내용 MD5 까지 원격에서 계산해 함께 받는다.
        크기·수정시각이 같은데 내용만 바뀐 경우(타임스탬프 보존 배포 등)를 잡는다.
        .NET 직접 호출이라 150MB 기준 약 1~3초. 전송량은 여전히 ~40KB.
    verify="fast"
        크기+수정시각만. 더 빠르지만 위 케이스를 놓칠 수 있다.
    """
    if verify == "hash":
        ps = (f"$d='{_q(remote_dir)}';"
              "if(-not (Test-Path -LiteralPath $d)){'__MISSING__';exit};"
              "$md5=[Security.Cryptography.MD5]::Create();"
              "Get-ChildItem -LiteralPath $d -File | "
              "Where-Object { $_.Extension -in '.dxml','.xml' } | "
              "ForEach-Object { "
              "  $fs=[IO.File]::OpenRead($_.FullName);"
              "  $h=[BitConverter]::ToString($md5.ComputeHash($fs)).Replace('-','');"
              "  $fs.Close();"
              "  $_.Name+'|'+$_.Length+'|'+$_.LastWriteTimeUtc.Ticks+'|'+$h }")
    else:
        ps = (f"$d='{_q(remote_dir)}';"
              "if(-not (Test-Path -LiteralPath $d)){'__MISSING__';exit};"
              "Get-ChildItem -LiteralPath $d -File | "
              "Where-Object { $_.Extension -in '.dxml','.xml' } | "
              "ForEach-Object { $_.Name + '|' + $_.Length + '|' + $_.LastWriteTimeUtc.Ticks }")
    r = _ps(alias, ps, timeout=90)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or "").strip() or "원격 조회 실패"}
    lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    if lines and lines[0] == "__MISSING__":
        return {"ok": False, "error": f"경로 없음: {remote_dir}"}
    man, total = {}, 0
    for l in lines:
        f = l.split("|")
        if len(f) < 3:
            continue
        name, size, ticks = f[0], f[1], f[2]
        rec = [name, int(size), ticks]
        if len(f) >= 4:
            rec.append(f[3])          # 내용 MD5
        man[name.lower()] = rec
        total += int(size)
    sig = hashlib.md5("\n".join(sorted(lines)).encode()).hexdigest()[:16]
    return {"ok": True, "manifest": man, "sig": sig,
            "files": len(man), "bytes": total, "path": remote_dir}


# ══════════════════════════════════════════════════════════
# 전송 (tar.gz → scp, 실패 시 base64 폴백)
# ══════════════════════════════════════════════════════════
def _fetch_tgz(alias, build_script, rtmp):
    """원격에서 tgz 를 만든 뒤 회수 → 로컬 임시파일 경로 반환."""
    mk = _ps(alias,
             build_script + f";if(Test-Path -LiteralPath '{_q(rtmp)}'){{'OK'}}else{{'FAIL'}}",
             timeout=900)
    if mk.returncode != 0 or 'OK' not in (mk.stdout or ''):
        raise RuntimeError((mk.stderr or "").strip() or "원격 아카이브 생성 실패")

    fd, local = tempfile.mkstemp(suffix=".tgz")
    os.close(fd)
    sp = subprocess.run(['scp'] + SSH_OPTS +
                        [f'{alias}:{rtmp.replace(chr(92), "/")}', local],
                        capture_output=True, text=True, timeout=1800)
    if sp.returncode != 0:                       # scp 불가 → base64 폴백
        try:
            os.unlink(local)
        except OSError:
            pass
        r = _ps(alias, f"[Convert]::ToBase64String([IO.File]::ReadAllBytes('{_q(rtmp)}'))",
                timeout=1800)
        if r.returncode != 0 or not (r.stdout or "").strip():
            _ps(alias, f"Remove-Item -LiteralPath '{_q(rtmp)}' -Force", timeout=30)
            raise RuntimeError("전송 실패 (scp/base64 모두)")
        fd, local = tempfile.mkstemp(suffix=".tgz")
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode("".join(r.stdout.split())))
    _ps(alias, f"Remove-Item -LiteralPath '{_q(rtmp)}' -Force", timeout=30)
    return local


def _extract(tgz, dest, wipe):
    """tgz → dest. wipe=True 면 기존 시나리오 파일 제거 후 해제(전체 수집)."""
    if wipe:
        for f in os.listdir(dest):
            p = os.path.join(dest, f)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif f.lower().endswith(DIFF.SCN_EXT):
                os.unlink(p)
    with tarfile.open(tgz) as t:                 # 'r' = gzip 자동 인식
        t.extractall(dest)
    # 하위폴더가 생겼으면 평면화 + 시나리오 외 파일 정리
    for root, _d, files in os.walk(dest, topdown=False):
        if root == dest:
            continue
        for f in files:
            src = os.path.join(root, f)
            if f.lower().endswith(DIFF.SCN_EXT):
                shutil.move(src, os.path.join(dest, f))
            else:
                os.unlink(src)
        try:
            os.rmdir(root)
        except OSError:
            pass
    for f in os.listdir(dest):
        p = os.path.join(dest, f)
        if os.path.isfile(p) and not f.startswith('.') \
                and not f.lower().endswith(DIFF.SCN_EXT):
            os.unlink(p)


def _pull_full(alias, remote_dir, dest):
    rtmp = f"C:\\Windows\\Temp\\scn_{int(time.time()*1000)}.tgz"
    script = f"tar -czf '{_q(rtmp)}' -C '{_q(remote_dir)}' ."
    tgz = _fetch_tgz(alias, script, rtmp)
    try:
        _extract(tgz, dest, wipe=True)
    finally:
        os.unlink(tgz)


def _pull_delta(alias, remote_dir, dest, names):
    """바뀐 파일만 스테이징 → tar.gz 1회 전송."""
    ts = int(time.time() * 1000)
    rtmp = f"C:\\Windows\\Temp\\scn_{ts}.tgz"
    stage = f"C:\\Windows\\Temp\\scnstg_{ts}"
    arr = ",".join("'" + _q(n) + "'" for n in names)
    script = (
        f"$d='{_q(remote_dir)}';$s='{_q(stage)}';"
        f"New-Item -ItemType Directory -Force -Path $s | Out-Null;"
        f"foreach($n in @({arr})){{"
        f" Copy-Item -LiteralPath (Join-Path $d $n) -Destination $s -Force }};"
        f"tar -czf '{_q(rtmp)}' -C $s .;"
        f"Remove-Item -LiteralPath $s -Recurse -Force"
    )
    tgz = _fetch_tgz(alias, script, rtmp)
    try:
        _extract(tgz, dest, wipe=False)
    finally:
        os.unlink(tgz)


# ══════════════════════════════════════════════════════════
# 슬롯 동기화 (매니페스트 비교 → 전체/델타/스킵 결정)
# ══════════════════════════════════════════════════════════
def _sync_slot(alias, slot, remote_dir, state, force=False, verify="hash"):
    info = remote_manifest(alias, remote_dir, verify=verify)
    if not info.get("ok"):
        return info

    dest = _local_dir(slot)
    prev = (state["slots"].get(slot) or {}).get("manifest") or {}
    man = info["manifest"]

    have = {f.lower() for f in os.listdir(dest) if f.lower().endswith(DIFF.SCN_EXT)}
    consistent = bool(prev) and set(prev) == have

    if force or not consistent:
        mode, targets = "full", list(man)
    else:
        changed = [k for k, v in man.items() if prev.get(k) != v]
        removed = [k for k in prev if k not in man]
        if not changed and not removed:
            mode, targets = "skip", []
        elif len(changed) > DELTA_MAX_FILES or len(changed) > len(man) * DELTA_MAX_RATIO:
            mode, targets = "full", list(man)
        else:
            mode, targets = "delta", changed
            for k in removed:                       # 원격에서 사라진 파일 로컬 삭제
                p = os.path.join(dest, prev[k][0])
                if os.path.isfile(p):
                    os.unlink(p)

    t0 = time.time()
    if mode == "full":
        _pull_full(alias, remote_dir, dest)
    elif mode == "delta":
        _pull_delta(alias, remote_dir, dest, [man[k][0] for k in targets])

    state["slots"][slot] = {
        "manifest": man, "sig": info["sig"], "files": info["files"],
        "bytes": info["bytes"], "path": remote_dir,
        "pulled_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return {"ok": True, "sig": info["sig"], "files": info["files"],
            "bytes": info["bytes"], "path": remote_dir,
            "mode": mode, "pulled": len(targets) if mode != "skip" else 0,
            "elapsed": round(time.time() - t0, 1)}


# ══════════════════════════════════════════════════════════
# 상태 · 부가
# ══════════════════════════════════════════════════════════
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"slots": {}, "history": []}


def _save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def _make_locator(viewer_env):
    if not viewer_env:
        return None
    try:
        import scenario_store as STORE
    except Exception:
        return None

    def locate(page, seqs):
        if not seqs:
            return {}
        try:
            res = STORE.locate_blocks(viewer_env, seqs, page=page)["results"]
            return {r["seq"]: r["matches"][0] for r in res if r.get("matches")}
        except Exception:
            return {}
    return locate


def _mirror_viewer(cfg):
    """운영 배포본을 뷰어 캐시(scenario_cache/<env>)로 미러."""
    env = cfg.get("viewer_env")
    if not env:
        return
    try:
        import scenario_store as STORE
        dst = os.path.join(STORE.CACHE_ROOT, env)
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(dst):
            if f.lower().endswith(DIFF.SCN_EXT):
                os.unlink(os.path.join(dst, f))
        src = _local_dir("new")
        for f in os.listdir(src):
            if f.lower().endswith(DIFF.SCN_EXT):
                shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
        STORE._cache.pop(env, None)
        try:                                  # 디스크 캐시(scenario_boot)도 무효화
            import scenario_boot
            scenario_boot.clear_disk_cache()
        except Exception:
            pass
    except Exception:
        logger.exception("뷰어 캐시 미러 실패")


# ══════════════════════════════════════════════════════════
# 공개 API
# ══════════════════════════════════════════════════════════
def peek():
    """가벼운 변경 감지 — 매니페스트만 조회(파일 전송 없음, 약 1~2초)."""
    cfg = load_cfg()
    if not cfg.get("ssh"):
        return {"error": "config.json 의 scenario_deploy.ssh 설정 필요"}
    st = load_state()
    out, need = {}, False
    for slot in ("old", "new"):
        info = remote_manifest(cfg["ssh"], _remote_dir(cfg, slot),
                               verify=cfg.get("verify", "hash"))
        if not info.get("ok"):
            return {"error": f"[{SLOT_NAME[slot]}] {info.get('error')}"}
        prev = (st.get("slots", {}).get(slot) or {}).get("sig")
        chg = prev != info["sig"]
        need = need or chg
        out[SLOT_NAME[slot]] = {"files": info["files"],
                                "mb": round(info["bytes"] / 1048576, 1),
                                "changed": chg, "known": bool(prev)}
    out["need_refresh"] = need
    return out


def check(force=False):
    """
    '변경내용 확인' 버튼 진입점.
      ① 매니페스트 조회 → ② 필요한 슬롯만 전체/델타 수집
      → ③ 증분 스냅샷 → ④ 과거·운영 diff → ⑤ 리포트
    """
    cfg = load_cfg()
    alias = cfg.get("ssh")
    if not alias:
        return {"error": "config.json 의 scenario_deploy.ssh 설정 필요"}

    st = load_state()
    st.setdefault("slots", {})
    fetch = {}
    for slot in ("old", "new"):
        r = _sync_slot(alias, slot, _remote_dir(cfg, slot), st, force=force,
                       verify=cfg.get("verify", "hash"))
        if not r.get("ok"):
            return {"error": f"[{SLOT_NAME[slot]}] {r.get('error')}",
                    "path": _remote_dir(cfg, slot)}
        fetch[SLOT_NAME[slot]] = r

    # 운영 재배포 이력
    new_sig = fetch["운영"]["sig"]
    hist = st.get("history", [])
    if not hist or hist[-1].get("sig") != new_sig:
        hist.append({"sig": new_sig, "files": fetch["운영"]["files"],
                     "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        st["history"] = hist[-50:]
    _save_state(st)

    if fetch["운영"]["mode"] != "skip":
        _mirror_viewer(cfg)

    os.makedirs(REPORT_DIR, exist_ok=True)
    key = f"{fetch['과거']['sig']}__{new_sig}"
    rpath = os.path.join(REPORT_DIR, key + ".json")

    if os.path.isfile(rpath) and not force:
        with open(rpath, encoding="utf-8") as f:
            rep = json.load(f)
        rep["cached"] = True
    else:
        t0 = time.time()
        so, sto = DIFF.snapshot_folder_cached(_local_dir("old"), _snapcache("old"))
        sn, stn = DIFF.snapshot_folder_cached(_local_dir("new"), _snapcache("new"))
        rep = DIFF.diff_snapshots(so, sn, locate=_make_locator(cfg.get("viewer_env")))
        rep["cached"] = False
        rep["elapsed"] = round(time.time() - t0, 1)
        rep["parse"] = {"과거": sto, "운영": stn}
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

    rep["meta"] = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "old": {"path": fetch["과거"]["path"], "files": fetch["과거"]["files"],
                "mb": round(fetch["과거"]["bytes"] / 1048576, 1),
                "sig": fetch["과거"]["sig"], "fetch": fetch["과거"]["mode"],
                "pulled": fetch["과거"]["pulled"], "sec": fetch["과거"]["elapsed"]},
        "new": {"path": fetch["운영"]["path"], "files": fetch["운영"]["files"],
                "mb": round(fetch["운영"]["bytes"] / 1048576, 1),
                "sig": new_sig, "fetch": fetch["운영"]["mode"],
                "pulled": fetch["운영"]["pulled"], "sec": fetch["운영"]["elapsed"]},
        "redeploy_count": len(st.get("history", [])),
    }
    return rep


def status():
    cfg = load_cfg()
    st = load_state()
    slots = {}
    for slot, s in (st.get("slots") or {}).items():
        slots[SLOT_NAME.get(slot, slot)] = {k: v for k, v in s.items() if k != "manifest"}
    return {"config": {k: v for k, v in cfg.items() if k != "ssh"},
            "ssh": bool(cfg.get("ssh")), "slots": slots,
            "history": st.get("history", [])}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if '--peek' in sys.argv:
        print(json.dumps(peek(), ensure_ascii=False, indent=2))
        sys.exit(0)
    r = check(force='--force' in sys.argv)
    if r.get("error"):
        print("오류:", r["error"])
    else:
        print(json.dumps(r["summary"], ensure_ascii=False, indent=2))
        print(json.dumps(r["meta"], ensure_ascii=False, indent=2))
        if r.get("parse"):
            print("파싱:", json.dumps(r["parse"], ensure_ascii=False))
