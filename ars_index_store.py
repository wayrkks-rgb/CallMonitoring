# -*- coding: utf-8 -*-
"""
ars_index_store.py — ARS 콜 인덱스 저장소 (SQLite)

역할:
  - 인덱서가 추출한 '종료된 콜'의 메타데이터를 저장 (원본은 저장 안 함)
  - 검색 시 phone/cust_id 로 콜 위치(file_path, start_offset~end_offset)를 즉시 조회
  - 증분 스캔용 파일별 오프셋 상태 저장
  - 30일 경과분 자동 정리

설계 메모:
  - SQLite 는 퍼블릭 도메인 · 파이썬 표준 내장(sqlite3) → 폐쇄망 반입/설치 부담 0
  - 인덱서(쓰기 스레드)와 웹 검색(읽기)이 동시에 접근 → WAL 모드 + Lock 으로 직렬화
  - start_time/end_time 은 'YYYY-MM-DD HH:MM:SS' 전체 시각(정렬=시간순, 30일 정리에 사용)
  - ucid UNIQUE → 준실시간 재스캔 시 UPSERT 로 중복/갱신 안전
"""

import sqlite3
import threading
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = 'ars_index.db'
RETENTION_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    server        TEXT,
    file_path     TEXT,
    start_offset  INTEGER,
    end_offset    INTEGER,
    channel       TEXT,
    ucid          TEXT UNIQUE,
    phone         TEXT,
    cust_id       TEXT,
    start_time    TEXT,
    end_time      TEXT,
    end_by        TEXT,
    line_count    INTEGER,
    indexed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_phone  ON calls(phone);
CREATE INDEX IF NOT EXISTS idx_calls_custid ON calls(cust_id);
CREATE INDEX IF NOT EXISTS idx_calls_start  ON calls(start_time);

CREATE TABLE IF NOT EXISTS scan_state (
    file_path       TEXT PRIMARY KEY,
    last_offset     INTEGER,   -- 지금까지 읽어들인 EOF 위치(신규 데이터 감지용)
    pending_offset  INTEGER,   -- 다음 스캔 시작점(아직 안 끝난 콜의 시작 바이트)
    sealed          INTEGER DEFAULT 0,  -- 1이면 더 이상 커지지 않는 완료 파일
    updated_at      TEXT
);
"""


class ArsIndexStore:
    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── 콜 UPSERT ─────────────────────────────────────────
    def upsert_calls(self, calls):
        """
        calls: dict 리스트. 필요한 키:
          server, file_path, start_offset, end_offset, channel, ucid,
          phone, cust_id, start_time, end_time, end_by, line_count
        (ucid 기준 UPSERT — 이미 있으면 갱신)
        """
        if not calls:
            return 0
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows = [(
            c.get('server'), c.get('file_path'),
            c.get('start_offset'), c.get('end_offset'),
            c.get('channel'), c.get('ucid'),
            c.get('phone'), c.get('cust_id'),
            c.get('start_time'), c.get('end_time'),
            c.get('end_by'), c.get('line_count'), now,
        ) for c in calls if c.get('ucid')]
        if not rows:
            return 0
        sql = """
        INSERT INTO calls
          (server, file_path, start_offset, end_offset, channel, ucid,
           phone, cust_id, start_time, end_time, end_by, line_count, indexed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ucid) DO UPDATE SET
           server=excluded.server, file_path=excluded.file_path,
           start_offset=excluded.start_offset, end_offset=excluded.end_offset,
           channel=excluded.channel, phone=excluded.phone, cust_id=excluded.cust_id,
           start_time=excluded.start_time, end_time=excluded.end_time,
           end_by=excluded.end_by, line_count=excluded.line_count,
           indexed_at=excluded.indexed_at
        """
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()
        return len(rows)

    # ── 검색 ──────────────────────────────────────────────
    def search(self, phone=None, cust_id=None, start_date=None, end_date=None,
               servers=None, limit=500):
        """
        phone 또는 cust_id 로 콜 조회. start_date/end_date='YYYY-MM-DD'(포함),
        servers=허용 서버라벨 리스트(None이면 전체). 시간순 정렬.
        """
        where, params = [], []
        if phone and cust_id:
            where.append("(phone = ? OR cust_id = ?)"); params += [phone, cust_id]
        elif phone:
            where.append("phone = ?"); params.append(phone)
        elif cust_id:
            where.append("cust_id = ?"); params.append(cust_id)
        else:
            return []

        if start_date:
            where.append("start_time >= ?"); params.append(f"{start_date} 00:00:00")
        if end_date:
            where.append("start_time <= ?"); params.append(f"{end_date} 23:59:59")
        if servers:
            qs = ",".join("?" * len(servers))
            where.append(f"server IN ({qs})"); params += list(servers)

        sql = f"SELECT * FROM calls WHERE {' AND '.join(where)} ORDER BY start_time LIMIT ?"
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    # ── 스캔 상태(증분) ───────────────────────────────────
    def get_scan_state(self, file_path):
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM scan_state WHERE file_path = ?", (file_path,))
            r = cur.fetchone()
            return dict(r) if r else None

    def set_scan_state(self, file_path, last_offset, pending_offset, sealed=0):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._lock:
            self._conn.execute("""
                INSERT INTO scan_state (file_path, last_offset, pending_offset, sealed, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(file_path) DO UPDATE SET
                   last_offset=excluded.last_offset,
                   pending_offset=excluded.pending_offset,
                   sealed=excluded.sealed,
                   updated_at=excluded.updated_at
            """, (file_path, last_offset, pending_offset, sealed, now))
            self._conn.commit()

    def active_scan_files(self, only_unsealed=True):
        with self._lock:
            q = "SELECT * FROM scan_state"
            if only_unsealed:
                q += " WHERE sealed = 0"
            return [dict(r) for r in self._conn.execute(q).fetchall()]

    # ── 정리 / 상태 ───────────────────────────────────────
    def purge_older_than(self, days=RETENTION_DAYS):
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        with self._lock:
            cur = self._conn.execute("DELETE FROM calls WHERE start_time < ?", (cutoff,))
            # 오래된 sealed 스캔상태도 정리
            self._conn.execute(
                "DELETE FROM scan_state WHERE sealed = 1 AND updated_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def stats(self):
        today = datetime.now().strftime('%Y-%m-%d')
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
            latest = self._conn.execute(
                "SELECT MAX(indexed_at) FROM calls").fetchone()[0]
            files = self._conn.execute(
                "SELECT COUNT(*) FROM scan_state WHERE sealed = 0").fetchone()[0]
            # '오늘 로그가 안 나온다' 진단용 — 오늘 색인된 콜 수와 마지막 콜 시각
            today_calls, last_call = self._conn.execute(
                "SELECT COUNT(*), MAX(start_time) FROM calls WHERE start_time >= ?",
                (f"{today} 00:00:00",)).fetchone()
        return {'indexed_calls': total, 'last_indexed_at': latest,
                'active_files': files,
                'today': today,
                'today_calls': today_calls, 'today_last_call': last_call}

    def close(self):
        with self._lock:
            self._conn.close()


# ── 프로세스 공용 싱글톤 (인덱서 스레드와 웹 검색이 같은 DB 인스턴스 공유) ──
_default_store = None
_default_lock = threading.Lock()


def get_default_store(db_path=DEFAULT_DB_PATH):
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = ArsIndexStore(db_path)
        return _default_store
