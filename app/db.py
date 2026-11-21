# -*- coding: utf-8 -*-
"""SQLite 项目数据库：项目、素材、分镜、任务日志。

设计要点（对应任务书）：
- 数据库只存路径与元数据，不存大型视频文件。
- 每个项目一个 .db 文件，放在 projects/ 目录。
- 素材路径失效后可重新定位（update_asset_path）。
"""
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ProjectDB:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self.connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    folder TEXT,
                    size_bytes INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    width INTEGER DEFAULT 0,
                    height INTEGER DEFAULT 0,
                    fps REAL DEFAULT 0,
                    rotation INTEGER DEFAULT 0,
                    has_audio INTEGER DEFAULT 0,
                    decode_ok INTEGER DEFAULT 0,
                    codec TEXT,
                    error TEXT,
                    thumb_path TEXT,
                    import_time TEXT,
                    UNIQUE(path)
                );
                CREATE TABLE IF NOT EXISTS storyboards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seq INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT DEFAULT '未分析',
                    locked INTEGER DEFAULT 0,
                    selected_clip_id INTEGER,
                    note TEXT
                );
                CREATE TABLE IF NOT EXISTS clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    storyboard_id INTEGER,
                    asset_id INTEGER,
                    start_ms INTEGER,
                    end_ms INTEGER,
                    primary_tag TEXT,
                    confidence REAL DEFAULT 0,
                    score_text TEXT,
                    recommend_reason TEXT,
                    potential_issue TEXT,
                    verify_state TEXT DEFAULT '待核验',
                    source TEXT DEFAULT 'manual'
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT,
                    input_file TEXT,
                    output_file TEXT,
                    status TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    error TEXT,
                    retryable INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            # 项目基础信息
            c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", ("created_at", now()))
            c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", ("app_version", config.APP_VERSION))
            self._migrate(c)

    def _migrate(self, c):
        """增量迁移：只添加缺失的列/表，不改动已有数据（只增不改）。"""
        def has_col(table, col):
            return any(r["name"] == col for r in c.execute(f"PRAGMA table_info({table})"))

        # clips 表阶段二新增字段
        for col, decl in [
            ("keyframe_paths", "TEXT DEFAULT ''"),
            ("cut_reason", "TEXT DEFAULT ''"),
            ("shot_index", "INTEGER DEFAULT 0"),
            ("confidence", "REAL DEFAULT 0"),  # 保险：旧库若缺
            ("analysis_status", "TEXT DEFAULT '待核验'"),
            # 阶段三新增：视觉线索、质量分、语义核验（只增列，不改旧数据）
            ("vision_labels", "TEXT DEFAULT '[]'"),
            ("vision_metrics", "TEXT DEFAULT '{}'"),
            ("quality_total", "INTEGER DEFAULT 0"),
            ("quality_detail", "TEXT DEFAULT '{}'"),
            ("semantic_status", "TEXT DEFAULT '未分析'"),
            # 人工评分标签：用于校准自动质量分（只增列）
            ("human_rating", "INTEGER"),
            ("human_rating_note", "TEXT DEFAULT ''"),
            ("quality_raw_total", "INTEGER DEFAULT 0"),
        ]:
            if not has_col("clips", col):
                c.execute(f"ALTER TABLE clips ADD COLUMN {col} {decl}")

        # 素材新增：md5 / 感知哈希 / 分析状态 / 镜头数
        for col, decl in [
            ("md5", "TEXT DEFAULT ''"),
            ("dhash", "TEXT DEFAULT ''"),
            ("analysis_status", "TEXT DEFAULT '未分析'"),
            ("shot_count", "INTEGER DEFAULT 0"),
            ("scene_json", "TEXT DEFAULT ''"),
        ]:
            if not has_col("assets", col):
                c.execute(f"ALTER TABLE assets ADD COLUMN {col} {decl}")

        c.execute("""CREATE TABLE IF NOT EXISTS duplicate_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            member_paths TEXT,
            created_at TEXT
        )""")

        # 阶段四：时间线镜头表。每个镜头独立ID/素材引用/起止/裁切/锁定/缓存标识。
        # 单镜头操作只改自己这一行，绝不动其它镜头（非破坏性编辑的数据结构基础）。
        c.execute("""CREATE TABLE IF NOT EXISTS timeline (
            shot_id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL,
            storyboard_id INTEGER,
            name TEXT,
            asset_path TEXT,
            asset_id INTEGER,
            clip_id INTEGER,
            start_ms INTEGER DEFAULT 0,
            end_ms INTEGER DEFAULT 0,
            crop TEXT DEFAULT '',
            locked INTEGER DEFAULT 0,
            cache_key TEXT DEFAULT '',
            thumb_path TEXT DEFAULT '',
            duration_ms INTEGER DEFAULT 0,
            updated_at TEXT
        )""")
        if not has_col("timeline", "seq"):  # 保险
            c.execute("ALTER TABLE timeline ADD COLUMN seq INTEGER DEFAULT 0")
        # 阶段六：联动镜头的子片段（JSON）。空=普通镜头；非空=一个镜头含多段真实画面。
        # 增量迁移：只加列，不动已有数据。
        if not has_col("timeline", "sub_segments"):
            c.execute("ALTER TABLE timeline ADD COLUMN sub_segments TEXT DEFAULT ''")
        # 阶段七：每镜头建议时长（毫秒）。0=不限（素材多长用多长）。
        # 素材比它短->末帧定格补足；比它长->只取需要的部分。仍只加列，不动已有数据。
        if not has_col("timeline", "target_ms"):
            c.execute("ALTER TABLE timeline ADD COLUMN target_ms INTEGER DEFAULT 0")
        if not has_col("storyboards", "target_ms"):
            c.execute("ALTER TABLE storyboards ADD COLUMN target_ms INTEGER DEFAULT 0")

        # 阶段五：逐句文案/配音表。每句对应一个镜头，可独立编辑文字、时间、配音文件。
        c.execute("""CREATE TABLE IF NOT EXISTS narration_lines (
            line_id TEXT PRIMARY KEY,
            seq INTEGER DEFAULT 0,
            shot_id TEXT,
            name TEXT,
            text TEXT DEFAULT '',
            start_ms INTEGER DEFAULT 0,
            end_ms INTEGER DEFAULT 0,
            voice_path TEXT DEFAULT '',
            source TEXT DEFAULT 'template',
            updated_at TEXT
        )""")

    # ---------- 项目 ----------
    def set_project_name(self, name):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('name',?)", (name,))

    def get_meta(self):
        with self.connect() as c:
            return {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM meta")}

    def touch_modified(self):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('modified_at',?)", (now(),))

    # ---------- 素材 ----------
    def add_asset(self, data: dict):
        """插入或更新一条素材记录，返回 asset id。"""
        cols = ("path", "filename", "folder", "size_bytes", "duration_ms", "width",
                "height", "fps", "rotation", "has_audio", "decode_ok", "codec",
                "error", "thumb_path", "import_time")
        vals = [data.get(k) for k in cols]
        if vals[-1] is None:
            vals[-1] = now()
        with self.connect() as c:
            c.execute(
                "INSERT INTO assets(" + ",".join(cols) + ") VALUES(" + ",".join("?" * len(cols)) + ")"
                " ON CONFLICT(path) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in cols if k != "path"),
                vals,
            )
            row = c.execute("SELECT id FROM assets WHERE path=?", (data["path"],)).fetchone()
            return row["id"] if row else None

    def get_assets(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM assets ORDER BY id")]

    def get_asset(self, asset_id):
        with self.connect() as c:
            r = c.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            return dict(r) if r else None

    def update_asset_path(self, asset_id, new_path):
        """素材重新定位：路径失效后指向新位置。"""
        with self.connect() as c:
            c.execute("UPDATE assets SET path=?, error=NULL WHERE id=?", (new_path, asset_id))

    def delete_asset(self, asset_id):
        with self.connect() as c:
            c.execute("DELETE FROM assets WHERE id=?", (asset_id,))

    # ---------- 分镜 ----------
    def init_storyboards(self, names):
        with self.connect() as c:
            if c.execute("SELECT COUNT(*) n FROM storyboards").fetchone()["n"] > 0:
                return
            for i, name in enumerate(names, 1):
                c.execute("INSERT INTO storyboards(seq,name,status) VALUES(?,?,?)", (i, name, "未分析"))

    def get_storyboards(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM storyboards ORDER BY seq")]

    def update_storyboard(self, sb_id, **fields):
        allowed = {"seq", "name", "status", "locked", "selected_clip_id", "note", "target_ms"}
        sets = [k for k in fields if k in allowed]
        if not sets:
            return
        with self.connect() as c:
            c.execute(
                "UPDATE storyboards SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?",
                [fields[k] for k in sets] + [sb_id],
            )

    def add_storyboard(self, name):
        with self.connect() as c:
            n = c.execute("SELECT COALESCE(MAX(seq),0)+1 n FROM storyboards").fetchone()["n"]
            cur = c.execute("INSERT INTO storyboards(seq,name,status) VALUES(?,?,?)", (n, name, "未分析"))
            return cur.lastrowid

    def delete_storyboard(self, sb_id):
        with self.connect() as c:
            c.execute("DELETE FROM storyboards WHERE id=?", (sb_id,))
            c.execute("DELETE FROM clips WHERE storyboard_id=?", (sb_id,))

    def reorder_storyboards(self, id_seq_list):
        """按给定 id 顺序重排 seq。"""
        with self.connect() as c:
            for seq, sb_id in enumerate(id_seq_list, 1):
                c.execute("UPDATE storyboards SET seq=? WHERE id=?", (seq, sb_id))

    # ---------- 候选片段 ----------
    def add_clip(self, data: dict):
        cols = ("storyboard_id", "asset_id", "start_ms", "end_ms", "primary_tag",
                "confidence", "score_text", "recommend_reason", "potential_issue",
                "verify_state", "source", "keyframe_paths", "cut_reason",
                "shot_index", "analysis_status")
        vals = [data.get(k) for k in cols]
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO clips(" + ",".join(cols) + ") VALUES(" + ",".join("?" * len(cols)) + ")",
                vals,
            )
            return cur.lastrowid

    def get_clips(self, storyboard_id=None):
        with self.connect() as c:
            if storyboard_id is None:
                return [dict(r) for r in c.execute("SELECT * FROM clips ORDER BY id")]
            return [dict(r) for r in c.execute("SELECT * FROM clips WHERE storyboard_id=? ORDER BY id", (storyboard_id,))]

    def update_clip(self, clip_id, **fields):
        allowed = {"start_ms", "end_ms", "primary_tag", "confidence", "score_text",
                   "recommend_reason", "potential_issue", "verify_state", "analysis_status",
                   "cut_reason", "keyframe_paths", "vision_labels", "vision_metrics",
                   "quality_total", "quality_detail", "semantic_status",
                   "human_rating", "human_rating_note", "quality_raw_total"}
        sets = [k for k in fields if k in allowed]
        if not sets:
            return
        with self.connect() as c:
            c.execute("UPDATE clips SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?",
                      [fields[k] for k in sets] + [clip_id])

    def delete_clip(self, clip_id):
        with self.connect() as c:
            c.execute("DELETE FROM clips WHERE id=?", (clip_id,))

    def get_clips_by_asset(self, asset_id):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM clips WHERE asset_id=? ORDER BY start_ms", (asset_id,))]

    def clear_clips_for_asset(self, asset_id):
        """重新分析某素材时，先清掉它旧的候选片段（只删派生记录，不动原片）。"""
        with self.connect() as c:
            c.execute("DELETE FROM clips WHERE asset_id=?", (asset_id,))

    def save_scene_analysis(self, asset_id, scene_result, keyframe_map):
        """保存一条视频的镜头切分结果：更新素材状态 + 写入候选片段。

        keyframe_map: {shot_index: [关键帧路径, ...]}
        """
        self.clear_clips_for_asset(asset_id)
        with self.connect() as c:
            c.execute(
                "UPDATE assets SET analysis_status=?, shot_count=?, scene_json=? WHERE id=?",
                ("已分析", len(scene_result["shots"]),
                 __import__("json").dumps({
                     "boundaries": scene_result["boundaries"],
                     "light_events": scene_result["light_events"],
                 }), asset_id),
            )
        for shot in scene_result["shots"]:
            idx = shot["index"]
            kfs = keyframe_map.get(idx, [])
            nxt = next((b for b in scene_result["boundaries"]
                        if b["time_ms"] == shot["end_ms"]), None)
            self.add_clip({
                "asset_id": asset_id,
                "start_ms": shot["start_ms"], "end_ms": shot["end_ms"],
                "primary_tag": "", "confidence": nxt["confidence"] if nxt else 0.0,
                "score_text": "", "recommend_reason": "",
                "potential_issue": nxt["reason"] if nxt else "",
                "verify_state": "待核验", "source": "auto_scene",
                "keyframe_paths": __import__("json").dumps(kfs),
                "cut_reason": nxt["reason"] if nxt else "视频起始片段",
                "shot_index": idx, "analysis_status": "候选待核验",
            })

    def update_asset_analysis_fields(self, asset_id, **fields):
        allowed = {"md5", "dhash", "analysis_status", "shot_count", "scene_json"}
        sets = [k for k in fields if k in allowed]
        if not sets:
            return
        with self.connect() as c:
            c.execute("UPDATE assets SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?",
                      [fields[k] for k in sets] + [asset_id])

    def save_duplicate_groups(self, groups):
        with self.connect() as c:
            c.execute("DELETE FROM duplicate_groups")
            for kind, gs in (("exact", groups["exact_groups"]),
                             ("similar", groups["similar_groups"])):
                for g in gs:
                    c.execute(
                        "INSERT INTO duplicate_groups(kind,member_paths,created_at) VALUES(?,?,?)",
                        (kind, __import__("json").dumps([a["path"] for a in g]), now()),
                    )

    def get_duplicate_groups(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM duplicate_groups ORDER BY id")]

    # ---------- 任务日志 ----------
    def add_task(self, task_type, input_file="", output_file=""):
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO tasks(task_type,input_file,output_file,status,started_at,retryable) "
                "VALUES(?,?,?,?,?,1)",
                (task_type, input_file, output_file, "运行中", now()),
            )
            return cur.lastrowid

    def finish_task(self, task_id, status="成功", error="", output_file=None):
        with self.connect() as c:
            if output_file is not None:
                c.execute("UPDATE tasks SET status=?,ended_at=?,error=?,output_file=? WHERE id=?",
                          (status, now(), error, output_file, task_id))
            else:
                c.execute("UPDATE tasks SET status=?,ended_at=?,error=? WHERE id=?",
                          (status, now(), error, task_id))

    def get_tasks(self, limit=200):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))]

    # ---------- 设置 ----------
    def set_setting(self, key, value):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))

    def get_settings(self):
        with self.connect() as c:
            return {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}

    # ---------- 【阶段十二新增】素材分组 / 引用检查 / 步骤状态 ----------
    def set_asset_group(self, asset_ids, group_name):
        """批量把素材归到某个文件夹分组（只改项目记录，不动磁盘文件）。"""
        ids = [int(i) for i in asset_ids]
        if not ids:
            return 0
        with self.connect() as c:
            c.execute(f"UPDATE assets SET folder=? WHERE id IN ({','.join('?' * len(ids))})",
                      [str(group_name)] + ids)
            return c.execute(
                f"SELECT COUNT(*) AS n FROM assets WHERE id IN ({','.join('?' * len(ids))})",
                ids).fetchone()["n"]

    def count_shots_using_paths(self, paths):
        """统计时间线里有多少镜头仍引用这些素材路径（用于移出前如实提示）。"""
        ps = [str(p) for p in paths if str(p).strip()]
        if not ps:
            return 0
        with self.connect() as c:
            rows = c.execute("SELECT asset_path, sub_segments FROM timeline").fetchall()
        import json
        hit = 0
        for r in rows:
            ap = (r["asset_path"] or "").strip()
            if ap and ap in ps:
                hit += 1
                continue
            raw = r["sub_segments"] or ""
            try:
                subs = json.loads(raw) if isinstance(raw, str) and raw.strip() else []
            except (ValueError, TypeError):
                subs = []
            if isinstance(subs, list) and any(
                    (str(sb.get("asset_path") or sb.get("asset_hint") or "") in ps)
                    for sb in subs if isinstance(sb, dict)):
                hit += 1
        return hit

    def get_step_state(self):
        """向导五步的真实完成状态，返回 {step_index(int): bool}。"""
        import json
        try:
            raw = (self.get_settings() or {}).get("wizard_step_state", "")
            d = json.loads(raw) if raw else {}
            return {int(k): bool(v) for k, v in d.items() if str(k).isdigit()}
        except Exception:  # noqa: BLE001
            return {}

    def mark_step_done(self, idx, last_step=None):
        """把某一步标记为真的做过，并记录当前停在哪一步。"""
        import json
        st = self.get_step_state()
        st[int(idx)] = True
        self.set_setting("wizard_step_state", json.dumps({str(k): v for k, v in st.items()}))
        if last_step is not None:
            self.set_setting("wizard_last_step", str(int(last_step)))

    def get_last_step(self):
        try:
            return int((self.get_settings() or {}).get("wizard_last_step", "0"))
        except (ValueError, TypeError):
            return 0

    # ---------- 阶段三：语义分析 / 分镜匹配 ----------
    def get_score_calibration(self):
        """读取项目内的自动分校准模型（由人工评分拟合）。"""
        from .core import score_calibrator
        return score_calibrator.loads(
            (self.get_settings() or {}).get(score_calibrator.SETTINGS_KEY, ""))

    def save_score_calibration(self, model):
        from .core import score_calibrator
        self.set_setting(score_calibrator.SETTINGS_KEY, score_calibrator.dumps(model or {}))

    def count_human_ratings(self):
        """有合法人工分的片段数（用于界面进度）。"""
        n = 0
        for c in self.get_clips():
            hr = c.get("human_rating")
            if hr is None or hr == "":
                continue
            try:
                v = int(hr)
            except (TypeError, ValueError):
                continue
            if 0 <= v <= 100:
                n += 1
        return n

    def calibration_status(self):
        """界面用：校准是否生效、还差几条、一句话说明。"""
        from .core import score_calibrator
        return score_calibrator.status_summary(
            self.get_score_calibration(), self.count_human_ratings())

    def rebuild_score_calibration(self):
        """根据当前项目全部人工评分重建校准，并写回 settings。"""
        from .core import score_calibrator
        model = score_calibrator.rebuild_from_clips(self.get_clips())
        self.save_score_calibration(model)
        return model

    def save_human_rating(self, clip_id, rating, note="", mark_verified=False):
        """保存人工质量分（0–100），并重建校准模型。

        默认只写评分标签，不改核验/分析状态（避免「打分」与「已确认」语义混用）。
        mark_verified=True 时才顺带标已确认。
        返回 (ok, message, model)。选片用的 quality_total 会按新模型重算（若该片段已有六维分）。
        """
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            return False, "人工评分必须是 0–100 的整数", None
        if rating < 0 or rating > 100:
            return False, "人工评分必须在 0–100", None
        note = (note or "").strip()
        fields = {"human_rating": rating, "human_rating_note": note}
        if mark_verified:
            fields["verify_state"] = "已确认"
            fields["analysis_status"] = "已确认"
        self.update_clip(clip_id, **fields)
        model = self.rebuild_score_calibration()
        # 用新模型重算本片段（及其他已有六维分的片段）的校准总分，供选片使用
        self.reapply_calibration_to_clips(model)
        return True, model.get("message", "已保存人工评分"), model

    def reapply_calibration_to_clips(self, model=None):
        """对已有 quality_detail 的片段，用校准模型刷新 quality_total。

        只改 quality_total / 展示字段；quality_raw_total 一旦有合法值就绝不被校准分覆盖。
        """
        from .core import score_calibrator
        import json as _json
        model = model if model is not None else self.get_score_calibration()
        n = 0
        for c in self.get_clips():
            detail_raw = c.get("quality_detail") or ""
            if not detail_raw:
                continue
            try:
                detail = _json.loads(detail_raw) if isinstance(detail_raw, str) else detail_raw
            except Exception:
                continue
            scores = (detail or {}).get("scores") or {}
            if len(scores) < 6:
                continue
            # 只在「列缺失」(None) 时回退；0 是合法原始分，绝不当缺失
            raw = c.get("quality_raw_total")
            has_persisted_raw = raw is not None
            if not has_persisted_raw:
                try:
                    st = _json.loads(c.get("score_text") or "{}")
                    # 优先 score_text.raw_total；不要用可能已校准的 total 冒充原始分
                    if st.get("raw_total") is not None:
                        raw = st.get("raw_total")
                    elif (detail or {}).get("raw_total") is not None:
                        raw = (detail or {}).get("raw_total")
                    else:
                        # 旧数据无 raw：用六维分按默认权重重算，避免吃进已校准 total
                        from .core.quality_scorer import DEFAULT_WEIGHTS, DIMENSIONS as _DIMS
                        raw = round(sum(float(scores[d]) * DEFAULT_WEIGHTS[d]
                                        for d in _DIMS if d in scores))
                except Exception:
                    raw = 0
            try:
                raw_i = int(raw)
            except (TypeError, ValueError):
                raw_i = 0
            fake = {
                "ok": True,
                "scores": scores,
                "total": raw_i,
                "raw_total": raw_i,
                "grade": (detail or {}).get("grade", ""),
                "detail": (detail or {}).get("detail", {}),
                "disclaimer": "",
            }
            calibrated = score_calibrator.apply_calibration(fake, model)
            # 已有原始分则保持不动，只刷新校准后的 total
            new_raw = raw_i if has_persisted_raw else int(calibrated.get("raw_total", raw_i) or 0)
            self.update_clip(
                c["id"],
                quality_raw_total=new_raw,
                quality_total=int(calibrated.get("total", 0) or 0),
                quality_detail=_json.dumps({
                    "scores": scores,
                    "detail": (detail or {}).get("detail", {}),
                    "grade": calibrated.get("grade", ""),
                    "calibrated": bool(calibrated.get("calibrated")),
                    "calibration_meta": calibrated.get("calibration_meta", {}),
                    "raw_total": new_raw,
                }, ensure_ascii=False),
                score_text=_json.dumps(calibrated, ensure_ascii=False),
            )
            n += 1
        return n

    def save_semantic(self, clip_id, vision, quality):
        """把视觉线索与质量分写入一个候选片段。"""
        vm = dict(vision.get("metrics", {}))
        vm["review_reasons"] = vision.get("review_reasons", [])
        raw_total = quality.get("raw_total", quality.get("total", 0)) if quality.get("ok") else 0
        self.update_clip(clip_id,
                         vision_labels=__import__("json").dumps(vision.get("labels", []), ensure_ascii=False),
                         vision_metrics=__import__("json").dumps(vm, ensure_ascii=False),
                         quality_raw_total=raw_total,
                         quality_total=quality.get("total", 0) if quality.get("ok") else 0,
                         quality_detail=__import__("json").dumps(
                             {"scores": quality.get("scores", {}),
                              "detail": quality.get("detail", {}),
                              "grade": quality.get("grade", ""),
                              "calibrated": bool(quality.get("calibrated")),
                              "calibration_meta": quality.get("calibration_meta", {}),
                              "raw_total": raw_total}, ensure_ascii=False),
                         score_text=__import__("json").dumps(quality, ensure_ascii=False)
                         if quality.get("ok") else "",
                         semantic_status="已分析")

    def save_match_results(self, results):
        """把分镜匹配结果整体存入 settings（结构化 JSON，可追溯）。"""
        self.set_setting("match_results", __import__("json").dumps(results, ensure_ascii=False, default=str))

    def get_match_results(self):
        raw = self.get_settings().get("match_results", "")
        if not raw:
            return {}
        try:
            return __import__("json").loads(raw)
        except Exception:
            return {}

    def apply_match_selection(self, results):
        """将匹配结果的最佳候选写入分镜（跳过锁定/已确认镜头）。

        返回实际更新的分镜数。这是阶段四剪辑的输入基础。
        """
        updated = 0
        for sb_id_s, res in results.items():
            sb = self.get_storyboard(int(sb_id_s))
            if not sb or sb.get("locked") or sb.get("status") == "已确认":
                continue
            best = res.get("best")
            if not best:
                continue
            clip = best.get("clip") if isinstance(best, dict) else None
            if not clip or not clip.get("id"):
                continue
            self.update_storyboard(int(sb_id_s),
                                   selected_clip_id=clip["id"],
                                   status="候选待核验",
                                   note=res.get("reason", ""))
            updated += 1
        self.touch_modified()
        return updated

    def get_storyboard(self, sb_id):
        with self.connect() as c:
            r = c.execute("SELECT * FROM storyboards WHERE id=?", (sb_id,)).fetchone()
            return dict(r) if r else None

    # ---------- 阶段四：时间线（非破坏性编辑） ----------
    def rebuild_timeline_from_storyboards(self):
        """根据已确认/有选片的分镜建立初始时间线。

        只插入缺失的镜头，不覆盖用户已手动改过的镜头（lock/时间/素材）。
        返回新建镜头数。
        """
        made = 0
        with self.connect() as c:
            existing = {r["shot_id"] for r in c.execute("SELECT shot_id FROM timeline")}
            for sb in c.execute("SELECT * FROM storyboards ORDER BY seq"):
                shot_id = f"shot_{sb['id']}"
                if shot_id in existing:
                    continue
                clip_id = sb["selected_clip_id"]
                clip = c.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone() if clip_id else None
                asset = None
                if clip:
                    asset = c.execute("SELECT * FROM assets WHERE id=?", (clip["asset_id"],)).fetchone()
                c.execute(
                    "INSERT INTO timeline(shot_id,seq,storyboard_id,name,asset_path,asset_id,clip_id,"
                    "start_ms,end_ms,locked,thumb_path,duration_ms,target_ms,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (shot_id, sb["seq"], sb["id"], sb["name"],
                     asset["path"] if asset else "", asset["id"] if asset else None,
                     clip_id, clip["start_ms"] if clip else 0, clip["end_ms"] if clip else 0,
                     sb["locked"], (clip["keyframe_paths"] if clip else "") or "",
                     (clip["end_ms"] - clip["start_ms"]) if clip else 0,
                     sb["target_ms"] if "target_ms" in sb.keys() else 0, now()),
                )
                made += 1
        return made

    def apply_storyboard_targets(self, targets):
        """【阶段七】按模板默认节奏给分镜套建议时长。

        targets: {"分镜名": 毫秒}。只改分镜表；时间线镜头行的建议时长由用户在
        时间线页逐个改（改哪个只影响哪个）。只对当前为 0 的分镜生效，
        绝不覆盖用户已手动设过的值。返回套用数量。
        """
        if not targets:
            return 0
        n = 0
        with self.connect() as c:
            for row in c.execute("SELECT id,name,target_ms FROM storyboards"):
                t = targets.get(row["name"], 0)
                if t and not row["target_ms"]:
                    c.execute("UPDATE storyboards SET target_ms=? WHERE id=?", (int(t), row["id"]))
                    n += 1
        return n

    def get_timeline(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM timeline ORDER BY seq")]

    def get_shot(self, shot_id):
        with self.connect() as c:
            r = c.execute("SELECT * FROM timeline WHERE shot_id=?", (shot_id,)).fetchone()
            return dict(r) if r else None

    def update_shot(self, shot_id, **fields):
        """只更新单个镜头——非破坏性核心。locked 镜头默认拒绝修改。"""
        allowed = {"seq", "name", "asset_path", "asset_id", "clip_id", "start_ms",
                   "end_ms", "crop", "locked", "cache_key", "thumb_path", "duration_ms",
                   "sub_segments", "target_ms"}
        cur = self.get_shot(shot_id)
        if not cur:
            return False
        if cur.get("locked") and "locked" not in fields:
            return False  # 锁定镜头：非解锁操作一律拒绝
        sets = [k for k in fields if k in allowed]
        if not sets:
            return False
        vals = [fields[k] for k in sets]
        if "start_ms" in sets or "end_ms" in sets:
            s = fields.get("start_ms", cur["start_ms"])
            e = fields.get("end_ms", cur["end_ms"])
            if "duration_ms" not in sets:
                sets.append("duration_ms")
                vals.append(max(0, (e or 0) - (s or 0)))
        if "updated_at" not in sets:
            sets.append("updated_at")
            vals.append(now())
        with self.connect() as c:
            c.execute("UPDATE timeline SET " + ",".join(f"{k}=?" for k in sets) + " WHERE shot_id=?",
                      vals + [shot_id])
        return True

    def replace_shot_clip(self, shot_id, asset_path, asset_id, clip_id, start_ms, end_ms, thumb=""):
        """替换单个镜头的素材/选片（锁定镜头拒绝）。只动这一行。"""
        return self.update_shot(shot_id, asset_path=asset_path, asset_id=asset_id,
                                clip_id=clip_id, start_ms=start_ms, end_ms=end_ms,
                                thumb_path=thumb, cache_key="")  # cache_key 清空=强制重生成该镜头

    def set_linked_shot(self, shot_id, sub_segments, name=None):
        """把一个镜头设为联动镜头（一个镜头含多段真实画面，中间短叠化）。

        sub_segments: [{"asset_path","start_ms","end_ms"}, ...]
        仍只更新这一行——不破坏"改一个镜头不影响其它镜头"。locked 镜头拒绝。
        """
        payload = json.dumps(sub_segments, ensure_ascii=False)
        fields = {"sub_segments": payload, "cache_key": ""}
        if name:
            fields["name"] = name
        return self.update_shot(shot_id, **fields)

    def reorder_timeline(self, id_seq):
        """调整镜头顺序：只更新 seq 字段，素材引用/时间/锁定全部不动。"""
        with self.connect() as c:
            for seq, sid in enumerate(id_seq, 1):
                c.execute("UPDATE timeline SET seq=? WHERE shot_id=?", (seq, sid))

    def delete_shot(self, shot_id):
        with self.connect() as c:
            c.execute("DELETE FROM timeline WHERE shot_id=?", (shot_id,))

    def clear_timeline(self):
        with self.connect() as c:
            c.execute("DELETE FROM timeline")

    # ---------- 阶段五：文案/配音/字幕/音乐 ----------
    def save_narration_lines(self, lines):
        """整组替换文案行（生成文案时调用；用户编辑用 update_narration_line 单句改）。"""
        with self.connect() as c:
            c.execute("DELETE FROM narration_lines")
            for i, ln in enumerate(lines, 1):
                c.execute("""INSERT INTO narration_lines
                    (line_id,seq,shot_id,name,text,start_ms,end_ms,voice_path,source,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (ln.get("line_id") or f"L{i:03d}", i, ln.get("shot_id", ""),
                     ln.get("name", ""), ln.get("text", ""), ln.get("start_ms", 0),
                     ln.get("end_ms", 0), ln.get("voice_path", ""),
                     ln.get("source", "template"), now()))

    def get_narration_lines(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM narration_lines ORDER BY seq")]

    def update_narration_line(self, line_id, **fields):
        allowed = {"text", "start_ms", "end_ms", "voice_path", "seq"}
        sets = [k for k in fields if k in allowed]
        if not sets:
            return False
        with self.connect() as c:
            c.execute("UPDATE narration_lines SET " + ",".join(f"{k}=?" for k in sets) +
                      ",updated_at=? WHERE line_id=?",
                      [fields[k] for k in sets] + [now(), line_id])
        return True

    def get_narration_line(self, line_id):
        with self.connect() as c:
            r = c.execute("SELECT * FROM narration_lines WHERE line_id=?", (line_id,)).fetchone()
            return dict(r) if r else None

    def set_product_info(self, info_dict):
        self.set_setting("product_info", json.dumps(info_dict, ensure_ascii=False))

    def get_product_info(self):
        raw = self.get_settings().get("product_info", "")
        try:
            return json.loads(raw) if raw else {}
        except Exception:  # noqa: BLE001
            return {}

    def set_audio_settings(self, settings_dict):
        self.set_setting("audio_settings", json.dumps(settings_dict, ensure_ascii=False))

    def get_audio_settings(self):
        raw = self.get_settings().get("audio_settings", "")
        try:
            return json.loads(raw) if raw else {}
        except Exception:  # noqa: BLE001
            return {}
