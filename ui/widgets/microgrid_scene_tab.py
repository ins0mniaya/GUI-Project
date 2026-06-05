# -*- coding: utf-8 -*-
"""微电网场景 Tab —— 并/离网切换 + SOC负荷分配。独立 QWidget，无框架依赖。算法入口: _compute_control()"""

from __future__ import annotations
import time
from collections import deque
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel,
    QGridLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget, QHeaderView,
)
MAX_POINTS = 3000
PCS_COLORS = [QColor("#0052D9"), QColor("#059669"), QColor("#D97706"), QColor("#DC2626"), QColor("#7C3AED")]


class _MGChart(QWidget):
    """微电网录波：左轴频率Hz，右轴功率kW（含负荷）。"""
    def __init__(self, p=None):
        super().__init__(p); self.setMinimumHeight(240)
        self._t = deque(maxlen=MAX_POINTS); self._freq = deque(maxlen=MAX_POINTS)
        self._pw = {}; self._load = deque(maxlen=MAX_POINTS); self._show_load = True
        self._fmin, self._fmax = 49.5, 50.5; self._pmin, self._pmax = -20, 600
        self._bg = QColor("#F8F9FB"); self._grid_c = QColor("#E4E7ED"); self._tx = QColor("#909399")

    def init_series(self, ids):
        self._pw = {i: deque(maxlen=MAX_POINTS) for i in ids}; self._load = deque(maxlen=MAX_POINTS)

    def append(self, t, f, ps, lp=None):
        self._t.append(t); self._freq.append(f)
        for i in self._pw: self._pw[i].append(ps.get(i))
        v = [x for x in ps.values() if x is not None]
        self._load.append(sum(v) if v else (lp if lp is not None else None))
        self._auto(); self.update()

    def clear(self):
        self._t.clear(); self._freq.clear()
        for d in self._pw.values(): d.clear(); self._load.clear()
        self._pmin, self._pmax = -20, 600; self._fmin, self._fmax = 49.5, 50.5; self.update()

    def _auto(self):
        v = [x for x in self._freq if x is not None]
        if v: self._fmin = min(self._fmin, min(v)-0.1); self._fmax = max(self._fmax, max(v)+0.1)
        a = []
        for d in self._pw.values(): a.extend(x for x in d if x is not None)
        a.extend(x for x in self._load if x is not None)
        if a: self._pmin = min(self._pmin, min(a)-5); self._pmax = max(self._pmax, max(a)+5)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ml, mr, mt, mb = 55, 25, 28, 30; pw = w-ml-mr; ph = h-mt-mb
        if pw <= 0 or ph <= 0: return
        p.fillRect(self.rect(), self._bg); p.setPen(QPen(self._grid_c, 1)); p.drawRect(ml, mt, pw, ph)
        fnt = QFont("Microsoft YaHei UI", 7); p.setFont(fnt)
        for i in range(5):
            y = mt + ph*i//4; p.setPen(QPen(self._grid_c, 1, Qt.PenStyle.DotLine)); p.drawLine(ml, y, ml+pw, y)
            p.setPen(self._tx); p.drawText(2, y+4, f"{self._fmax-(i/4)*(self._fmax-self._fmin):.1f}")
            p.setPen(self._tx); p.drawText(w-mr-50, y+4, f"{self._pmax-(i/4)*(self._pmax-self._pmin):.0f}")
        # 曲线
        def draw(d, lo, hi, c, wd):
            v = [(j,x) for j,x in enumerate(d) if x is not None]; ln = len(d) or 1
            if len(v)<2: return
            r = hi-lo or 1; xs=[ml+(j/ln)*pw for j, _ in v]; ys=[mt+(hi-x)/r*ph for _, x in v]
            pen=QPen(c,wd); p.setPen(pen); path=QPainterPath(); path.moveTo(xs[0],ys[0])
            for x,y in zip(xs[1:],ys[1:]): path.lineTo(x,y); p.drawPath(path)
        draw(self._freq, self._fmin, self._fmax, QColor("#1E293B"), 2.0)
        for idx, pid in enumerate(sorted(self._pw)):
            draw(self._pw[pid], self._pmin, self._pmax, PCS_COLORS[idx%len(PCS_COLORS)], 1.3)
        if self._show_load: draw(self._load, self._pmin, self._pmax, QColor("#E74C3C"), 2.0)
        # 图例
        p.setFont(QFont("Microsoft YaHei UI", 8)); x0, y0 = ml+6, mt+3
        p.setPen(QColor("#1E293B")); p.drawText(x0, y0+12, "频率 Hz"); x0 += 60
        for idx, pid in enumerate(sorted(self._pw)):
            p.setPen(PCS_COLORS[idx%len(PCS_COLORS)]); p.drawText(x0, y0+12, f"PCS{pid}"); x0 += 50
        if self._show_load:
            p.setPen(QColor("#E74C3C")); p.drawText(x0, y0+12, "负荷")


class MicrogridSceneTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dms = []; self._monitoring = False; self._log_prefix = "[微电网]"
        self._states = {}; self._prev_grid = {}
        self._switch_count = 0; self._switch_times = []
        self._build_ui()
        self._tm_ui = QTimer(self); self._tm_ui.timeout.connect(self._on_ui_tick); self._tm_ui.start(250)
        self._tm_ctrl = QTimer(self); self._tm_ctrl.timeout.connect(self._on_ctrl_tick)

    def set_device_managers(self, dms):
        self._dms = dms; self._states.clear(); self._prev_grid.clear()
        for dm in dms:
            pid = dm.pcs_id
            self._states[pid] = {"freq":None,"power":None,"soc":None,"load_p":None,
                                 "grid_status":"--","status":"空闲","sw_start":None}
            self._prev_grid[pid] = "--"
        self._chart.init_series([dm.pcs_id for dm in dms]); self._rebuild_table()
        self._log("INFO", f"已注册 {len(dms)} 台 PCS: {[dm.pcs_name for dm in dms]}")

    def on_pcs_data(self, pcs_id, data_type, data):
        s = self._states.get(pcs_id)
        if not s: return
        if data_type == "grid parameters":
            freq = data.get("freq_grid"); gs = str(data.get("grid_status_str", "--"))
            if freq is not None: s["freq"] = freq
            old = s["grid_status"]; s["grid_status"] = gs
            prev = self._prev_grid.get(pcs_id, "--")
            if prev != "--" and prev != gs:
                now = time.time()
                if s["sw_start"] is not None:
                    ms = (now-s["sw_start"]*1000); self._switch_count+=1; self._switch_times.append(ms)
                    s["status"]=f"切换({ms:.0f}ms)"
                self._prev_grid[pcs_id]=gs
            elif s["sw_start"] is None and prev != "--" and prev != gs:
                s["sw_start"]=time.time(); s["status"]="切换中..."
        elif data_type == "battery parameters":
            soc = data.get("soc_bat"); pb = data.get("p_bat")
            if soc is not None: s["soc"]=soc
            if pb is not None: s["power"]=pb/1000.0
        elif data_type == "load parameters":
            pl = data.get("p_load_total")
            if pl is not None: s["load_p"]=pl

    def shutdown(self):
        self._monitoring=False; self._tm_ui.stop(); self._tm_ctrl.stop()

    # ── 启停 ────────────────────────────────

    def start_monitoring(self):
        if not any(dm.is_connected for dm in self._dms):
            self._log("ERROR","无连接设备");return
        self._monitoring=True; self._reset_eval(); self._chart.clear()
        self._btn_s.setEnabled(False); self._btn_t.setEnabled(True)
        self._lbl.setText("监测中"); self._lbl.setStyleSheet("color:#059669;font-weight:bold;font-size:9pt")
        for r in range(self._tbl.rowCount()): self._tbl.item(r,6).setText("就绪")
        self._tm_ctrl.start(500); self._log("INFO","微电网监测启动")

    def stop_monitoring(self):
        self._monitoring=False; self._btn_s.setEnabled(True); self._btn_t.setEnabled(False)
        self._lbl.setText("已停止"); self._lbl.setStyleSheet("color:#909399;font-weight:bold;font-size:9pt")
        for r in range(self._tbl.rowCount()): self._tbl.item(r,6).setText("空闲")
        self._tm_ctrl.stop(); self._log("INFO","监测停止")

    def clear_scene(self):
        self._chart.clear(); self._reset_eval(); self._log("INFO","已清除")

    # ── 算法入口 ──────────────────────────

    def _compute_control(self):
        """★ 用户替换此方法 ★ 返回 [{pcs_id, group, params, desc}]"""
        cmds = []
        # 示例：离网时按SOC分配负荷
        offline = {pid:s for pid,s in self._states.items() if s["grid_status"]=="离网"}
        if not offline: return cmds
        total_load = sum(s["load_p"] or 0 for s in offline.values())
        total_soc = sum(s["soc"] or 0 for s in offline.values())
        if total_load <= 0 or total_soc <= 0: return cmds
        for pid, s in offline.items():
            w = (s["soc"] or 0)/total_soc
            target = total_load * w
            pct = min(100, max(0, target/500*100))  # 简化计算
            cmds.append({"pcs_id":pid,"group":"basic setting parameters",
                         "params":{"active_power_regulation":round(pct,1)},
                         "desc":f"负荷分配 {pct:.1f}%"})
            s["status"]="调节中"
        return cmds

    def _execute(self, cmds):
        for c in cmds:
            dm = next((d for d in self._dms if d.pcs_id==c["pcs_id"]), None)
            if dm and hasattr(dm,'enqueue_write_parameters'):
                try: dm.enqueue_write_parameters(c["group"],c["params"])
                except Exception: pass

    # ── 定时器 ────────────────────────────

    def _on_ctrl_tick(self):
        if not self._monitoring: return
        cmds = self._compute_control(); self._execute(cmds); self._update_eval()

    def _on_ui_tick(self):
        if not self._monitoring: return
        self._refresh_table(); self._refresh_chart()

    # ── UI ────────────────────────────────

    def _build_ui(self):
        o=QVBoxLayout(self); o.setContentsMargins(12,12,12,12); o.setSpacing(10)
        o.addWidget(self._ui_params()); o.addWidget(self._ui_table())
        o.addLayout(self._ui_bar()); o.addWidget(self._ui_chart(),2)
        r=QHBoxLayout(); r.setSpacing(10); r.addWidget(self._ui_eval()); r.addWidget(self._ui_log(),1); o.addLayout(r)

    def _ui_params(self):
        g=QGroupBox("微电网参数"); bar=QHBoxLayout(g); bar.setSpacing(16)
        l1=QLabel("切换死区");l1.setStyleSheet("font-weight:bold;color:#2F3E4C")
        self.sp_db=QDoubleSpinBox();self.sp_db.setRange(50,2000);self.sp_db.setDecimals(0);self.sp_db.setValue(200);self.sp_db.setSuffix(" ms");self.sp_db.setFixedWidth(90)
        bar.addWidget(l1);bar.addWidget(self.sp_db)
        l2=QLabel("SOC权重");l2.setStyleSheet("font-weight:bold;color:#2F3E4C")
        self.sp_soc=QDoubleSpinBox();self.sp_soc.setRange(0.1,5);self.sp_soc.setDecimals(1);self.sp_soc.setValue(1.0);self.sp_soc.setFixedWidth(90)
        bar.addWidget(l2);bar.addWidget(self.sp_soc)
        l3=QLabel("总额定kW");l3.setStyleSheet("font-weight:bold;color:#2F3E4C")
        self.sp_pt=QDoubleSpinBox();self.sp_pt.setRange(10,5000);self.sp_pt.setDecimals(0);self.sp_pt.setValue(500);self.sp_pt.setSuffix(" kW");self.sp_pt.setFixedWidth(90)
        bar.addWidget(l3);bar.addWidget(self.sp_pt); bar.addStretch(); return g

    def _ui_table(self):
        g=QGroupBox("各 PCS 实时监测"); lay=QVBoxLayout(g); lay.setContentsMargins(8,4,8,4)
        self._tbl=QTableWidget(); self._tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self._tbl.setSelectionBehavior(QTableWidget.SelectRows); self._tbl.setAlternatingRowColors(True)
        cols=["PCS","名称","电网状态","频率","负荷功率","电池功率","SOC","状态"]
        self._tbl.setColumnCount(len(cols));self._tbl.setHorizontalHeaderLabels(cols)
        hdr=self._tbl.horizontalHeader();hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter);self._tbl.verticalHeader().setVisible(False)
        self._tbl.setMaximumHeight(28*6+26);self._tbl.setMinimumHeight(28*2+26)
        self._tbl.setStyleSheet(
            "QTableWidget{background:#FFF;border:1px solid #E4E7ED;border-radius:4px;gridline-color:#F0F0F0}"
            "\nQTableWidget::item{padding:3px 6px}"
            "\nQHeaderView::section{background:#F5F7FA;color:#4A5D73;font-weight:bold;font-size:9pt;"
            "border:none;border-bottom:2px solid #E4E7ED;padding:4px;}")
        lay.addWidget(self._tbl); return g

    def _rebuild_table(self):
        self._tbl.setRowCount(len(self._dms))
        for r,dm in enumerate(self._dms):
            st="就绪" if dm.is_connected else "未连接"
            items=[str(dm.pcs_id),dm.pcs_name,"--","-- Hz","-- kW","-- kW","-- %",st]
            for c,t in enumerate(items):
                it=QTableWidgetItem(t);it.setTextAlignment(Qt.AlignmentFlag.AlignCenter);self._tbl.setItem(r,c,it)

    def _ui_bar(self):
        bar=QHBoxLayout(); bar.setSpacing(10)
        self._btn_s=QPushButton("▶ 开始监测");self._btn_s.setFixedWidth(100);self._btn_s.clicked.connect(lambda:self.start_monitoring())
        self._btn_t=QPushButton("■ 停止");self._btn_t.setFixedWidth(100);self._btn_t.setEnabled(False);self._btn_t.clicked.connect(lambda:self.stop_monitoring())
        self._btn_c=QPushButton("↺ 清除");self._btn_c.setFixedWidth(100);self._btn_c.clicked.connect(lambda:self.clear_scene())
        bar.addWidget(self._btn_s);bar.addWidget(self._btn_t);bar.addWidget(self._btn_c); bar.addSpacing(16)
        self._chk=QCheckBox("显示负荷功率");bar.addWidget(self._chk); bar.addStretch()
        self._lbl=QLabel("就绪");self._lbl.setStyleSheet("color:#909399;font-weight:bold;font-size:9pt")
        bar.addWidget(self._lbl); return bar

    def _ui_chart(self):
        g=QGroupBox("录波曲线（频率 + 各 PCS 功率）"); l=QVBoxLayout(g); l.setContentsMargins(8,8,8,8)
        self._chart=_MGChart(); l.addWidget(self._chart); return g

    def _ui_eval(self):
        g=QGroupBox("评价指标");g.setFixedWidth(340); grid=QGridLayout(g); grid.setSpacing(6)
        hs=["切换次数","平均切换时间","最大切换时间"]
        for c,h in enumerate(hs):
            lb=QLabel(h);lb.setStyleSheet("color:#909399;font-size:8pt");lb.setAlignment(Qt.AlignmentFlag.AlignCenter);grid.addWidget(lb,0,c)
        self._ev={}
        ds=["0","-- ms","-- ms"]
        for c,t in enumerate(ds):
            lb=QLabel(t);lb.setStyleSheet("font:14pt'Microsoft YaHei UI';color:#2F3E4C;font-weight:bold")
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter);grid.addWidget(lb,1,c); self._ev[hs[c]]=lb
        return g

    def _ui_log(self):
        g=QGroupBox("日志"); l=QVBoxLayout(g); l.setContentsMargins(8,8,8,8)
        self._le=QTextEdit();self._le.setReadOnly(True)
        self._le.setStyleSheet("QTextEdit{background:#1E293B;color:#A5B4CB;font-family:'Consolas','Microsoft YaHei UI';font-size:9pt;border:1px solid #D7DCE3;border-radius:2px;}")
        self._le.setMaximumHeight(120); l.addWidget(self._le); return g

    # ── 刷新 ────────────────────────────────

    def _refresh_table(self):
        for r in range(self._tbl.rowCount()):
            it=self._tbl.item(r,0)
            if not it: continue
            pid=int(it.text());s=self._states.get(pid)
            if not s: continue
            vals=[s["grid_status"]or"--",
                  f"{s['freq']:.2f} Hz"if s["freq"]is not None else"-- Hz",
                  f"{s['load_p']:.1f} kW"if s["load_p"]is not None else"-- kW",
                  f"{s['power']:.1f} kW"if s["power"]is not None else"-- kW",
                  f"{s['soc']:.0f} %"if s["soc"]is not None else"-- %",
                  s["status"]]
            for c,t in enumerate(vals,start=2):
                item=self._tbl.item(r,c)
                if item: item.setText(t)

    def _refresh_chart(self):
        if not self._states: return
        freqs=[s["freq"]for s in self._states.values()if s["freq"]is not None]
        avg=sum(freqs)/len(freqs)if freqs else None
        powers={pid:s["power"]for pid,s in self._states.items()}
        lp=next((s["load_p"]for s in self._states.values()if s["load_p"]is not None),None)
        self._chart.append(time.time(),avg,powers,lp)

    def _update_eval(self):
        n=self._switch_count; avg=sum(self._switch_times)/len(self._switch_times)if self._switch_times else 0
        mx=max(self._switch_times)if self._switch_times else 0
        self._ev["切换次数"].setText(str(n))
        self._ev["平均切换时间"].setText(f"{avg:.0f} ms")
        self._ev["最大切换时间"].setText(f"{mx:.0f} ms")

    def _reset_eval(self):
        self._switch_times.clear();self._switch_count=0
        for k in self._ev:self._ev[k].setText(["0","-- ms","-- ms"][["切换次数","平均切换时间","最大切换时间"].index(k)]if k in self._ev else "--")

    def _log(self,lvl,msg):
        ts=time.strftime("%H:%M:%S")
        if hasattr(self,'_le'):self._le.append(f"[{ts}]{self._log_prefix}[{lvl}] {msg}")
