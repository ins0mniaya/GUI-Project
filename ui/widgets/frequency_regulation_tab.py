# -*- coding: utf-8 -*-
"""
场景应用 Tab —— 多 PCS 实机联调

包含:
  - SceneApplicationsTab   外层容器（管理3个子Tab）
  - WaveformChart          通用录波控件
  - FrequencyRegulationSceneTab  调频场景（PFR下垂控制）
"""

from __future__ import annotations

import time
from collections import deque
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QTextEdit, QTabWidget, QVBoxLayout, QWidget, QHeaderView,
)

# ── 常量 ──────────────────────────────────────────────

WAVEFORM_MAX_POINTS = 3000
PCS_COLORS = [QColor("#0052D9"), QColor("#059669"), QColor("#D97706"),
              QColor("#DC2626"), QColor("#7C3AED")]


# ══════════════════════════════════════════════════════════
# 通用录波控件
# ══════════════════════════════════════════════════════════

class WaveformChart(QWidget):
    """多 PCS 录波曲线：左轴（频率/SOC等）+ 右轴（各PCS功率）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self._times: deque = deque(maxlen=WAVEFORM_MAX_POINTS)
        self._left_data: deque = deque(maxlen=WAVEFORM_MAX_POINTS)
        self._right_series: dict = {}
        self._right_total: deque = deque(maxlen=WAVEFORM_MAX_POINTS)
        self._show_total = False
        self._left_min, self._left_max = 49.5, 50.5
        self._right_min, self._right_max = -20, 500
        self._bg = QColor("#F8F9FB")
        self._grid = QColor("#E4E7ED")
        self._text = QColor("#909399")
        self._deadband = None          # (lo, hi) or None
        self._left_label = "Hz"
        self._right_label = "kW"

    def configure(self, left_label="Hz", lo=49.5, hi=50.5, r_lo=-20, r_hi=500):
        self._left_label = left_label
        self._left_min, self._left_max = lo, hi
        self._right_min, self._right_max = r_lo, r_hi

    def set_deadband(self, lo=None, hi=None):
        self._deadband = (lo, hi) if lo is not None else None

    def init_series(self, pcs_ids):
        self._right_series = {pid: deque(maxlen=WAVEFORM_MAX_POINTS) for pid in pcs_ids}
        self._right_total = deque(maxlen=WAVEFORM_MAX_POINTS)

    def append(self, t, left_val, rights):
        self._times.append(t)
        self._left_data.append(left_val)
        for pid in self._right_series:
            self._right_series[pid].append(rights.get(pid))
        valid = [v for v in rights.values() if v is not None]
        self._right_total.append(sum(valid) if valid else None)
        self._auto_range()
        self.update()

    def clear(self):
        self._times.clear()
        self._left_data.clear()
        for d in self._right_series.values():
            d.clear()
        self._right_total.clear()
        self._right_min, self._right_max = -20, 500
        self._left_min, self._left_max = 49.5, 50.5
        self.update()

    def _auto_range(self):
        vl = [x for x in self._left_data if x is not None]
        if vl:
            self._left_min = min(self._left_min, min(vl) - 0.1)
            self._left_max = max(self._left_max, max(vl) + 0.1)
        all_r = []
        for d in self._right_series.values():
            all_r.extend(x for x in d if x is not None)
        if all_r:
            self._right_min = min(self._right_min, min(all_r) - 5)
            self._right_max = max(self._right_max, max(all_r) + 5)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        m = {"l": 55, "r": 25, "t": 28, "b": 30}
        pw, ph = w - m["l"] - m["r"], h - m["t"] - h * 0
        if pw <= 0 or ph <= 0:
            return
        p.fillRect(self.rect(), self._bg)
        p.setPen(QPen(self._grid, 1))
        p.drawRect(m["l"], m["t"], pw, ph)
        # 死区底色
        if self._deadband:
            dlo, dhi = self._deadband
            rng_l = self._left_max - self._left_min or 1e-6
            y_lo = m["t"] + int((self._left_max - dlo) / rng_l * (h - m["t"] - m["b"]))
            y_hi = m["t"] + int((self._left_max - dhi) / rng_l * (h - m["t"] - m["b"]))
            p.fillRect(m["l"], y_hi, pw, max(1, y_lo - y_hi), QColor(0xD9, 0x77, 0x06, 40))
        # 刻度
        f = QFont("Microsoft YaHei UI", 7); p.setFont(f)
        for i in range(5):
            y = m["t"] + (h - m["t"] - m["b"]) * i // 4
            p.setPen(QPen(self._grid, 1, Qt.PenStyle.DotLine))
            p.drawLine(m["l"], y, m["l"] + pw, y)
            p.setPen(self._text)
            lv = self._left_max - (i / 4) * (self._left_max - self._left_min)
            p.drawText(2, y + 4, f"{lv:.1f}")
            rv = self._right_max - (i / 4) * (self._right_max - self._right_min)
            p.drawText(w - m["r"] - 50, y + 4, f"{rv:.0f}")
        # 曲线
        lc = QColor("#1E293B") if self._left_label == "Hz" else QColor("#0891B2")
        self._draw_curve(p, self._left_data, self._left_min, self._left_max, m, pw, h - m["b"], lc, 2.0)
        for idx, pid in enumerate(sorted(self._right_series)):
            c = PCS_COLORS[idx % len(PCS_COLORS)]
            self._draw_curve(p, self._right_series[pid], self._right_min, self._right_max, m, pw, h - m["b"], c, 1.3)
        if self._show_total:
            self._draw_curve(p, self._right_total, self._right_min, self._right_max, m, pw, h - m["b"], QColor("#E74C3C"), 2.0)
        # 图例
        p.setFont(QFont("Microsoft YaHei UI", 8))
        x0, y0 = m["l"] + 6, m["t"] + 3
        p.setPen(lc); p.drawText(x0, y0 + 12, self._left_label); x0 += 60
        for idx, pid in enumerate(sorted(self._right_series)):
            c = PCS_COLORS[idx % len(PCS_COLORS)]
            p.setPen(c); p.drawText(x0, y0 + 12, f"PCS{pid}"); x0 += 45
        if self._show_total:
            p.setPen(QColor("#E74C3C")); p.drawText(x0, y0 + 12, "总功率")

    def _draw_curve(self, p, data, lo, hi, m, pw, ph, color, width):
        valid = [(i, v) for i, v in enumerate(data) if v is not None]
        if len(valid) < 2:
            return
        rng = hi - lo or 1.0
        xs = [m["l"] + (i / max(len(data) - 1, 1)) * pw for i, _ in valid]
        ys = [m["t"] + (hi - v) / rng * ph for _, v in valid]
        pen = QPen(color, width); p.setPen(pen)
        path = QPainterPath(); path.moveTo(xs[0], ys[0])
        for x, y in zip(xs[1:], ys[1:]):
            path.lineTo(x, y)
        p.draw_path = lambda pa: p.drawPath(pa)  # noqa
        p.drawPath(path)


# ══════════════════════════════════════════════════════════
# 调频场景 Tab
# ══════════════════════════════════════════════════════════

class FrequencyRegulationSceneTab(QWidget):
    """一次调频(PFR)多PCS联调测试界面。

    算法入口: _compute_pfr() —— 下垂公式 ΔP = -(1/Kd)*Pn*(Δf/fn)
    用户改这里就行。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dms = []
        self._monitoring = False
        self._log_prefix = "[PFR]"
        # 每台PCS的运行状态 {pcs_id: {freq, power, soc, delta_p, status, ...}}
        self._states = {}
        # 参数
        self.p_droop = 0.05
        self.p_deadband = 0.03
        self.p_rated = 100.0
        self.p_upper = 100.0
        self.p_lower = 0.0
        # 评价
        self._eval_resp_times = []     # 响应时间列表(ms)
        self._trigger_count = 0
        #
        self._build_ui()
        # UI刷新定时器 250ms
        self._timer_ui = QTimer(self)
        self._timer_ui.timeout.connect(self._on_ui_tick)
        self._timer_ui.start(250)
        # 控制定时器 500ms
        self._timer_ctrl = QTimer(self)
        self._timer_ctrl.timeout.connect(self._on_ctrl_tick)

    # ── 公共接口 ────────────────────────────────────

    def set_device_managers(self, dms):
        self._dms = dms
        self._states.clear()
        for dm in dms:
            self._states[dm.pcs_id] = {
                "freq": None, "power": None, "soc": None,
                "delta_p": 0, "status": "空闲",
                "trigger_t": None, "trigger_dp": None,
            }
        self._chart.init_series([dm.pcs_id for dm in dms])
        self._rebuild_table()
        names = [dm.pcs_name for dm in dms]
        self._log("INFO", f"已注册 {len(dms)} 台 PCS: {names}")

    def on_pcs_data(self, pcs_id, data_type, data):
        """MainWindow 路由进来的数据。"""
        s = self._states.get(pcs_id)
        if not s:
            return
        if data_type == "grid parameters":
            v = data.get("freq_grid")
            if v is not None:
                s["freq"] = v
        elif data_type == "battery parameters":
            v = data.get("p_bat")
            if v is not None:
                s["power"] = v / 1000.0   # W → kW
            v = data.get("soc_bat")
            if v is not None:
                s["soc"] = v

    def shutdown(self):
        self._monitoring = False
        self._timer_ui.stop()
        self._timer_ctrl.stop()

    # ── 启停控制 ────────────────────────────────────

    def start_monitoring(self):
        if not any(dm.is_connected for dm in self._dms):
            self._log("ERROR", "没有已连接的 PCS 设备"); return
        self._monitoring = True
        self._sync_params()
        self._reset_eval()
        self._chart.clear()
        self._btn_start.setEnabled(False); self._btn_stop.setEnabled(True)
        self._lbl_status.setText("测试中")
        self._lbl_status.setStyleSheet("color:#059669;font-weight:bold;font-size:9pt;")
        for r in range(self._table.rowCount()):
            self._table.item(r, 7).setText("就绪")
        self._timer_ctrl.start(500)
        self._log("INFO", "PFR 测试启动")

    def stop_monitoring(self):
        self._monitoring = False
        self._btn_start.setEnabled(True); self._btn_stop.setEnabled(False)
        self._lbl_status.setText("已停止")
        self._lbl_status.setStyleSheet("color:#909399;font-weight:bold;font-size:9pt;")
        for r in range(self._table.rowCount()):
            self._table.item(r, 7).setText("空闲")
        self._timer_ctrl.stop()
        self._log("INFO", "PFR 测试停止")

    def clear_scene(self):
        self._chart.clear()
        self._reset_eval()
        self._log("INFO", "录波已清除")

    # ── 算法核心（用户修改入口）────────────────────

    def _compute_pfr(self):
        """PFR 下垂控制计算。★ 用户替换此方法即可 ★"""
        commands = []
        fn = 50.0
        now = time.time()
        for pid, s in self._states.items():
            freq, power = s["freq"], s["power"]
            if freq is None or power is None:
                continue
            df = freq - fn
            dp = 0.0
            if abs(df) > self.p_deadband:
                df_e = df - (1 if df > 0 else -1) * self.p_deadband
                dp = -(1.0 / self.p_droop) * self.p_rated * (df_e / fn)
                dp = max(self.p_lower - self.p_rated, min(self.p_upper - self.p_rated, dp))

            if abs(dp) > 0.01:
                target_pct = (self.p_rated + dp) / self.p_rated * 100.0
                commands.append({
                    "pcs_id": pid,
                    "group": "basic setting parameters",
                    "params": {"active_power_regulation": round(target_pct, 1)},
                    "desc": f"PFR ΔP={dp:+.1f}kW",
                })
                s["delta_p"] = dp; s["status"] = "已下发"
            elif s["status"] not in ("未连接",):
                s["status"] = "待命中"
            # 响应时间记录
            if s["trigger_t"] is None and abs(df) > self.p_deadband:
                s["trigger_t"] = now; s["trigger_dp"] = dp; self._trigger_count += 1
            if s["trigger_t"] is not None and abs(power - self.p_rated) > 0.5:
                ms = (now - s["trigger_t"]) * 1000
                self._eval_resp_times.append(ms)
                s["trigger_t"] = None; s["trigger_dp"] = None
        return commands

    def _execute_commands(self, cmds):
        """下发指令到 DeviceManager。"""
        for cmd in cmds:
            dm = next((d for d in self._dms if d.pcs_id == cmd["pcs_id"]), None)
            if dm and hasattr(dm, 'enqueue_write_parameters'):
                try:
                    dm.enqueue_write_parameters(cmd["group"], cmd["params"])
                except Exception:
                    pass

    # ── 定时器回调 ──────────────────────────────────

    def _on_ctrl_tick(self):
        if not self._monitoring:
            return
        self._sync_params()
        cmds = self._compute_pfr()
        self._execute_commands(cmds)
        self._update_eval_panel()

    def _on_ui_tick(self):
        if not self._monitoring:
            return
        self._refresh_table()
        self._refresh_chart()

    # ── UI 构建 ────────────────────────────────────

    def _build_ui(self):
        o = QVBoxLayout(self); o.setContentsMargins(12, 12, 12, 12); o.setSpacing(10)
        o.addWidget(self._build_params())
        o.addWidget(self._build_table())
        o.addLayout(self._build_controls())
        o.addWidget(self._build_chart(), 2)
        row = QHBoxLayout(); row.setSpacing(10)
        row.addWidget(self._build_eval()); row.addWidget(self._build_log(), 1)
        o.addLayout(row)

    def _build_params(self) -> QGroupBox:
        g = QGroupBox("PFR 参数（所有 PCS 共用）")
        bar = QHBoxLayout(g); bar.setSpacing(16)
        self.sp_droop = self._spin("δ%", 0.005, 0.20, 3, "", 0.05)
        self.sp_db = self._spin("Δf_db", 0, 1.0, 3, " Hz", 0.03)
        self.sp_pn = self._spin("Pn", 1, 500, 1, " kW", 100)
        self.sp_pmax = self._spin("Pmax", 0, 500, 1, " kW", 100)
        self.sp_pmin = self._spin("Pmin", 0, 500, 1, " kW", 0)
        for lbl, sp in [self.sp_droop, self.sp_db, self.sp_pn, self.sp_pmax, self.sp_pmin]:
            bar.addWidget(lbl); bar.addWidget(sp)
        bar.addStretch(); return g

    def _spin(self, label, lo, hi, dec, suf, default):
        lbl = QLabel(label)
        lbl.setStyleSheet("font-weight:bold;color:#2F3E4C;")
        sp = QDoubleSpinBox(); sp.setRange(lo, hi); sp.setDecimals(dec)
        sp.setValue(default); sp.setSuffix(suf); sp.setFixedWidth(90)
        return lbl, sp

    def _build_table(self) -> QGroupBox:
        g = QGroupBox("各 PCS 实时监测")
        lay = QVBoxLayout(g); lay.setContentsMargins(8, 4, 8, 4)
        self._table = QTableWidget()
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        cols = ["PCS", "名称", "电网频率", "Δf", "当前功率", "期望ΔP", "SOC", "状态"]
        self._table.setColumnCount(len(cols)); self._table.setHorizontalHeaderLabels(cols)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.verticalHeader().setVisible(False)
        self._table.setMaximumHeight(28 * 6 + 26)
        self._table.setMinimumHeight(28 * 2 + 26)
        self._table.setStyleSheet(
            "QTableWidget{background:#FFF;border:1px solid #E4E7ED;border-radius:4px;gridline-color:#F0F0F0}"
            "\nQTableWidget::item{padding:3px 6px}"
            "\nQHeaderView::section{background:#F5F7FA;color:#4A5D73;font-weight:bold;font-size:9pt;"
            "border:none;border-bottom:2px solid #E4E7ED;padding:4px;}")
        lay.addWidget(self._table); return g

    def _rebuild_table(self):
        self._table.setRowCount(len(self._dms))
        for r, dm in enumerate(self._dms):
            items = [str(dm.pcs_id), dm.pcs_name, "-- Hz", "-- Hz",
                     "-- kW", "-- kW", "-- %",
                     "未连接" if not dm.is_connected else "就绪"]
            for c, t in enumerate(items):
                it = QTableWidgetItem(t)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(r, c, it)

    def _build_controls(self) -> QHBoxLayout:
        bar = QHBoxLayout(); bar.setSpacing(10)
        self._btn_start = QPushButton("▶ 开始测试"); self._btn_start.setFixedWidth(100)
        self._btn_start.clicked.connect(lambda: self.start_monitoring())
        self._btn_stop = QPushButton("■ 停止测试"); self._btn_stop.setFixedWidth(100)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(lambda: self.stop_monitoring())
        self._btn_clear = QPushButton("↺ 清除录波"); self._btn_clear.setFixedWidth(100)
        self._btn_clear.clicked.connect(lambda: self.clear_scene())
        bar.addWidget(self._btn_start); bar.addWidget(self._btn_stop); bar.addWidget(self._btn_clear)
        bar.addSpacing(16)
        self._chk_total = QCheckBox("显示总功率"); bar.addWidget(self._chk_total)
        bar.addStretch()
        self._lbl_status = QLabel("就绪")
        self._lbl_status.setStyleSheet("color:#909399;font-weight:bold;font-size:9pt;")
        bar.addWidget(self._lbl_status); return bar

    def _build_chart(self) -> QGroupBox:
        g = QGroupBox("录波曲线（频率 + 各 PCS 功率）")
        l = QVBoxLayout(g); l.setContentsMargins(8, 8, 8, 8)
        self._chart = WaveformChart(); l.addWidget(self._chart); return g

    def _build_eval(self) -> QGroupBox:
        g = QGroupBox("评价"); g.setFixedWidth(340)
        grid = QGridLayout(g); grid.setSpacing(6)
        headers = ["触发次数", "响应时间", "调节精度", "最大偏差"]
        for c, h in enumerate(headers):
            lb = QLabel(h); lb.setStyleSheet("color:#909399;font-size:8pt;")
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter); grid.addWidget(lb, 0, c)
        self._ev_labels = {}
        defaults = ["0", "-- ms", "-- %", "-- kW"]
        for c, t in enumerate(defaults):
            lb = QLabel(t)
            lb.setStyleSheet("font:14pt'Microsoft YaHei UI';color:#2F3E4C;font-weight:bold;")
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter); grid.addWidget(lb, 1, c)
            self._ev_labels[headers[c]] = lb
        return g

    def _build_log(self) -> QGroupBox:
        g = QGroupBox("日志")
        l = QVBoxLayout(g); l.setContentsMargins(8, 8, 8, 8)
        self._log_edit = QTextEdit(); self._log_edit.setReadOnly(True)
        self._log_edit.setStyleSheet(
            "QTextEdit{background:#1E293B;color:#A5B4CB;font-family:'Consolas','Microsoft YaHei UI';"
            "font-size:9pt;border:1px solid #D7DCE3;border-radius:2px;}")
        self._log_edit.setMaximumHeight(120)
        l.addWidget(self._log_edit); return g

    # ── 刷新 ────────────────────────────────────────

    def _refresh_table(self):
        for r in range(self._table.rowCount()):
            it = self._table.item(r, 0)
            if not it: continue
            pid = int(it.text())
            s = self._states.get(pid)
            if not s: continue
            vals = [
                f"{s['freq']:.2f} Hz" if s['freq'] is not None else "-- Hz",
                f"{s['freq'] - 50:+.2f} Hz" if s['freq'] is not None else "-- Hz",
                f"{s['power']:.1f} kW" if s['power'] is not None else "-- kW",
                f"{s['delta_p']:+.1f} kW",
                f"{s['soc']:.0f} %" if s['soc'] is not None else "-- %",
                s["status"],
            ]
            for c, t in enumerate(vals, start=2):
                item = self._table.item(r, c)
                if item: item.setText(t)

    def _refresh_chart(self):
        if not self._states: return
        freqs = [s["freq"] for s in self._states.values() if s["freq"] is not None]
        avg = sum(freqs) / len(freqs) if freqs else None
        powers = {pid: s["power"] for pid, s in self._states.items()}
        self._chart.append(time.time(), avg, powers)

    def _sync_params(self):
        self.p_droop = self.sp_droop.value()
        self.p_deadband = self.sp_db.value()
        self.p_rated = self.sp_pn.value()
        self.p_upper = self.sp_pmax.value()
        self.p_lower = self.sp_pmin.value()

    def _update_eval_panel(self):
        n = self._trigger_count
        avg = sum(self._eval_resp_times) / len(self._eval_resp_times) if self._eval_resp_times else 0
        self._ev_labels["触发次数"].setText(str(n))
        self._ev_labels["响应时间"].setText(f"{avg:.0f} ms" if n else "-- ms")

    def _reset_eval(self):
        self._eval_resp_times.clear(); self._trigger_count = 0
        self._ev_labels["触发次数"].setText("0")
        self._ev_labels["响应时间"].setText("-- ms")

    def _log(self, level, msg):
        ts = time.strftime("%H:%M:%S")
        if hasattr(self, '_log_edit'):
            self._log_edit.append(f"[{ts}]{self._log_prefix}[{level}] {msg}")


# ══════════════════════════════════════════════════════════
# 延迟导入其他场景
# ══════════════════════════════════════════════════════════

def _get_microgrid_tab():
    from .microgrid_scene_tab import MicrogridSceneTab as T
    return T

def _get_backup_tab():
    from .data_center_backup_scene_tab import DataCenterBackupSceneTab as T
    return T


# ══════════════════════════════════════════════════════════
# 场景应用外层容器
# ══════════════════════════════════════════════════════════

class SceneApplicationsTab(QWidget):
    """场景应用容器，管理调频/微电网/备电三个子Tab。"""

    def __init__(self, host, device_managers=None):
        super().__init__()
        self.host = host
        self._dms = device_managers or []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()

        t1 = FrequencyRegulationSceneTab(self)
        if self._dms: t1.set_device_managers(self._dms)
        self.tabs.addTab(t1, "调频场景")

        t2 = _get_microgrid_tab()(self)
        if self._dms: t2.set_device_managers(self._dms)
        self.tabs.addTab(t2, "微电网")

        t3 = _get_backup_tab()(self)
        if self._dms: t3.set_device_managers(self._dms)
        self.tabs.addTab(t3, "数据中心备电")

        layout.addWidget(self.tabs)

    def set_device_managers(self, dms):
        self._dms = dms
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if hasattr(t, 'set_device_managers'):
                t.set_device_managers(dms)

    def on_pcs_data(self, pcs_id, data_type, data):
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if hasattr(t, 'on_pcs_data'):
                t.on_pcs_data(pcs_id, data_type, data)

    def shutdown(self):
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if hasattr(t, 'shutdown'):
                t.shutdown()
