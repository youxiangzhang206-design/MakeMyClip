# -*- coding: utf-8 -*-
"""主窗口：五个页面的容器与协调。

页面：项目主页 / 素材库 / 视频预览 / 分镜工作台 / 导出设置
"""
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QPixmap, QIcon, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QStackedWidget, QFileDialog, QMessageBox,
    QProgressBar, QAbstractItemView, QHeaderView, QCheckBox, QSpinBox,
    QGroupBox, QFormLayout, QTextEdit, QToolBar, QStatusBar, QMenu, QInputDialog,
)

from .. import config, db as dbmod, media
from ..core import duplicate_detector, storyboard_matcher
from ..core.quality_scorer import format_scores
from ..workers import ImportWorker, AnalyzeWorker, SemanticWorker, RenderWorker, TTSWorker, FinalExportWorker
from .player import PlayerWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = None            # 当前 ProjectDB
        self.project_path = None
        self.assets_cache = []
        self.import_worker = None
        self.setWindowTitle(f"{config.APP_NAME}  v{config.APP_VERSION}  —  {config.STAGE}")
        self.resize(1280, 800)
        self._build_ui()
        self._refresh_recent()

    # ==================== 界面搭建 ====================
    def _build_ui(self):
        # 左侧导航
        nav = QListWidget()
        nav.setFixedWidth(150)
        nav.setStyleSheet("QListWidget{background:#fafafa;font-size:14px;}"
                          "QListWidget::item{padding:10px;}")
        for name in ["项目主页", "素材库", "素材分析", "视频预览", "分镜工作台", "时间线", "音频字幕", "导出设置"]:
            nav.addItem(name)
        nav.currentRowChanged.connect(self._switch_page)
        self.nav = nav

        self.stack = QStackedWidget()
        self.page_home = self._make_home()
        self.page_library = self._make_library()
        self.page_analysis = self._make_analysis()
        self.page_preview = self._make_preview()
        self.page_story = self._make_storyboard()
        self.page_timeline = self._make_timeline()
        self.page_audio = self._make_audio()
        self.page_export = self._make_export()
        for p in (self.page_home, self.page_library, self.page_analysis,
                  self.page_preview, self.page_story, self.page_timeline,
                  self.page_audio, self.page_export):
            self.stack.addWidget(p)

        body = QHBoxLayout()
        body.addWidget(nav)
        body.addWidget(self.stack, 1)
        central = QWidget()
        central.setLayout(body)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪 — 请先新建或打开项目")
        self.nav.setCurrentRow(0)

    def _switch_page(self, row):
        self.stack.setCurrentIndex(row)
        if row == 1:
            self.refresh_assets()
        elif row == 2:
            self.refresh_analysis()
        elif row == 4:
            self.refresh_storyboards()
        elif row == 5:
            self.refresh_timeline()
        elif row == 6:
            self.refresh_audio_page()

    # ---------- 项目主页 ----------
    def _make_home(self):
        w = QWidget()
        v = QVBoxLayout(w)
        title = QLabel("AI 家具电商视频自动剪辑系统")
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        v.addWidget(title)
        sub = QLabel("第一阶段 · 基础工作台 — 导入 / 预览 / 分镜 / 保存（视频识别与自动剪辑将在后续阶段实现）")
        sub.setStyleSheet("color:#666;")
        v.addWidget(sub)

        btns = QHBoxLayout()
        b_new = QPushButton("新建项目")
        b_open = QPushButton("打开项目")
        b_new.setFixedHeight(40); b_open.setFixedHeight(40)
        b_new.clicked.connect(self.new_project)
        b_open.clicked.connect(self.open_project)
        btns.addWidget(b_new); btns.addWidget(b_open); btns.addStretch()
        v.addLayout(btns)

        v.addWidget(QLabel("最近项目："))
        self.recent_list = QListWidget()
        self.recent_list.itemDoubleClicked.connect(self._open_recent)
        v.addWidget(self.recent_list, 1)

        self.home_info = QLabel("当前项目：未打开")
        self.home_info.setStyleSheet("color:#0a6;padding:8px;background:#eef8f4;")
        v.addWidget(self.home_info)
        return w

    # ---------- 素材库 ----------
    def _make_library(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(QLabel("搜索："))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("按文件名搜索")
        self.search_box.textChanged.connect(self.refresh_assets)
        h.addWidget(self.search_box, 1)
        b_import_files = QPushButton("导入视频")
        b_import_folder = QPushButton("导入文件夹")
        b_import_files.clicked.connect(self.import_files)
        b_import_folder.clicked.connect(self.import_folder)
        h.addWidget(b_import_files); h.addWidget(b_import_folder)
        v.addLayout(h)

        split = QSplitter(Qt.Horizontal)
        self.asset_table = QTableWidget()
        self.asset_table.setColumnCount(9)
        self.asset_table.setHorizontalHeaderLabels(
            ["文件名", "时长", "分辨率", "帧率", "编码", "音轨", "识别状态", "解码", "路径"])
        self.asset_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.asset_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.asset_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.asset_table.doubleClicked.connect(self._asset_double_click)
        self.asset_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.asset_table.customContextMenuRequested.connect(self._asset_context_menu)
        split.addWidget(self.asset_table)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("缩略图预览："))
        self.asset_thumb = QLabel("—")
        self.asset_thumb.setAlignment(Qt.AlignCenter)
        self.asset_thumb.setMinimumSize(320, 200)
        self.asset_thumb.setStyleSheet("background:#222;color:#888;")
        self.asset_thumb.setScaledContents(True)
        rv.addWidget(self.asset_thumb)
        b_prev = QPushButton("在预览区播放此素材")
        b_prev.clicked.connect(self._preview_selected_asset)
        rv.addWidget(b_prev)
        rv.addStretch()
        split.addWidget(right)
        split.setStretchFactor(0, 3); split.setStretchFactor(1, 1)
        v.addWidget(split, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        return w

    # ---------- 素材分析（阶段二） ----------
    def _make_analysis(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(QLabel("AI 素材分析：把每条视频按画面内容拆成多个候选镜头（非等分）"))
        h.addStretch()
        self.btn_analyze = QPushButton("▶ 开始分析素材")
        self.btn_analyze.clicked.connect(self.start_analysis)
        self.btn_cancel_analysis = QPushButton("■ 取消")
        self.btn_cancel_analysis.setEnabled(False)
        self.btn_dedup = QPushButton("检测重复素材")
        self.btn_dedup.clicked.connect(self.start_dedup)
        self.btn_semantic = QPushButton("▶ AI 识别+评分")
        self.btn_semantic.clicked.connect(self.start_semantic)
        h.addWidget(self.btn_analyze); h.addWidget(self.btn_cancel_analysis)
        h.addWidget(self.btn_semantic); h.addWidget(self.btn_dedup)
        v.addLayout(h)

        self.analysis_progress = QProgressBar()
        self.analysis_progress.setVisible(False)
        v.addWidget(self.analysis_progress)
        self.semantic_note = QLabel(
            "说明：AI 识别为本地图像信号启发式（不联网、不臆造），只报告画面可测的线索并标置信度；"
            "不能确认的语义（拉手/灯罩/材质/完整动作）需人工核验。"
            "实用建议：先识别，再对关键镜头点「好片/一般/差片」，满 3 条后自动分会跟着你的偏好走。")
        self.semantic_note.setWordWrap(True)
        self.semantic_note.setStyleSheet("color:#666;font-size:11px;padding:4px;")
        v.addWidget(self.semantic_note)
        self.calib_status = QLabel("人工校准：打开项目后显示进度")
        self.calib_status.setWordWrap(True)
        self.calib_status.setStyleSheet(
            "color:#333;font-size:12px;padding:6px 8px;background:#f0f4f8;border:1px solid #d0d7de;")
        v.addWidget(self.calib_status)

        split = QSplitter(Qt.Horizontal)
        # 左：候选片段列表
        self.clip_table = QTableWidget()
        self.clip_table.setColumnCount(10)
        self.clip_table.setHorizontalHeaderLabels(
            ["来源素材", "镜头#", "开始", "结束", "时长", "置信度", "视觉线索",
             "质量分", "人工分", "核验状态/切镜原因"])
        self.clip_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.clip_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.clip_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.clip_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        self.clip_table.itemClicked.connect(self._clip_selected)
        split.addWidget(self.clip_table)

        # 右：片段预览 + 时间调整
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("候选片段预览："))
        self.clip_thumb = QLabel("—")
        self.clip_thumb.setAlignment(Qt.AlignCenter)
        self.clip_thumb.setMinimumSize(320, 180)
        self.clip_thumb.setStyleSheet("background:#222;color:#888;")
        self.clip_thumb.setScaledContents(True)
        rv.addWidget(self.clip_thumb)
        b_play_clip = QPushButton("在预览区播放此片段")
        b_play_clip.clicked.connect(self._play_selected_clip)
        rv.addWidget(b_play_clip)

        form = QFormLayout()
        self.clip_start = QLineEdit(); self.clip_end = QLineEdit()
        self.clip_note = QLineEdit(); self.clip_note.setPlaceholderText("人工备注/修正说明")
        self.clip_human_rating = QSpinBox()
        self.clip_human_rating.setRange(0, 100)
        self.clip_human_rating.setValue(70)
        self.clip_human_rating.setToolTip(
            "人工质量分 0–100。也可直接点下方「好片/一般/差片」一键打分。")
        b_apply_time = QPushButton("应用起止时间(秒)")
        b_apply_time.clicked.connect(self._apply_clip_time)
        form.addRow("开始(秒):", self.clip_start)
        form.addRow("结束(秒):", self.clip_end)
        form.addRow("备注:", self.clip_note)
        form.addRow("人工评分:", self.clip_human_rating)
        rv.addLayout(form)
        rv.addWidget(b_apply_time)
        # 快捷评分：实操比拖数字快，直接驱动校准
        from ..core import score_calibrator as _sc
        quick = QHBoxLayout()
        quick.addWidget(QLabel("快捷："))
        for label, score in _sc.QUICK_RATINGS:
            btn = QPushButton(f"{label}({score})")
            btn.setToolTip(f"本片段记 {score} 分并立即校准自动分")
            btn.clicked.connect(lambda _=False, s=score: self._quick_human_rating(s))
            quick.addWidget(btn)
        quick.addStretch()
        rv.addLayout(quick)
        self.btn_save_rating = QPushButton("保存人工评分并校准自动分")
        self.btn_save_rating.setToolTip("把本片段的人工分当标签；样本≥3 后自动分会校准，并刷新选片用的质量分")
        self.btn_save_rating.clicked.connect(self._save_human_rating)
        rv.addWidget(self.btn_save_rating)
        b_verify = QPushButton("标记为已核验")
        b_verify.clicked.connect(self._verify_clip)
        b_reject = QPushButton("标记为需要人工选择")
        b_reject.clicked.connect(lambda: self._set_clip_status("需要人工选择"))
        rv.addWidget(b_verify); rv.addWidget(b_reject)
        rv.addWidget(QLabel("视觉线索与质量明细："))
        self.clip_detail = QTextEdit()
        self.clip_detail.setReadOnly(True)
        self.clip_detail.setMaximumHeight(150)
        self.clip_detail.setPlaceholderText("选中片段后，点『AI 识别+评分』查看识别线索与各项质量分")
        rv.addWidget(self.clip_detail)
        rv.addStretch()
        split.addWidget(right)
        split.setStretchFactor(0, 3); split.setStretchFactor(1, 1)
        v.addWidget(split, 1)

        self.dedup_result = QLabel("重复检测：尚未运行")
        self.dedup_result.setWordWrap(True)
        self.dedup_result.setStyleSheet("color:#555;padding:6px;background:#f6f6f6;")
        v.addWidget(self.dedup_result)
        return w

    def refresh_analysis(self):
        if not self.db:
            self.clip_table.setRowCount(0)
            self._refresh_calib_status()
            return
        assets = {a["id"]: a for a in self.db.get_assets()}
        clips = self.db.get_clips()
        # 只显示自动切镜产生的片段
        clips = [c for c in clips if c.get("source") == "auto_scene"]
        self.clips_cache = clips
        t = self.clip_table
        t.setRowCount(len(clips))
        for row, c in enumerate(clips):
            a = assets.get(c["asset_id"], {})
            dur = (c["end_ms"] - c["start_ms"]) / 1000.0
            status = c.get("analysis_status") or c.get("verify_state", "")
            issue = c.get("cut_reason") or ""
            display = f"{status} | {issue}"
            # 视觉线索：取建议标签 + 命中 cue 的中文名
            try:
                vlabels = json.loads(c.get("vision_labels") or "[]")
                cue_names = {"color_temperature": "色温切换", "light_switch": "灯光切换",
                             "light_states": "灯光状态", "exposure_only": "疑似曝光变化",
                             "motion": "运动", "reciprocating": "往复动作", "hand": "疑似手部",
                             "subject_close": "近景", "subject_whole": "整体视角"}
                cues_text = "/".join(cue_names.get(l.get("code"), l.get("code"))
                                     for l in vlabels if l.get("code"))[:40]
            except Exception:
                cues_text = ""
            q_total = c.get("quality_total")
            if q_total is None:
                quality_text = "—"
            else:
                quality_text = str(q_total)
            hr = c.get("human_rating")
            human_text = "—" if hr is None or hr == "" else str(hr)
            vals = [
                a.get("filename", "?"), str(c.get("shot_index", "")),
                f'{c["start_ms"]/1000:.1f}', f'{c["end_ms"]/1000:.1f}',
                f"{dur:.1f}", f'{c.get("confidence",0):.2f}',
                cues_text, quality_text, human_text, display,
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if c.get("confidence", 0) < 0.5 and col == 5:
                    item.setForeground(Qt.red)  # 低置信度=疑似灯光变化，需核验
                if col == 7 and q_total is not None:
                    try:
                        qt = int(q_total)
                        if qt < 60:
                            item.setForeground(Qt.red)
                        elif qt >= 80:
                            item.setForeground(Qt.darkGreen)
                    except (TypeError, ValueError):
                        pass
                if col == 8 and hr is not None and hr != "":
                    item.setForeground(Qt.darkBlue)
                t.setItem(row, col, item)
        self._refresh_calib_status()

    def _refresh_calib_status(self):
        lab = getattr(self, "calib_status", None)
        if lab is None:
            return
        if not self.db:
            lab.setText("人工校准：未打开项目")
            return
        try:
            st = self.db.calibration_status()
            lab.setText(st.get("line") or "")
            if st.get("ok"):
                lab.setStyleSheet(
                    "color:#0b3d0b;font-size:12px;padding:6px 8px;"
                    "background:#e8f5e9;border:1px solid #a5d6a7;")
            elif st.get("n_labeled", 0) > 0:
                lab.setStyleSheet(
                    "color:#5c4a00;font-size:12px;padding:6px 8px;"
                    "background:#fff8e1;border:1px solid #ffe082;")
            else:
                lab.setStyleSheet(
                    "color:#333;font-size:12px;padding:6px 8px;"
                    "background:#f0f4f8;border:1px solid #d0d7de;")
        except Exception as e:  # noqa: BLE001
            lab.setText(f"人工校准：状态读取失败（{e}）")

    def start_analysis(self):
        if not self._require_db():
            return
        assets = [a for a in self.db.get_assets() if a.get("decode_ok")]
        if not assets:
            QMessageBox.information(self, "提示", "请先导入视频素材")
            return
        items = [(a["id"], a["path"], a["duration_ms"]) for a in assets]
        self.btn_analyze.setEnabled(False)
        self.btn_cancel_analysis.setEnabled(True)
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setRange(0, len(items))
        task_id = self.db.add_task("镜头分析", input_file=f"{len(items)} 个素材")
        w = AnalyzeWorker(items, do_dedup=False, parent=self)
        self.analyze_worker = w

        def on_prog(cur, total, name):
            self.analysis_progress.setValue(cur)
            self.status.showMessage(f"分析镜头 {cur}/{total}：{name}")

        def on_file(asset_id, payload):
            result, kf_map = payload
            if result is None:
                self.status.showMessage(f"素材分析失败：{kf_map.get('error','')}")
                return
            self.db.save_scene_analysis(asset_id, result, kf_map)

        def on_done(ok, fail):
            self.analysis_progress.setVisible(False)
            self.btn_analyze.setEnabled(True)
            self.btn_cancel_analysis.setEnabled(False)
            self.db.finish_task(task_id, "成功" if fail == 0 else "部分失败",
                                f"成功 {ok}，失败 {fail}")
            self.refresh_analysis()
            self.refresh_assets()
            self.status.showMessage(f"分析完成：成功 {ok}，失败 {fail}")
            self.analyze_worker = None

        w.progress.connect(on_prog)
        w.file_done.connect(on_file)
        w.all_done.connect(on_done)
        w.start()

    def start_dedup(self):
        if not self._require_db():
            return
        assets = self.db.get_assets()
        if len(assets) < 2:
            QMessageBox.information(self, "提示", "至少需要 2 条素材才能检测重复")
            return
        self.status.showMessage("正在检测重复素材…")
        groups = duplicate_detector.detect(assets)
        self.db.save_duplicate_groups(groups)
        exact = len(groups["exact_groups"])
        similar = len(groups["similar_groups"])
        self.dedup_result.setText(
            f"重复检测完成：完全重复 {exact} 组，画面相似 {similar} 组。"
            f"（原始文件均已保留，未删除。分组结果已保存到项目）")
        self.status.showMessage("重复检测完成")

    def start_semantic(self):
        """对已切分出的候选片段做视觉识别 + 质量评分（后台、可取消）。"""
        if not self._require_db():
            return
        clips = [c for c in self.db.get_clips() if c.get("source") == "auto_scene"]
        if not clips:
            QMessageBox.information(self, "提示", "请先在『素材分析』做镜头切分，再运行 AI 识别")
            return
        assets = {a["id"]: a for a in self.db.get_assets()}
        items = []
        for c in clips:
            a = assets.get(c["asset_id"])
            if a and a.get("decode_ok"):
                items.append((c["id"], a["path"], c["start_ms"], c["end_ms"]))
        if not items:
            QMessageBox.information(self, "提示", "没有可识别的有效片段")
            return
        self.btn_semantic.setEnabled(False)
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setRange(0, len(items))
        task_id = self.db.add_task("AI识别评分", input_file=f"{len(items)} 个片段")
        # 已有人工评分时，用校准模型让自动分贴近人的偏好；
        # holder 可在评分中途被人工评分回调刷新，避免整批都用陈旧快照
        calib = self.db.get_score_calibration()
        self._calib_holder = {"model": calib}
        w = SemanticWorker(items, calibration=calib,
                           calibration_holder=self._calib_holder, parent=self)
        self.semantic_worker = w

        def on_prog(cur, total, name):
            self.analysis_progress.setValue(cur)
            self.status.showMessage(f"AI 识别评分 {cur}/{total}：{name}")

        def on_one(cid, vision, quality):
            self.db.save_semantic(cid, vision, quality)

        def on_done(ok, fail):
            self.analysis_progress.setVisible(False)
            self.btn_semantic.setEnabled(True)
            self.db.finish_task(task_id, "成功" if fail == 0 else "部分失败",
                                f"成功 {ok}，失败 {fail}")
            self.refresh_analysis()
            self.status.showMessage(f"AI 识别评分完成：成功 {ok}，失败 {fail}")
            self.semantic_worker = None

        w.progress.connect(on_prog)
        w.one_done.connect(on_one)
        w.all_done.connect(on_done)
        w.start()

    def _clip_selected(self, index):
        row = index.row()
        if not hasattr(self, "clips_cache") or row >= len(self.clips_cache):
            return
        c = self.clips_cache[row]
        self._current_clip_id = c["id"]
        assets = {a["id"]: a for a in self.db.get_assets()}
        a = assets.get(c["asset_id"], {})
        self.clip_start.setText(f'{c["start_ms"]/1000:.2f}')
        self.clip_end.setText(f'{c["end_ms"]/1000:.2f}')
        self.clip_note.setText(c.get("potential_issue") or "")
        # 取第一张关键帧显示
        try:
            kfs = json.loads(c.get("keyframe_paths") or "[]")
            if kfs and Path(kfs[0]).exists():
                self.clip_thumb.setPixmap(QPixmap(kfs[0]))
            else:
                self.clip_thumb.setText("无关键帧")
        except Exception:
            self.clip_thumb.setText("无关键帧")
        # 视觉线索 + 质量明细
        lines = []
        try:
            vlabels = json.loads(c.get("vision_labels") or "[]")
            if vlabels:
                lines.append("【视觉线索】")
                for l in vlabels:
                    mark = "⚠需核验" if l.get("need_review") else ""
                    lines.append(f'  · {l.get("name")}（置信{l.get("confidence")}）{mark}')
                    if l.get("evidence"):
                        lines.append(f'      依据：{l.get("evidence")}')
        except Exception:
            pass
        if c.get("quality_total") is not None:
            try:
                st = json.loads(c.get("score_text") or "{}")
                if st:
                    lines.append(f'【质量】{format_scores(st)}')
                else:
                    lines.append(f'【质量】总分{c.get("quality_total")}')
            except Exception:
                lines.append(f'【质量】总分{c.get("quality_total")}')
            raw_q = c.get("quality_raw_total")
            if raw_q is not None and raw_q != c.get("quality_total"):
                lines.append(f'  校准前启发式总分：{raw_q}')
        if c.get("human_rating") is not None:
            note = c.get("human_rating_note") or ""
            lines.append(f'【人工评分】{c.get("human_rating")}' + (f' — {note}' if note else ""))
        try:
            review = json.loads(c.get("vision_metrics") or "{}").get("review_reasons", [])
            if review:
                lines.append("【需人工核验】")
                for r in review:
                    lines.append(f"  · {r}")
        except Exception:
            pass
        # 切换片段时同步 SpinBox：有人分用其人分，否则回到默认 70，避免误把上一条分存到下一条
        try:
            hr = c.get("human_rating")
            if hr is not None and hr != "":
                self.clip_human_rating.setValue(int(hr))
            else:
                self.clip_human_rating.setValue(70)
        except Exception:
            self.clip_human_rating.setValue(70)
        self.clip_detail.setPlainText("\n".join(lines) if lines else "尚未做 AI 识别，点上方『AI 识别+评分』")

    def _play_selected_clip(self):
        cid = getattr(self, "_current_clip_id", None)
        if not cid or not hasattr(self, "clips_cache"):
            return
        c = next((x for x in self.clips_cache if x["id"] == cid), None)
        if not c:
            return
        assets = {a["id"]: a for a in self.db.get_assets()}
        a = assets.get(c["asset_id"], {})
        if not a.get("decode_ok"):
            return
        self.nav.setCurrentRow(3)
        if self.player.open(a["path"], c["start_ms"], c["end_ms"]):
            self.player.player.play()
            self.status.showMessage(f"预览片段：{a.get('filename')} {c['start_ms']/1000:.1f}-{c['end_ms']/1000:.1f}s")

    def _apply_clip_time(self):
        cid = getattr(self, "_current_clip_id", None)
        if not cid:
            QMessageBox.information(self, "提示", "请先选择一个片段")
            return
        try:
            s = float(self.clip_start.text())
            e = float(self.clip_end.text())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "起止时间必须是数字（秒）")
            return
        if e <= s:
            QMessageBox.warning(self, "输入错误", "结束时间必须大于开始时间")
            return
        self.db.update_clip(cid, start_ms=int(s * 1000), end_ms=int(e * 1000),
                            analysis_status="已人工修正")
        self.db.touch_modified()
        self.refresh_analysis()
        self.status.showMessage(f"片段时间已修正：{s:.2f}-{e:.2f}s")

    def _verify_clip(self):
        self._set_clip_status("已确认")

    def _quick_human_rating(self, score: int):
        """一键好片/一般/差片：写入 SpinBox 后走统一保存逻辑。"""
        self.clip_human_rating.setValue(int(score))
        self._save_human_rating(ask_rematch=True)

    def _save_human_rating(self, ask_rematch=True):
        """把当前片段的人工分写入项目，并重建自动分校准。"""
        cid = getattr(self, "_current_clip_id", None)
        if not cid:
            QMessageBox.information(self, "提示", "请先选择一个片段")
            return
        if not self._require_db():
            return
        # 防连点：重建校准可能扫全库，主线程执行时先禁用按钮
        b_rate = getattr(self, "btn_save_rating", None)
        if b_rate is not None:
            if not b_rate.isEnabled():
                return
            b_rate.setEnabled(False)
        just_became_ready = False
        try:
            rating = int(self.clip_human_rating.value())
            note = self.clip_note.text().strip()
            before_ok = bool((self.db.get_score_calibration() or {}).get("ok"))
            ok, msg, model = self.db.save_human_rating(cid, rating, note)
            if not ok:
                QMessageBox.warning(self, "保存失败", msg)
                return
            after_ok = bool((model or {}).get("ok"))
            just_became_ready = (not before_ok) and after_ok
            # 若 AI 评分线程还在跑，刷新其校准快照
            holder = getattr(self, "_calib_holder", None)
            if isinstance(holder, dict):
                holder["model"] = model
            self.db.touch_modified()
            self.refresh_analysis()
            # 重新选中当前行以刷新右侧明细（人工分 / 校准前后总分）
            t = getattr(self, "clip_table", None)
            if t is not None and hasattr(self, "clips_cache"):
                for row, c in enumerate(self.clips_cache):
                    if c.get("id") == cid:
                        t.selectRow(row)
                        self._clip_selected(t.model().index(row, 0))
                        break
            n = (model or {}).get("n_samples", 0)
            calibrated = bool((model or {}).get("ok"))
            tip = msg
            if calibrated:
                tip += f"（已用 {n} 条人工分校准自动评分）"
            else:
                tip += f"（已攒 {n} 条，满 3 条后开始校准）"
            self.status.showMessage(tip)
            # 校准刚生效或用户主动保存后：闭环问是否重跑选片（实用性关键）
            do_rematch = False
            if ask_rematch and calibrated:
                prompt = tip + "\n\n是否按新质量分重新自动选片？（锁定/已确认镜头不会被改）"
                if just_became_ready:
                    prompt = "校准已生效！\n\n" + prompt
                ans = QMessageBox.question(
                    self, "人工评分已保存", prompt,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                do_rematch = ans == QMessageBox.Yes
            else:
                QMessageBox.information(self, "人工评分已保存", tip)
            if do_rematch:
                self.auto_match_storyboards()
        finally:
            if b_rate is not None:
                b_rate.setEnabled(True)

    def _set_clip_status(self, status):
        cid = getattr(self, "_current_clip_id", None)
        if not cid:
            QMessageBox.information(self, "提示", "请先选择一个片段")
            return
        fields = {"analysis_status": status}
        if self.clip_note.text().strip():
            fields["potential_issue"] = self.clip_note.text().strip()
        self.db.update_clip(cid, **fields)
        self.db.touch_modified()
        self.refresh_analysis()
        self.status.showMessage(f"片段状态已更新：{status}")

    # ---------- 视频预览 ----------
    def _make_preview(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.player = PlayerWidget()
        v.addWidget(self.player, 1)
        self.preview_hint = QLabel("提示：双击素材库中的视频，或点击“在预览区播放此素材”。支持播放/暂停/拖动进度/逐帧。")
        self.preview_hint.setStyleSheet("color:#666;")
        v.addWidget(self.preview_hint)
        return w

    # ---------- 分镜工作台 ----------
    def _make_storyboard(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(QLabel("分镜列表（首版默认 12 个模板，可增删改）："))
        h.addStretch()
        b_add = QPushButton("新增分镜")
        b_del = QPushButton("删除分镜")
        b_up = QPushButton("上移")
        b_down = QPushButton("下移")
        b_match = QPushButton("▶ 自动匹配分镜")
        b_match.clicked.connect(self.auto_match_storyboards)
        self.btn_match = b_match
        b_apply = QPushButton("应用最佳候选到分镜")
        b_apply.clicked.connect(self.apply_match_selection)
        b_add.clicked.connect(self.add_storyboard)
        b_del.clicked.connect(self.delete_storyboard)
        b_up.clicked.connect(lambda: self.move_storyboard(-1))
        b_down.clicked.connect(lambda: self.move_storyboard(1))
        for b in (b_add, b_del, b_up, b_down, b_match, b_apply):
            h.addWidget(b)
        v.addLayout(h)
        self.match_hint = QLabel(
            "匹配规则见 templates/furniture/nightstand.json（可改）。内容准确性优先于画质；"
            "证据不足标『候选待核验』；无合格素材显示『缺少素材，暂不展示』；锁定镜头不被覆盖。")
        self.match_hint.setWordWrap(True)
        self.match_hint.setStyleSheet("color:#666;font-size:11px;")
        v.addWidget(self.match_hint)

        self.story_list = QListWidget()
        self.story_list.setSpacing(4)
        self.story_list.itemClicked.connect(self._story_selected)
        v.addWidget(self.story_list, 1)

        detail = QGroupBox("当前分镜详情")
        form = QFormLayout(detail)
        self.sb_name = QLineEdit(); self.sb_name.editingFinished.connect(self._save_story_detail)
        self.sb_status = QComboBox(); self.sb_status.addItems(config.ANALYSIS_STATES)
        self.sb_status.currentTextChanged.connect(self._save_story_detail)
        self.sb_lock = QCheckBox("锁定此镜头（锁定时自动选片不得覆盖）")
        self.sb_lock.stateChanged.connect(self._save_story_detail)
        self.sb_note = QTextEdit(); self.sb_note.setMaximumHeight(80)
        self.sb_note.textChanged.connect(self._save_story_detail)
        form.addRow("名称：", self.sb_name)
        form.addRow("识别状态：", self.sb_status)
        form.addRow("", self.sb_lock)
        form.addRow("备注：", self.sb_note)
        self.sb_detail = detail
        detail.setVisible(False)
        v.addWidget(detail)

        # 候选片段与推荐理由（阶段三）
        cand_box = QGroupBox("AI 候选片段与推荐理由")
        cv = QVBoxLayout(cand_box)
        self.cand_list = QListWidget()
        self.cand_list.setMaximumHeight(120)
        self.cand_list.itemClicked.connect(self._candidate_selected)
        cv.addWidget(self.cand_list)
        self.cand_reason = QTextEdit()
        self.cand_reason.setReadOnly(True)
        self.cand_reason.setMaximumHeight(90)
        self.cand_reason.setPlaceholderText("点『自动匹配分镜』后，这里显示每个分镜的最佳候选、备选与推荐理由")
        cv.addWidget(self.cand_reason)
        self.sb_cand_box = cand_box
        cand_box.setVisible(False)
        v.addWidget(cand_box)
        return w

    # ---------- 时间线 / 单镜头编辑（阶段四） ----------
    def _make_timeline(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(QLabel("时间线：点镜头选中 → 单独替换/改时间/锁定/调序（只改这一个镜头）"))
        h.addStretch()
        b_build = QPushButton("① 从分镜建立时间线")
        b_build.clicked.connect(self.build_timeline)
        b_render = QPushButton("② 生成初版成片")
        b_render.clicked.connect(self.render_timeline)
        self.btn_render = b_render
        h.addWidget(b_build); h.addWidget(b_render)
        v.addLayout(h)

        self.render_progress = QProgressBar()
        self.render_progress.setVisible(False)
        v.addWidget(self.render_progress)

        # 镜头序列（横向卡片用列表模拟：每行一个镜头）
        self.shot_list = QListWidget()
        self.shot_list.setSpacing(3)
        self.shot_list.itemClicked.connect(self._shot_selected)
        v.addWidget(self.shot_list, 1)

        # 单镜头编辑区
        edit = QGroupBox("单镜头编辑（只影响选中镜头）")
        ev = QVBoxLayout(edit)
        form = QFormLayout()
        self.shot_start = QLineEdit(); self.shot_end = QLineEdit()
        b_apply = QPushButton("应用起止时间(秒)")
        b_apply.clicked.connect(self._apply_shot_time)
        form.addRow("开始(秒):", self.shot_start)
        form.addRow("结束(秒):", self.shot_end)
        # 【阶段七】建议时长：素材比它短->末帧定格补足；比它长->只取需要的部分
        self.shot_target = QSpinBox()
        self.shot_target.setRange(0, 30000); self.shot_target.setSingleStep(500)
        self.shot_target.setSuffix(" 毫秒")
        self.shot_target.setToolTip("0=不限(素材多长用多长)。核心卖点镜头设长些慢慢看，次要镜头设短些快速过。只影响这一个镜头。")
        form.addRow("建议时长(0=不限):", self.shot_target)
        ev.addLayout(form)
        b_tapply = QPushButton("应用建议时长")
        b_tapply.clicked.connect(self._apply_shot_target)
        self.btn_shot_target = b_tapply
        ev.addWidget(b_apply)
        ev.addWidget(b_tapply)
        bh = QHBoxLayout()
        b_replace = QPushButton("替换为其它候选…")
        b_replace.clicked.connect(self._replace_shot)
        b_lock = QPushButton("锁定/解锁")
        b_lock.clicked.connect(self._toggle_shot_lock)
        b_del = QPushButton("删除此镜头")
        b_del.clicked.connect(self._delete_shot)
        b_up = QPushButton("上移"); b_up.clicked.connect(lambda: self._move_shot(-1))
        b_down = QPushButton("下移"); b_down.clicked.connect(lambda: self._move_shot(1))
        self.shot_replace = b_replace; self.shot_lock_btn = b_lock
        for b in (b_replace, b_lock, b_del, b_up, b_down):
            bh.addWidget(b)
        ev.addLayout(bh)
        self.shot_info = QLabel("未选中镜头")
        self.shot_info.setWordWrap(True)
        ev.addWidget(self.shot_info)
        self.shot_edit = edit
        edit.setVisible(False)
        v.addWidget(edit)

        self.render_result = QTextEdit()
        self.render_result.setReadOnly(True)
        self.render_result.setMaximumHeight(120)
        self.render_result.setPlaceholderText("生成成片后：显示缓存命中/重渲染镜头数、成片校验结果")
        v.addWidget(self.render_result)
        return w

    def refresh_timeline(self):
        if not self.db:
            self.shot_list.clear()
            return
        shots = self.db.get_timeline()
        self.shots_cache = shots
        self.shot_list.clear()
        for i, s in enumerate(shots, 1):
            dur = s.get("duration_ms", 0) / 1000.0
            tgt = s.get("target_ms", 0) or 0
            tgt_s = f" →建议{tgt/1000:.1f}s" if tgt else ""
            lock = " 🔒" if s.get("locked") else ""
            src = Path(s.get("asset_path", "")).name if s.get("asset_path") else "（缺镜头）"
            cache = " 缓存✓" if s.get("cache_key") else ""
            self.shot_list.addItem(
                f"{i:02d}. {s.get('name','?')}　{src} "
                f"{s.get('start_ms',0)/1000:.1f}-{s.get('end_ms',0)/1000:.1f}s "
                f"({dur:.1f}s){tgt_s}{lock}{cache}")

    def build_timeline(self):
        if not self._require_db():
            return
        n = self.db.rebuild_timeline_from_storyboards()
        self.refresh_timeline()
        shots = self.db.get_timeline()
        empty = sum(1 for s in shots if not s.get("asset_path"))
        self.status.showMessage(
            f"时间线已建立：新建 {n} 个镜头，共 {len(shots)} 个；"
            f"其中 {empty} 个缺素材（不会编造，导出时跳过）")
        QMessageBox.information(self, "完成",
                                f"时间线共 {len(shots)} 个镜头，新建 {n} 个。\n"
                                f"缺素材镜头 {empty} 个（导出时跳过，不编造画面）。\n\n"
                                "现在可以逐个镜头替换/改时间/锁定，再点『生成初版成片』。")

    def _shot_selected(self, item):
        row = self.shot_list.row(item)
        if not hasattr(self, "shots_cache") or row >= len(self.shots_cache):
            return
        s = self.shots_cache[row]
        self._current_shot_id = s["shot_id"]
        self.shot_edit.setVisible(True)
        self.shot_start.blockSignals(True); self.shot_end.blockSignals(True)
        self.shot_start.setText(f'{s.get("start_ms",0)/1000:.2f}')
        self.shot_end.setText(f'{s.get("end_ms",0)/1000:.2f}')
        self.shot_target.setValue(int(s.get("target_ms", 0) or 0))
        self.shot_start.blockSignals(False); self.shot_end.blockSignals(False)
        src = Path(s.get("asset_path", "")).name if s.get("asset_path") else "（缺素材）"
        self.shot_info.setText(
            f'镜头：{s.get("name")}　素材：{src}\n'
            f'锁定：{"是 🔒" if s.get("locked") else "否"}　'
            f'缓存标识：{(s.get("cache_key") or "未生成")[:12]}')
        locked = bool(s.get("locked"))
        self.shot_start.setDisabled(locked); self.shot_end.setDisabled(locked)
        self.shot_target.setDisabled(locked); self.btn_shot_target.setDisabled(locked)
        self.shot_replace.setDisabled(locked)
        self.shot_lock_btn.setText("🔓 解锁" if locked else "🔒 锁定")

    def _apply_shot_time(self):
        sid = getattr(self, "_current_shot_id", None)
        if not sid:
            QMessageBox.information(self, "提示", "请先选中一个镜头")
            return
        try:
            s = float(self.shot_start.text()); e = float(self.shot_end.text())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "起止时间必须是数字（秒）")
            return
        if e <= s:
            QMessageBox.warning(self, "输入错误", "结束时间必须大于开始时间")
            return
        ok = self.db.update_shot(sid, start_ms=int(s*1000), end_ms=int(e*1000), cache_key="")
        if not ok:
            QMessageBox.warning(self, "已锁定", "该镜头已锁定，请先解锁再修改")
            return
        self.db.touch_modified()
        self.refresh_timeline()
        self.status.showMessage(f"仅镜头 {sid} 时间已改为 {s:.2f}-{e:.2f}s（其它镜头未动）")

    def _apply_shot_target(self):
        """【阶段七】改当前镜头的建议时长——只影响这一个镜头。"""
        sid = getattr(self, "_current_shot_id", None)
        if not sid:
            QMessageBox.information(self, "提示", "请先选中一个镜头")
            return
        ok = self.db.update_shot(sid, target_ms=int(self.shot_target.value()), cache_key="")
        if not ok:
            QMessageBox.warning(self, "已锁定", "该镜头已锁定，请先解锁再修改")
            return
        self.db.touch_modified()
        self.refresh_timeline()
        t = self.shot_target.value()
        self.status.showMessage(
            f"仅镜头 {sid} 建议时长已改为 {'不限(素材原长)' if not t else f'{t/1000:.1f}秒'}"
            "（其它镜头未动；下次生成成片只有此镜头重渲染）")

    def _toggle_shot_lock(self):
        sid = getattr(self, "_current_shot_id", None)
        if not sid:
            return
        cur = self.db.get_shot(sid)
        new_lock = 0 if cur.get("locked") else 1
        # update_shot 对锁定镜头只允许改 locked 字段本身（解锁），此处正好符合
        self.db.update_shot(sid, locked=new_lock)
        self.refresh_timeline()
        self._shot_selected(self.shot_list.currentItem())
        self.status.showMessage(f"镜头 {sid} 已{'锁定' if new_lock else '解锁'}")

    def _delete_shot(self):
        sid = getattr(self, "_current_shot_id", None)
        if not sid:
            return
        if self.db.get_shot(sid).get("locked"):
            QMessageBox.warning(self, "已锁定", "锁定镜头不能删除，请先解锁")
            return
        if QMessageBox.question(self, "确认", "删除该镜头？（不影响其它镜头）") == QMessageBox.Yes:
            self.db.delete_shot(sid)
            self.shot_edit.setVisible(False)
            self.refresh_timeline()
            self.status.showMessage(f"镜头 {sid} 已删除（其它镜头未动）")

    def _move_shot(self, delta):
        row = self.shot_list.currentRow()
        shots = self.db.get_timeline()
        new = row + delta
        if row < 0 or new < 0 or new >= len(shots):
            return
        ids = [s["shot_id"] for s in shots]
        ids[row], ids[new] = ids[new], ids[row]
        self.db.reorder_timeline(ids)   # 只改 seq，素材/时间/锁定全不动
        self.refresh_timeline()
        self.shot_list.setCurrentRow(new)
        self.status.showMessage("镜头顺序已调整（素材引用与时间均未改动）")

    def _replace_shot(self):
        """用该分镜的其它候选片段替换当前镜头（只改这一行）。"""
        sid = getattr(self, "_current_shot_id", None)
        if not sid:
            return
        shot = self.db.get_shot(sid)
        sb_id = shot.get("storyboard_id")
        cands = self.db.get_clips(sb_id) if sb_id else []
        cands = [c for c in cands if c.get("semantic_status") == "已分析" and c.get("quality_total")]
        if not cands:
            QMessageBox.information(self, "无候选",
                                    "该分镜没有已识别评分的候选片段。请先在素材分析页做 AI 识别。")
            return
        cands.sort(key=lambda c: -(c.get("quality_total") or 0))
        items = [f'{c["id"]}: {f"{c['start_ms']/1000:.1f}-{c['end_ms']/1000:.1f}s"} '
                 f'质量{c.get("quality_total")}' for c in cands]
        choice, ok = QInputDialog.getItem(self, "替换镜头", "选择候选片段：", items, 0, False)
        if not ok:
            return
        cid = int(choice.split(":")[0])
        c = next(x for x in cands if x["id"] == cid)
        a = self.db.get_asset(c["asset_id"])
        if not a:
            return
        # 只更新当前镜头
        self.db.replace_shot_clip(sid, a["path"], a["id"], cid, c["start_ms"], c["end_ms"])
        self.db.touch_modified()
        self.refresh_timeline()
        self._shot_selected(self.shot_list.currentItem())
        self.status.showMessage(f"镜头 {sid} 已替换为候选 {cid}（其它镜头未动，缓存仅该镜头失效）")

    def _export_params(self):
        # 【阶段十二】下拉里「3:4 竖版(淘宝商详推荐)」带说明文字，不是 EXPORT_SIZES 的键，
        # 以前会静默回落到 16:9 横屏；这里统一映射回真实键，用户选的竖版才算数。
        raw_ratio = self.ex_ratio.currentText()
        ratio = raw_ratio.split("(")[0].strip().split("（")[0].strip()
        if ratio not in config.EXPORT_SIZES:
            ratio = "3:4 竖版"
        return {
            "ratio": ratio,
            "res": self.ex_res.currentText(),
            "fps": 30 if "30" in self.ex_fps.currentText() else 25,
            "crf": config.QUALITY_CRF.get(self.ex_quality.currentText(), 23),
            "transition": "fade" if self.ex_transition.currentIndex() == 0 else "none",
            "transition_s": config.DEFAULT_TRANSITION_S,
            "min_hold_ms": self.ex_hold.value(),
            "sub_transition_s": config.DEFAULT_SUB_TRANSITION_S,
        }

    def render_timeline(self):
        if not self._require_db():
            return
        shots = self.db.get_timeline()
        usable = [s for s in shots if s.get("asset_path")]
        if not usable:
            QMessageBox.information(self, "提示", "时间线为空或无有效镜头，请先建立时间线并绑定素材")
            return
        task_id = self.db.add_task("成片渲染", input_file=f"{len(shots)} 个镜头")
        out_name = f"成片_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        out_path = str(Path(self.ex_path.text()) / out_name)
        self.btn_render.setEnabled(False)
        self.render_progress.setVisible(True)
        self.render_progress.setRange(0, len(shots))
        w = RenderWorker(shots, self._export_params(), out_path, db=self.db, parent=self)
        self.render_worker = w

        def on_prog(cur, total, sid, cached):
            self.render_progress.setValue(cur)
            self.status.showMessage(f"渲染 {cur}/{total}：{sid}（{'缓存复用' if cached else '新生成'}）")

        def on_done(result):
            self.btn_render.setEnabled(True)
            self.render_progress.setVisible(False)
            lines = [f'镜头处理：新生成 {result.get("rendered",0)}，缓存复用 {result.get("cached",0)}']
            for e in result.get("errors", []):
                lines.append(f'  错误 {e.get("shot_id")}: {e.get("error","")[:80]}')
            if result.get("output", {}).get("ok"):
                v = result.get("verify", {})
                self.last_render_path = result["output"]["path"]  # 供阶段五最终合成使用
                lines.append(f'成片：{result["output"]["path"]}')
                lines.append(f'校验：{v.get("width")}x{v.get("height")} '
                             f'{v.get("fps")}fps 时长{v.get("duration_ms",0)/1000:.1f}s 可解码={v.get("ok")}')
                self.db.finish_task(task_id, "成功", output_file=result["output"]["path"])
            else:
                lines.append("成片失败：" + result.get("output", {}).get("error", "见上方错误"))
                self.db.finish_task(task_id, "失败", error=str(result.get("errors", ""))[:200])
            self.render_result.setPlainText("\n".join(lines))
            self.status.showMessage("成片渲染完成")
            self.render_worker = None

        w.progress.connect(on_prog)
        w.done.connect(on_done)
        w.start()

    # ==================== 阶段五：音频字幕 ====================
    def _make_audio(self):
        from ..core import narration as narration_mod
        from ..ai import tts_provider
        w = QWidget()
        outer = QVBoxLayout(w)

        hint = QLabel("文案 · 中文配音 · 字幕 · 背景音乐 —— 三轨可独立开关。本地离线配音，不联网、不上传、无费用。")
        hint.setStyleSheet("color:#377; font-weight:bold;")
        outer.addWidget(hint)

        # 顶部：商品资料 + 三个开关
        top = QHBoxLayout()
        info_g = QGroupBox("商品资料（只填真实信息，未填不会编入文案）")
        form = QFormLayout(info_g)
        self.pi_name = QLineEdit(); self.pi_name.setPlaceholderText("商品名称，如：带灯床头柜")
        self.pi_color = QLineEdit(); self.pi_color.setPlaceholderText("颜色（选填）")
        self.pi_material = QLineEdit(); self.pi_material.setPlaceholderText("材质（选填，须真实）")
        self.pi_scene = QLineEdit(); self.pi_scene.setPlaceholderText("使用场景（选填）")
        self.pi_selling = QLineEdit(); self.pi_selling.setPlaceholderText("卖点（选填）")
        form.addRow("商品名称*", self.pi_name)
        form.addRow("颜色", self.pi_color)
        form.addRow("材质", self.pi_material)
        form.addRow("使用场景", self.pi_scene)
        form.addRow("卖点", self.pi_selling)
        top.addWidget(info_g, 2)

        opt_g = QGroupBox("声音与字幕开关")
        ov = QVBoxLayout(opt_g)
        self.chk_narration = QCheckBox("启用中文旁白")
        self.chk_narration.setChecked(True)
        self.chk_subtitle = QCheckBox("启用字幕（内嵌视频）")
        self.chk_subtitle.setChecked(True)
        self.chk_music = QCheckBox("启用背景音乐")
        self.chk_music.setChecked(False)
        self.chk_duck = QCheckBox("旁白出现时自动降低音乐")
        self.chk_duck.setChecked(True)
        for c in (self.chk_narration, self.chk_subtitle, self.chk_music, self.chk_duck):
            ov.addWidget(c)
        self.note_missing = QLabel("")
        self.note_missing.setStyleSheet("color:#c60;")
        self.note_missing.setWordWrap(True)
        ov.addWidget(self.note_missing)
        top.addWidget(opt_g, 1)
        outer.addLayout(top)

        # 中部左：逐句文案表
        mid = QHBoxLayout()
        line_g = QGroupBox("逐句文案（与镜头一一对应，可直接改文字/时间）")
        lv = QVBoxLayout(line_g)
        self.line_table = QTableWidget()
        self.line_table.setColumnCount(5)
        self.line_table.setHorizontalHeaderLabels(["#", "对应镜头", "文案", "开始s", "结束s"])
        self.line_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.line_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lv.addWidget(self.line_table)
        btn_row = QHBoxLayout()
        b_gen = QPushButton("① 生成文案"); b_gen.clicked.connect(self.generate_narration)
        b_tts = QPushButton("② 生成中文配音"); b_tts.clicked.connect(self.start_tts)
        b_save_line = QPushButton("保存选中句"); b_save_line.clicked.connect(self.save_selected_line)
        b_try = QPushButton("试听选中句"); b_try.clicked.connect(self.play_selected_voice)
        for b in (b_gen, b_tts, b_save_line, b_try):
            btn_row.addWidget(b)
        lv.addLayout(btn_row)
        self.tts_progress = QProgressBar(); self.tts_progress.setVisible(False)
        lv.addWidget(self.tts_progress)
        mid.addWidget(line_g, 3)

        # 中部右：字幕样式 + 音乐
        right = QVBoxLayout()
        sub_g = QGroupBox("字幕样式（默认底部，避开拉手/灯头/开关）")
        sf = QFormLayout(sub_g)
        self.sub_style_mode = QComboBox()
        self.sub_style_mode.addItems(["卖点标签（参考片风格）", "标准字幕（完整句白字）"])
        self.sub_font = QComboBox(); self.sub_font.addItems(["Microsoft YaHei", "SimHei", "SimSun"])
        self.sub_size = QSpinBox(); self.sub_size.setRange(16, 72); self.sub_size.setValue(36)
        self.sub_color = QComboBox(); self.sub_color.addItems(["白色", "黄色", "青色"])
        self.sub_align = QComboBox(); self.sub_align.addItems(["底部居中", "顶部居中"])
        self.sub_outline = QSpinBox(); self.sub_outline.setRange(0, 5); self.sub_outline.setValue(2)
        sf.addRow("字幕风格", self.sub_style_mode)
        sf.addRow("字体", self.sub_font); sf.addRow("字号", self.sub_size)
        sf.addRow("颜色", self.sub_color); sf.addRow("位置", self.sub_align)
        sf.addRow("描边", self.sub_outline)
        right.addWidget(sub_g)

        mus_g = QGroupBox("配音与背景音乐（参考片=纯BGM无念稿；也可换自有音乐）")
        mv = QVBoxLayout(mus_g)
        vm_row = QHBoxLayout()
        vm_row.addWidget(QLabel("配音模式"))
        self.voice_mode = QComboBox()
        self.voice_mode.addItems(["纯背景音乐（参考片风格）", "中文旁白 + 音乐", "静音（无音轨内容）"])
        vm_row.addWidget(self.voice_mode)
        mv.addLayout(vm_row)
        self.music_path = QLineEdit(); self.music_path.setPlaceholderText("未选择音乐文件（默认用本地合成无版权BGM）")
        b_music = QPushButton("选择音乐文件"); b_music.clicked.connect(self.choose_music)
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("音量"))
        self.music_vol = QSpinBox(); self.music_vol.setRange(0, 100); self.music_vol.setValue(25)
        vol_row.addWidget(self.music_vol); vol_row.addWidget(QLabel("%"))
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("语速"))
        self.tts_rate = QSpinBox(); self.tts_rate.setRange(80, 240); self.tts_rate.setValue(160)
        rate_row.addWidget(self.tts_rate)
        mv.addWidget(self.music_path); mv.addWidget(b_music)
        mv.addLayout(vol_row); mv.addLayout(rate_row)
        right.addWidget(mus_g)
        mid.addLayout(right, 1)
        outer.addLayout(mid)

        # 底部：最终导出
        exp_g = QGroupBox("最终导出（在时间线页生成无声成片后，这里合成字幕+配音+音乐）")
        ev = QVBoxLayout(exp_g)
        b_srt = QPushButton("导出 SRT 字幕文件"); b_srt.clicked.connect(self.export_srt)
        b_final = QPushButton("③ 合成最终 MP4（字幕+旁白+音乐）"); b_final.clicked.connect(self.final_export)
        self.final_progress = QProgressBar(); self.final_progress.setVisible(False)
        self.final_result = QTextEdit(); self.final_result.setReadOnly(True); self.final_result.setMaximumHeight(90)
        er = QHBoxLayout(); er.addWidget(b_srt); er.addWidget(b_final)
        ev.addLayout(er); ev.addWidget(self.final_progress); ev.addWidget(self.final_result)
        outer.addWidget(exp_g)
        return w

    def refresh_audio_page(self):
        if not self.db:
            return
        # 载入商品资料
        info = self.db.get_product_info()
        self.pi_name.setText(info.get("name", ""))
        self.pi_color.setText(info.get("color", ""))
        self.pi_material.setText(info.get("material", ""))
        self.pi_scene.setText(info.get("scene", ""))
        self.pi_selling.setText(info.get("selling", ""))
        # 载入声音设置
        au = self.db.get_audio_settings()
        self.chk_narration.setChecked(au.get("narration", True))
        self.chk_subtitle.setChecked(au.get("subtitle", True))
        self.chk_music.setChecked(au.get("music", False))
        self.chk_duck.setChecked(au.get("duck", True))
        self.music_path.setText(au.get("music_path", ""))
        self.music_vol.setValue(int(au.get("music_volume", 25)))
        self.tts_rate.setValue(int(au.get("rate", 160)))
        self.sub_size.setValue(int(au.get("sub_size", 36)))
        self.sub_outline.setValue(int(au.get("sub_outline", 2)))
        self.sub_style_mode.setCurrentIndex(0 if au.get("sub_style", config.DEFAULT_SUBTITLE_STYLE) == "selling_badge" else 1)
        self.voice_mode.setCurrentIndex(
            {"bgm_only": 0, "narration": 1, "silent": 2}.get(au.get("voice_mode", config.DEFAULT_VOICE_MODE), 0))
        idx = {"白色": 0, "黄色": 1, "青色": 2}.get(au.get("sub_color", "白色"), 0)
        self.sub_color.setCurrentIndex(idx)
        self.sub_align.setCurrentIndex(1 if au.get("sub_align") == "顶部居中" else 0)
        self._load_line_table()

    def _audio_settings(self):
        color = self.sub_color.currentText()
        hexcolor = {"白色": "#FFFFFF", "黄色": "#FFE033", "青色": "#33EEEE"}.get(color, "#FFFFFF")
        return {
            "narration": self.chk_narration.isChecked(),
            "subtitle": self.chk_subtitle.isChecked(),
            "music": self.chk_music.isChecked(),
            "duck": self.chk_duck.isChecked(),
            "sub_style": "selling_badge" if self.sub_style_mode.currentIndex() == 0 else "standard",
            "voice_mode": ["bgm_only", "narration", "silent"][self.voice_mode.currentIndex()],
            "music_path": self.music_path.text().strip(),
            "music_volume": self.music_vol.value() / 100.0,
            "rate": self.tts_rate.value(),
            "sub_font": self.sub_font.currentText(),
            "sub_size": self.sub_size.value(),
            "sub_color": hexcolor,
            "sub_align": self.sub_align.currentText(),
            "sub_outline": self.sub_outline.value(),
        }

    def _persist_audio_inputs(self):
        """把界面上的资料/设置存进项目。"""
        if not self.db:
            return
        info = {
            "name": self.pi_name.text().strip(), "color": self.pi_color.text().strip(),
            "material": self.pi_material.text().strip(), "scene": self.pi_scene.text().strip(),
            "selling": self.pi_selling.text().strip(),
        }
        self.db.set_product_info(info)
        self.db.set_audio_settings(self._audio_settings())

    def _load_line_table(self):
        lines = self.db.get_narration_lines() if self.db else []
        self.line_table.setRowCount(len(lines))
        for r, ln in enumerate(lines):
            vals = [str(r + 1), ln.get("name", ""), ln.get("text", ""),
                    f'{ln.get("start_ms", 0)/1000:.1f}', f'{ln.get("end_ms", 0)/1000:.1f}']
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, ln.get("line_id", ""))
                self.line_table.setItem(r, c, item)

    def generate_narration(self):
        from ..core import narration as narration_mod
        if not self.db:
            self.status.showMessage("请先打开项目"); return
        self._persist_audio_inputs()
        shots = self.db.get_timeline()
        if not shots:
            QMessageBox.information(self, "提示", "请先在『时间线』页建立时间线（从分镜生成镜头）。")
            return
        info = self.db.get_product_info()
        # 【阶段十】卖点标签风格：用 4-8 字短卖点；标准风格沿用原完整句
        use_badge = self.sub_style_mode.currentIndex() == 0
        result = (narration_mod.build_selling_lines(shots, info) if use_badge
                  else narration_mod.build_all(shots, info))
        self.db.save_narration_lines(result["lines"])
        self._load_line_table()
        missing = result["missing"]
        if use_badge:
            self.note_missing.setText(
                "卖点标签：只写镜头里真实拍到的画面（手抚柜面/圆形拉手/三色灯光…），"
                "不含材质/参数等未核实宣称；逐句可编辑。")
        else:
            self.note_missing.setText(
                "提醒：未填写 " + "、".join(missing) + "（已用中性说法，未编造参数）" if missing
                else "商品资料已填写，文案引用了真实信息。")
        self.status.showMessage(
            f"已生成 {len(result['lines'])} 句{'卖点标签' if use_badge else '文案'}，逐句可直接编辑")

    def save_selected_line(self):
        if not self.db:
            return
        row = self.line_table.currentRow()
        if row < 0:
            return
        lid = self.line_table.item(row, 0).data(Qt.UserRole)
        try:
            start = int(float(self.line_table.item(row, 3).text()) * 1000)
            end = int(float(self.line_table.item(row, 4).text()) * 1000)
        except (ValueError, AttributeError):
            QMessageBox.warning(self, "输入错误", "开始/结束时间必须是数字（秒）")
            return
        text = self.line_table.item(row, 2).text().strip()
        self.db.update_narration_line(lid, text=text, start_ms=start, end_ms=end)
        self.status.showMessage("已保存该句文案与时间")

    def start_tts(self):
        from ..workers import TTSWorker
        if not self.db:
            self.status.showMessage("请先打开项目"); return
        self._persist_audio_inputs()
        lines = [ln for ln in self.db.get_narration_lines() if (ln.get("text") or "").strip()]
        if not lines:
            QMessageBox.information(self, "提示", "请先生成文案。")
            return
        items = [(ln["line_id"], ln["text"]) for ln in lines]
        rate = self.tts_rate.value()
        self.tts_progress.setVisible(True); self.tts_progress.setRange(0, len(items)); self.tts_progress.setValue(0)
        self.status.showMessage("正在用本地中文语音逐句配音…")
        w = TTSWorker(items, rate=rate, parent=self)

        def on_prog(cur, tot, msg):
            self.tts_progress.setValue(cur)
            self.status.showMessage(f"配音 {cur}/{tot}：{msg}")

        def on_one(line_id, res, text):
            if res.get("ok"):
                self.db.update_narration_line(line_id, voice_path=res["path"])

        def on_done(ok, fail):
            self.tts_progress.setVisible(False)
            self._load_line_table()
            QMessageBox.information(self, "配音完成", f"成功 {ok} 句，失败 {fail} 句。本地语音，无费用。")

        w.progress.connect(on_prog); w.one_done.connect(on_one); w.all_done.connect(on_done)
        self.tts_worker = w
        w.start()

    def play_selected_voice(self):
        import subprocess
        from .. import media
        row = self.line_table.currentRow()
        if row < 0 or not self.db:
            return
        lid = self.line_table.item(row, 0).data(Qt.UserRole)
        ln = self.db.get_narration_line(lid)
        path = (ln or {}).get("voice_path", "")
        if not path or not Path(path).exists():
            QMessageBox.information(self, "提示", "这句还没有配音，请先点『生成中文配音』。")
            return
        # Windows 用系统播放器试听
        try:
            os.startfile(path)  # noqa: S606 - 本地 wav 试听
            self.status.showMessage("已用系统播放器打开配音试听")
        except Exception:
            QMessageBox.information(self, "试听", f"请用播放器打开：{path}")

    def choose_music(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择背景音乐", "", "音频 (*.mp3 *.wav *.m4a *.aac)")
        if path:
            self.music_path.setText(path)
            self.chk_music.setChecked(True)
            self._persist_audio_inputs()

    def export_srt(self):
        if not self.db:
            self.status.showMessage("请先打开项目"); return
        lines = self.db.get_narration_lines()
        if not lines:
            QMessageBox.information(self, "提示", "请先生成文案。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出字幕", str(config.EXPORT_DIR / "字幕.srt"), "SRT 字幕 (*.srt)")
        if not path:
            return
        from ..core import subtitle
        r = subtitle.build_srt(lines, path)
        QMessageBox.information(self, "导出字幕", f"{'成功' if r['ok'] else '失败'}：{path}")

    def final_export(self):
        from ..workers import FinalExportWorker
        if not self.db:
            self.status.showMessage("请先打开项目"); return
        self._persist_audio_inputs()
        # 无声成片：时间线页生成的成片（优先最近一次）
        silent = getattr(self, "last_render_path", "")
        if not silent or not Path(silent).exists():
            cands = sorted(config.EXPORT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
            silent = str(cands[0]) if cands else ""
        if not silent:
            QMessageBox.information(self, "提示", "请先在『时间线』页生成一条无声成片。")
            return
        lines = self.db.get_narration_lines()
        if not lines:
            QMessageBox.information(self, "提示", "请先生成文案与配音。")
            return
        au = self._audio_settings()
        from ..core import audio_mixer, narration as narration_mod, subtitle as subtitle_mod, bgm as bgm_mod
        mode = au.get("voice_mode", config.DEFAULT_VOICE_MODE)
        use_badge = (au.get("sub_style", config.DEFAULT_SUBTITLE_STYLE) == "selling_badge")

        # 字幕风格：卖点标签给每行补版式 kind；标准风格原样
        eff_lines = narration_mod.decorate_lines(lines) if use_badge else lines

        info = media.probe(silent)
        dur = info.get("duration_ms", 0)

        # ---- 配音模式 ----
        narration_path = ""
        music_path = au.get("music_path", "") if au.get("music") else ""
        enable_narration = False
        enable_music = False
        if mode == "silent":
            pass  # 全静音
        elif mode == "narration":
            # 原逻辑：逐句中文旁白（只把有配音的句子摆上时间轴）
            voices = [(ln["voice_path"], ln["start_ms"]) for ln in lines
                      if ln.get("voice_path") and Path(ln["voice_path"]).exists()]
            if au["narration"] and voices:
                ntrack = config.CACHE_DIR / "final" / "narration_full.wav"
                nr = audio_mixer.build_narration_track(voices, dur, ntrack, volume=1.0)
                if nr["ok"]:
                    narration_path, enable_narration = nr["path"], True
                else:
                    self.status.showMessage("旁白轨生成失败：" + nr.get("error", "")[:80])
            # 音乐：优先自有，否则本地合成无版权 BGM 兜底
            if not music_path:
                gen = bgm_mod.synth_bgm(dur / 1000.0 + 0.3,
                                        config.CACHE_DIR / "final" / "auto_bgm.wav")
                music_path = gen["path"] if gen["ok"] else ""
            enable_music = bool(music_path)
        else:  # bgm_only（参考片风格，默认）：不念稿，全程平稳 BGM
            if not music_path:
                gen = bgm_mod.synth_bgm(dur / 1000.0 + 0.3,
                                        config.CACHE_DIR / "final" / "auto_bgm.wav",
                                        volume=config.DEFAULT_BGM_VOLUME)
                music_path = gen["path"] if gen["ok"] else ""
            enable_music = bool(music_path)

        sub_style = {
            "font": au["sub_font"], "size": au["sub_size"], "color": au["sub_color"],
            "outline": au["sub_outline"],
            "alignment": 8 if au["sub_align"] == "顶部居中" else 2,
        }
        if use_badge:
            sub_style["preset"] = subtitle_mod.BADGE_PRESET
            sub_style["lines"] = eff_lines
        srt_path = config.CACHE_DIR / "final" / "current.srt"
        out = config.EXPORT_DIR / "最终商品视频.mp4"
        opts = {
            "subtitle_style": sub_style,
            "enable_subtitle": au["subtitle"],
            "enable_narration": enable_narration,
            "enable_music": enable_music,
            "music_path": music_path or None,
            "music_volume": au["music_volume"] if not mode == "bgm_only" else 1.0,
            "duck": au["duck"] and enable_narration,
            "narration_path": narration_path or None,
        }
        self.final_progress.setVisible(True); self.final_progress.setRange(0, 3); self.final_progress.setValue(0)
        self.status.showMessage("正在合成最终视频…")
        w = FinalExportWorker(silent, lines, str(srt_path), str(out), opts, parent=self)

        def on_prog(cur, tot, msg):
            self.final_progress.setValue(cur)
            self.status.showMessage(msg)

        def on_done(r):
            self.final_progress.setVisible(False)
            if r.get("ok"):
                self.final_result.setPlainText(
                    f"成片成功：{r['path']}\n轨道：{r.get('tracks')}\n"
                    f"校验：{r.get('verify', {}).get('width')}x{r.get('verify', {}).get('height')} "
                    f"{r.get('verify', {}).get('fps')}fps "
                    f"{r.get('verify', {}).get('duration_ms', 0)/1000:.1f}s")
                self.status.showMessage("最终视频合成完成")
            else:
                self.final_result.setPlainText("失败：" + r.get("error", ""))
                self.status.showMessage("最终合成失败")

        w.progress.connect(on_prog); w.done.connect(on_done)
        self.final_worker = w
        w.start()

    # ---------- 导出设置 ----------
    def _make_export(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("导出设置（参数供『时间线』页生成成片使用；比例适配自动补边，不拉伸商品）"))
        g = QGroupBox("成片参数")
        form = QFormLayout(g)
        self.ex_ratio = QComboBox(); self.ex_ratio.addItems(["9:16 竖屏", "16:9 横屏", "1:1 正方形", "3:4 竖版(淘宝商详推荐)"])
        self.ex_res = QComboBox(); self.ex_res.addItems(["720p", "1080p"])
        self.ex_fps = QComboBox(); self.ex_fps.addItems(["25 fps", "30 fps"])
        self.ex_quality = QComboBox(); self.ex_quality.addItems(["标准", "高质量", "最高"])
        # 阶段六：转场与最短停留（默认值来自 config，均可回退）
        self.ex_transition = QComboBox()
        self.ex_transition.addItems(["叠化过渡（自然）", "硬切（原样）"])
        self.ex_transition.setCurrentIndex(0 if config.DEFAULT_TRANSITION == "fade" else 1)
        self.ex_hold = QSpinBox(); self.ex_hold.setRange(0, 10000); self.ex_hold.setSingleStep(500)
        self.ex_hold.setValue(config.DEFAULT_MIN_HOLD_MS)
        self.ex_path = QLineEdit(str(config.EXPORT_DIR))
        b_path = QPushButton("选择位置"); b_path.clicked.connect(self._choose_export_dir)
        ph = QHBoxLayout(); ph.addWidget(self.ex_path); ph.addWidget(b_path)
        form.addRow("输出比例：", self.ex_ratio)
        form.addRow("分辨率：", self.ex_res)
        form.addRow("帧率：", self.ex_fps)
        form.addRow("编码质量：", self.ex_quality)
        form.addRow("镜头转场：", self.ex_transition)
        self.ex_hold.setToolTip("全局兜底：没有单独设建议时长的镜头至少停这么久。节奏主要用时间线页每个镜头的『建议时长』管理。")
        form.addRow("最短停留(毫秒,兜底)：", self.ex_hold)
        form.addRow("导出位置：", ph)
        v.addWidget(g)
        b_save = QPushButton("保存导出设置")
        b_save.clicked.connect(self.save_export_settings)
        v.addWidget(b_save)
        self.export_note = QLabel("成片合成请到左侧『时间线』页点『生成初版成片』。此处参数即时生效（生成时读取）。")
        self.export_note.setStyleSheet("color:#383;")
        v.addWidget(self.export_note)
        v.addStretch()
        return w

    # ==================== 项目操作 ====================
    def new_project(self):
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称：", text="未命名项目")
        if not ok or not name.strip():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存项目到", str(config.DB_DIR / f"{name}.db"), "项目文件 (*.db)")
        if not path:
            return
        try:
            self.db = dbmod.ProjectDB(path)
            self.db.set_project_name(name.strip())
            self.db.init_storyboards(config.DEFAULT_STORYBOARD_TEMPLATE)
            # 【阶段七】新项目自动套用模板里的默认镜头节奏（按淘宝主图视频 15-30 秒分配）
            try:
                tpl = storyboard_matcher.load_template()
                if tpl and tpl.get("shot_targets"):
                    self.db.apply_storyboard_targets(tpl["shot_targets"])
            except Exception:  # noqa: BLE001  # 模板缺 shot_targets 不影响建项目
                pass
            self.db.touch_modified()
            self.project_path = path
            self._add_recent(path, name.strip())
            self._on_project_opened(name.strip(), path)
            QMessageBox.information(self, "完成", f"项目已创建：{name}\n位置：{path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "错误", f"创建失败：{e}")

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开项目", str(config.DB_DIR), "项目文件 (*.db)")
        if path:
            self._load_project(path)

    def _load_project(self, path):
        try:
            self.db = dbmod.ProjectDB(path)
            self.project_path = path
            meta = self.db.get_meta()
            name = meta.get("name", Path(path).stem)
            self._add_recent(path, name)
            self._on_project_opened(name, path)
            self.status.showMessage(f"已打开项目：{name}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "错误", f"打开失败：{e}")

    def _on_project_opened(self, name, path):
        self.home_info.setText(f"当前项目：<b>{name}</b>　|　位置：{path}")
        self.nav.setCurrentRow(0)
        self.refresh_assets()
        self.refresh_storyboards()
        # 载入导出设置
        s = self.db.get_settings()
        if s.get("ex_ratio") in [self.ex_ratio.itemText(i) for i in range(self.ex_ratio.count())]:
            self.ex_ratio.setCurrentText(s["ex_ratio"])
        if s.get("ex_res") in [self.ex_res.itemText(i) for i in range(self.ex_res.count())]:
            self.ex_res.setCurrentText(s["ex_res"])
        if s.get("ex_fps") in [self.ex_fps.itemText(i) for i in range(self.ex_fps.count())]:
            self.ex_fps.setCurrentText(s["ex_fps"])
        if s.get("ex_quality") in [self.ex_quality.itemText(i) for i in range(self.ex_quality.count())]:
            self.ex_quality.setCurrentText(s["ex_quality"])
        if s.get("ex_transition") in [self.ex_transition.itemText(i) for i in range(self.ex_transition.count())]:
            self.ex_transition.setCurrentText(s["ex_transition"])
        if s.get("ex_hold"):
            self.ex_hold.setValue(int(s["ex_hold"]))

    def save_export_settings(self):
        if not self.db:
            QMessageBox.warning(self, "提示", "请先新建或打开项目")
            return
        self.db.set_setting("ex_ratio", self.ex_ratio.currentText())
        self.db.set_setting("ex_res", self.ex_res.currentText())
        self.db.set_setting("ex_fps", self.ex_fps.currentText())
        self.db.set_setting("ex_quality", self.ex_quality.currentText())
        self.db.set_setting("ex_transition", self.ex_transition.currentText())
        self.db.set_setting("ex_hold", str(self.ex_hold.value()))
        self.db.set_setting("ex_path", self.ex_path.text())
        self.db.touch_modified()
        QMessageBox.information(self, "完成", "导出设置已保存到项目")

    def _choose_export_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出位置", self.ex_path.text())
        if d:
            self.ex_path.setText(d)

    # ==================== 素材库 ====================
    def _require_db(self):
        if self.db is None:
            QMessageBox.warning(self, "提示", "请先新建或打开项目")
            return False
        return True

    def import_files(self):
        if not self._require_db():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", str(Path.home() / "Desktop"),
            "视频文件 (*.mp4 *.mov *.m4v *.avi *.mkv *.wmv *.flv *.webm)")
        if files:
            self._start_import(files)

    def import_folder(self):
        if not self._require_db():
            return
        folder = QFileDialog.getExistingDirectory(self, "选择素材文件夹", str(Path.home() / "Desktop"))
        if not folder:
            return
        files = []
        for root, _, names in os.walk(folder):
            for n in names:
                if Path(n).suffix.lower() in config.VIDEO_EXTS:
                    files.append(str(Path(root) / n))
        if not files:
            QMessageBox.information(self, "提示", "该文件夹下未找到视频文件")
            return
        self._start_import(files)

    def _start_import(self, files):
        self.progress.setVisible(True)
        self.progress.setRange(0, len(files))
        task_id = self.db.add_task("导入素材", input_file=f"{len(files)} 个文件")
        w = ImportWorker(files, self)
        self.import_worker = w

        def on_progress(cur, total, name):
            self.progress.setValue(cur)
            self.status.showMessage(f"正在读取元数据 {cur}/{total}：{name}")

        def on_one(info):
            info["thumb_path"] = info.get("thumb_path", "") or ""
            self.db.add_asset(info)

        def on_done(ok, fail):
            self.progress.setVisible(False)
            self.db.finish_task(task_id, "成功" if fail == 0 else "部分失败",
                                f"成功 {ok}，失败/跳过 {fail}")
            self.refresh_assets()
            self.status.showMessage(f"导入完成：成功 {ok}，失败/跳过 {fail}")
            if fail:
                QMessageBox.warning(self, "导入完成（含问题）",
                                    f"成功 {ok} 条，{fail} 条无法解码或读取。\n请在素材库查看“解码”列和错误提示。")
            self.import_worker = None

        w.progress.connect(on_progress)
        w.one_done.connect(on_one)
        w.all_done.connect(on_done)
        w.start()

    def refresh_assets(self):
        if not self.db:
            self.asset_table.setRowCount(0)
            return
        kw = self.search_box.text().strip().lower()
        self.assets_cache = [a for a in self.db.get_assets()
                             if not kw or kw in a["filename"].lower()]
        t = self.asset_table
        t.setRowCount(len(self.assets_cache))
        for row, a in enumerate(self.assets_cache):
            vals = [
                a["filename"], media.format_duration(a["duration_ms"]),
                f'{a["width"]}x{a["height"]}' if a["width"] else "—",
                f'{a["fps"]:.1f}' if a["fps"] else "—",
                a["codec"] or "—", "有" if a["has_audio"] else "无",
                a.get("analysis_status") or "未分析",
                "正常" if a["decode_ok"] else "失败/跳过",
                a.get("error") or a["path"],
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                if col == 7 and val != "正常":
                    item.setForeground(Qt.red)
                t.setItem(row, col, item)

    def _asset_double_click(self, index):
        self._show_asset_thumb(index.row())

    def _show_asset_thumb(self, row):
        if row < 0 or row >= len(self.assets_cache):
            return
        a = self.assets_cache[row]
        tp = a.get("thumb_path") or ""
        if tp and Path(tp).exists():
            self.asset_thumb.setPixmap(QPixmap(tp))
        else:
            self.asset_thumb.setText("无缩略图（解码失败或未生成）")

    def _preview_selected_asset(self):
        rows = self.asset_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选中一条素材")
            return
        a = self.assets_cache[rows[0].row()]
        if not a["decode_ok"]:
            QMessageBox.warning(self, "无法播放", "该素材解码失败或尚未成功读取：" + (a.get("error") or ""))
            return
        self.nav.setCurrentRow(3)
        if self.player.open(a["path"]):
            self.status.showMessage(f"正在预览：{a['filename']}")
            self.player.player.play()
        else:
            QMessageBox.warning(self, "无法播放", "播放器无法打开该视频")

    def _asset_context_menu(self, pos):
        rows = self.asset_table.selectionModel().selectedRows()
        if not rows:
            return
        a = self.assets_cache[rows[0].row()]
        menu = QMenu(self)
        act_reloc = menu.addAction("重新定位素材路径…")
        act_del = menu.addAction("从项目移除（不删除原文件）")
        choice = menu.exec(self.asset_table.mapToGlobal(pos))
        if choice == act_reloc:
            new, _ = QFileDialog.getOpenFileName(self, "重新选择素材文件", a["path"])
            if new:
                self.db.update_asset_path(a["id"], new)
                self.refresh_assets()
                self.status.showMessage(f"已重新定位：{Path(new).name}")
        elif choice == act_del:
            if QMessageBox.question(self, "确认", "仅从项目移除记录，不会删除磁盘上的原文件。继续？") == QMessageBox.Yes:
                self.db.delete_asset(a["id"])
                self.refresh_assets()

    # ==================== 分镜工作台 ====================
    def refresh_storyboards(self):
        if not self.db:
            self.story_list.clear()
            return
        rows = self.db.get_storyboards()
        results = getattr(self, "match_results", None) or self.db.get_match_results()
        self.story_list.clear()
        for i, sb in enumerate(rows, 1):
            lock = " 🔒" if sb["locked"] else ""
            res = results.get(str(sb["id"]))
            mark = ""
            if res and res.get("missing"):
                mark = " ⚠缺素材"
            elif res and not res.get("skipped"):
                mark = " ✓有候选"
            text = f'{i:02d}. {sb["name"]}    [{sb["status"]}]{lock}{mark}'
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, sb["id"])
            if sb["status"] == "已确认":
                item.setForeground(Qt.darkGreen)
            elif res and res.get("missing"):
                item.setForeground(Qt.red)
            self.story_list.addItem(item)

    def _story_selected(self, item):
        sb_id = item.data(Qt.UserRole)
        sb = next((s for s in self.db.get_storyboards() if s["id"] == sb_id), None)
        if not sb:
            return
        self._current_sb_id = sb_id
        self.sb_detail.setVisible(True)
        self.sb_name.blockSignals(True); self.sb_status.blockSignals(True); self.sb_lock.blockSignals(True)
        self.sb_name.setText(sb["name"])
        self.sb_status.setCurrentText(sb["status"] if sb["status"] in config.ANALYSIS_STATES else "未分析")
        self.sb_lock.setChecked(bool(sb["locked"]))
        self.sb_note.blockSignals(True); self.sb_note.setPlainText(sb.get("note") or ""); self.sb_note.blockSignals(False)
        self.sb_name.blockSignals(False); self.sb_status.blockSignals(False); self.sb_lock.blockSignals(False)
        # 显示该分镜的匹配候选
        results = getattr(self, "match_results", None) or self.db.get_match_results()
        res = results.get(str(sb_id))
        self.sb_cand_box.setVisible(True)
        self.cand_list.clear()
        if not res:
            self.cand_reason.setPlainText("尚未匹配。点上方『自动匹配分镜』生成候选。")
            return
        if res.get("skipped"):
            self.cand_reason.setPlainText(f'已跳过：{res.get("reason","")}')
            return
        if res.get("missing"):
            self.cand_list.addItem("（无合格候选）")
            self.cand_reason.setPlainText(
                f'【缺少素材，暂不展示】\n{res.get("reason","")}\n\n补拍建议：{res.get("advice","")}')
            return
        for i, c in enumerate(res.get("candidates", []), 1):
            clip = c["clip"]
            tag = "★最佳" if i == 1 else f"备选{i}"
            self.cand_list.addItem(
                f'{tag}｜{clip.get("filename","?")} '
                f'{clip.get("start_ms",0)/1000:.1f}-{clip.get("end_ms",0)/1000:.1f}s '
                f'评分{c["score"]} 置信{c["confidence"]}')
        self.cand_reason.setPlainText(
            f'推荐理由：{res.get("reason","")}\n'
            + ("⚠ 需人工核验：本地视觉为启发式识别，未确认具体语义。"))

    def _save_story_detail(self):
        sb_id = getattr(self, "_current_sb_id", None)
        if not sb_id or not self.db:
            return
        self.db.update_storyboard(
            sb_id,
            name=self.sb_name.text().strip() or "未命名分镜",
            status=self.sb_status.currentText(),
            locked=1 if self.sb_lock.isChecked() else 0,
            note=self.sb_note.toPlainText(),
        )
        self.db.touch_modified()
        self.refresh_storyboards()

    def add_storyboard(self):
        if not self._require_db():
            return
        name, ok = QInputDialog.getText(self, "新增分镜", "分镜名称：")
        if ok and name.strip():
            self.db.add_storyboard(name.strip())
            self.refresh_storyboards()

    def delete_storyboard(self):
        if not self._require_db():
            return
        item = self.story_list.currentItem()
        if not item:
            return
        if QMessageBox.question(self, "确认", "删除该分镜及其候选记录？") == QMessageBox.Yes:
            self.db.delete_storyboard(item.data(Qt.UserRole))
            self.sb_detail.setVisible(False)
            self.refresh_storyboards()

    def move_storyboard(self, delta):
        if not self.db:
            return
        rows = self.db.get_storyboards()
        cur = self.story_list.currentRow()
        new = cur + delta
        if cur < 0 or new < 0 or new >= len(rows):
            return
        ids = [r["id"] for r in rows]
        ids[cur], ids[new] = ids[new], ids[cur]
        self.db.reorder_storyboards(ids)
        self.refresh_storyboards()
        self.story_list.setCurrentRow(new)

    # ==================== 阶段三：分镜匹配 ====================
    def auto_match_storyboards(self):
        if not self._require_db():
            return
        clips = self.db.get_clips()
        analyzed = [c for c in clips if c.get("semantic_status") == "已分析"]
        if not analyzed:
            QMessageBox.information(self, "提示",
                                    "请先在『素材分析』做镜头切分，并点『AI 识别+评分』完成识别后再匹配")
            return
        # 给每条片段补上文件名（匹配理由里要显示）
        assets = {a["id"]: a for a in self.db.get_assets()}
        for c in analyzed:
            a = assets.get(c["asset_id"], {})
            c["filename"] = a.get("filename", "?")
        sbs = self.db.get_storyboards()
        results = storyboard_matcher.match_all(sbs, analyzed)
        self.db.save_match_results(results)
        self.match_results = results
        self.refresh_storyboards()
        missing = sum(1 for r in results.values() if r.get("missing"))
        locked = sum(1 for r in results.values() if r.get("skipped"))
        self.status.showMessage(
            f"分镜匹配完成：{len(results)} 个分镜，缺素材 {missing} 个，跳过锁定/已确认 {locked} 个")
        QMessageBox.information(self, "匹配完成",
                                f"已为 {len(results)} 个分镜生成候选。\n"
                                f"缺少合格素材：{missing} 个（显示补拍建议，不虚构镜头）\n"
                                f"锁定/已确认跳过：{locked} 个\n\n"
                                "点任一分镜查看候选与理由；满意后点『应用最佳候选到分镜』。")

    def apply_match_selection(self):
        if not self._require_db():
            return
        results = getattr(self, "match_results", None) or self.db.get_match_results()
        if not results:
            QMessageBox.information(self, "提示", "请先点『自动匹配分镜』")
            return
        n = self.db.apply_match_selection(results)
        self.refresh_storyboards()
        self.status.showMessage(f"已将 {n} 个分镜设为候选待核验（锁定镜头未被改动）")

    def _candidate_selected(self, item):
        sb_id = getattr(self, "_current_sb_id", None)
        idx = self.cand_list.row(item)
        results = getattr(self, "match_results", None) or self.db.get_match_results()
        res = results.get(str(sb_id)) if sb_id else None
        if not res:
            return
        cands = res.get("candidates", [])
        if idx >= len(cands):
            return
        c = cands[idx]
        lines = [f'推荐评分：{c["score"]}  置信度：{c["confidence"]}',
                 f'推荐理由：{c["reason"]}']
        if c.get("need_review"):
            lines.append("⚠ 需人工核验：本地视觉为启发式识别，未确认具体语义。")
        self.cand_reason.setPlainText("\n".join(lines))

    # ==================== 最近项目 ====================
    def _add_recent(self, path, name):
        items = self._load_recent()
        items = [x for x in items if x.get("path") != path]
        items.insert(0, {"path": path, "name": name})
        items = items[:10]
        config.RECENT_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_recent(self):
        try:
            return json.loads(config.RECENT_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []

    def _refresh_recent(self):
        self.recent_list.clear()
        for it in self._load_recent():
            exists = Path(it["path"]).exists()
            item = QListWidgetItem(f'{it["name"]}  —  {it["path"]}' + ("" if exists else "  (文件丢失)"))
            item.setData(Qt.UserRole, it["path"])
            self.recent_list.addItem(item)

    def _open_recent(self, item):
        path = item.data(Qt.UserRole)
        if Path(path).exists():
            self._load_project(path)
        else:
            QMessageBox.warning(self, "文件丢失", "该项目文件已不存在，请手动打开其它项目。")

    def closeEvent(self, event):
        if getattr(self, "import_worker", None) and self.import_worker.isRunning():
            self.import_worker.stop()
            self.import_worker.wait(2000)
        if getattr(self, "analyze_worker", None) and self.analyze_worker.isRunning():
            self.analyze_worker.stop()
            self.analyze_worker.wait(2000)
        if getattr(self, "semantic_worker", None) and self.semantic_worker.isRunning():
            self.semantic_worker.stop()
            self.semantic_worker.wait(2000)
        if getattr(self, "render_worker", None) and self.render_worker.isRunning():
            self.render_worker.stop()
            self.render_worker.wait(2000)
        if getattr(self, "tts_worker", None) and self.tts_worker.isRunning():
            self.tts_worker.stop()
            self.tts_worker.wait(2000)
        if getattr(self, "final_worker", None) and self.final_worker.isRunning():
            self.final_worker.stop()
            self.final_worker.wait(2000)
        if self.player:
            self.player.stop()
        event.accept()
