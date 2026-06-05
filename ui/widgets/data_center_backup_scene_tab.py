# -*- coding: utf-8 -*-
"""数据中心备电场景 Tab —— 掉电检测 + 备电时长预估。独立 QWidget，无框架依赖。算法入口: _compute_control()"""

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
MAX_P = 3000
PCS_C = [QColor("#0052D9"), QColor("#059669"), QColor("#D97706"), QColor("#DC2626"), QColor("#7C3AED")]
SOC_C = QColor("#0891B2"); LOAD_C = QColor("#E74C3C")


class _BKChart(QWidget):
    """备电录波：左轴SOC%，右轴功率kW。"""
    def __init__(self,p=None):
        super().__init__(p); self.setMinimumHeight(240)
        self._t=deque(maxlen=MAX_P); self._soc_avg=deque(maxlen=MAX_P)
        self._sc={}; self._pc={}; self._ld=deque(maxlen=MAX_P); self._show_ld=True
        self._smin,self._smax=0,100; self._pmin,self._pmax=-20,300
        self._bg=QColor("#F8F9FB"); self._gc=QColor("#E4E7ED"); self._tx=QColor("#909399")
        self._rz_c=QColor(0xD9,0x22,0x26,30); self._reserve=20.0

    def init_series(self,ids):
        self._sc={i:deque(maxlen=MAX_P) for i in ids}
        self._pc={i:deque(maxlen=MAX_P) for i in ids}
        self._soc_avg=deque(maxlen=MAX_P); self._ld=deque(maxlen=MAX_P)

    def append(self,t,socs,pows,lp=None,reserve=20.0):
        self._reserve=reserve; self._t.append(t)
        for i in self._sc:self._sc[i].append(socs.get(i))
        v=[x for x in socs.values()if x is not None]
        self._soc_avg.append(sum(v)/len(v) if v else None)
        for i in self._pc:self._pc[i].append(pows.get(i))
        v2=[x for x in pows.values()if x is not None]
        self._ld.append(sum(v2) if v2 else (lp if lp is not None else None))
        self._auto(); self.update()

    def clear(self):
        self._t.clear();self._soc_avg.clear()
        for d in list(self._sc.values())+list(self._pc.values()):d.clear();self._ld.clear()
        self._smin,self._smax=0,100;self._pmin,self._pmax=-20,300;self.update()

    def _auto(self):
        a=[]
        for d in self._sc.values():a.extend(x for x in d if x is not None)
        if a: self._smin=max(0,min(a)-5); self._smax=min(100,max(a)+5)
        b=[]
        for d in self._pc.values():b.extend(x for x in d if x is not None)
        if b: self._pmin=min(self._pmin,min(b)-5); self._pmax=max(self._pmax,max(b)+5)

    def paintEvent(self,_e):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=self.width(),self.height(); ml,mr,mt,mb=55,25,28,30; pw=w-ml-mr; ph=h-mt-mb
        if pw<=0 or ph<=0:return
        p.fillRect(self.rect(),self._bg); p.setPen(QPen(self._gc,1)); p.drawRect(ml,mt,pw,ph)
        # SOC保留区
        sr=self._smax-self._smin or 1e-6
        ry=mt+int((self._smax-self._reserve)/sr*ph)
        p.fillRect(ml,ry,pw,ph-(ry-mt),self._rz_c)
        p.setPen(QColor(0xD9,0x22,0x26,120)); p.setFont(QFont("Microsoft YaHei UI",7))
        p.drawText(ml+4,ry+10,f"SOC保留区 <{self._reserve:.0f}%")
        fnt=QFont("Microsoft YaHei UI",7); p.setFont(fnt)
        for i in range(5):
            y=mt+ph*i//4; p.setPen(QPen(self._gc,1,Qt.PenStyle.DotLine)); p.drawLine(ml,y,ml+pw,y)
            p.setPen(self._tx); p.drawText(2,y+4,f"{self._smax-(i/4)*(self._smax-self._smin):.0f}%")
            p.setPen(self._tx); p.drawText(w-mr-50,y+4,f"{self._pmax-(i/4)*(self._pmax-self._pmin):.0f}")
        def dr(d,lo,hi,c,wd):
            v=[(j,x)for j,x in enumerate(d)if x is not None]; ln=len(d)or 1
            if len(v)<2:return; r=hi-lo or 1
            xs=[ml+(j/ln)*pw for j, _ in v]; ys=[mt+(hi-x)/r*ph for _, x in v]
            pen=QPen(c,wd); p.setpen=pen; p.setPen(pen); pa=QPainterPath(); pa.moveTo(xs[0],ys[0])
            for x,y in zip(xs[1:],ys[1:]):pa.lineTo(x,y); p.drawPath(pa)
        dr(self._soc_avg,self._smin,self._smax,SOC_C,2.0)
        for idx,pid in enumerate(sorted(self._pc)):
            dr(self._pc[pid],self._pmin,self._pmax,PCS_C[idx%len(PCS_C)],1.3)
        if self._show_ld:dr(self._ld,self._pmin,self._pmax,LOAD_C,2.0)
        # 图例
        p.setFont(QFont("Microsoft YaHei UI",8)); x0,y0=ml+6,mt+3
        p.setPen(SOC_C);p.drawText(x0,y0+12,"SOC%");x0+=50
        for idx,pid in enumerate(sorted(self._pc)):
            p.setPen(PCS_C[idx%len(PCS_C)]);p.drawText(x0,y0+12,f"PCS{pid}");x0+=45
        if self._show_ld:p.setPen(LOAD_C);p.drawText(x0,y0+12,"负载")


class DataCenterBackupSceneTab(QWidget):

    def __init__(self,parent=None):
        super().__init__(parent)
        self._dms=[];self._monitoring=False;self._log_prefix="[备电]"
        self._states={};self._prev_gs={}
        self._pf_count=0;self._pf_durs=[]
        self._build_ui()
        self._tm_ui=QTimer(self);self._tm_ui.timeout.connect(self._on_ui_tick);self._tm_ui.start(250)
        self._tm_ctrl=QTimer(self);self._tm_ctrl.timeout.connect(self._on_ctrl_tick)

    def set_device_managers(self,dms):
        self._dms=dms;self._states.clear();self._prev_gs.clear()
        for dm in dms:
            pid=dm.pcs_id
            self._states[pid]={"soc":None,"bat_kw":None,"grid":"--","freq":None,
                               "load_p":None,"backup_s":None,"status":"空闲","pf_start":None}
            self._prev_gs[pid]="--"
        self._chart.init_series([dm.pcs_id for dm in dms]);self._rebuild_table()
        self._log("INFO",f"已注册 {len(dms)} 台 PCS: {[dm.pcs_name for dm in dms]}")

    def on_pcs_data(self,pcs_id,data_type,data):
        s=self._states.get(pcs_id)
        if not s:return
        if data_type=="grid parameters":
            freq=data.get("freq_grid");gs=str(data.get("grid_status_str","--"))
            if freq is not None:s["freq"]=freq;s["grid"]=gs
            prev=self._prev_gs.get(pcs_id,"--")
            if prev=="并网" and gs=="离网":
                s["pf_start"]=time.time();self._pf_count+=1;s["status"]="掉电"
                self._log("INFO",f"[PCS{pcs_id}] 市电掉电!")
            elif prev=="离网" and gs=="并网":
                if s["pf_start"]is not None:
                    ms=(time.time()-s["pf_start"])*1000;self._pf_durs.append(ms)
                    s["status"]=f"恢复({ms:.0f}ms)"
                s["pf_start"]=None
            self._prev_gs[pcs_id]=gs
        elif data_type=="battery parameters":
            soc=data.get("soc_bat");pb=data.get("p_bat")
            if soc is not None:s["soc"]=soc
            if pb is not None:s["bat_kw"]=abs(pb)/1000.0
            self._estimate(s)
        elif data_type=="load parameters":
            pl=data.get("p_load_total")
            if pl is not None:s["load_p"]=pl;self._estimate(s)

    def shutdown(self):
        self._monitoring=False;self._tm_ui.stop();self._tm_ctrl.stop()

    def _estimate(self,s):
        """T=SOC*C*3600/P (s)"""
        if s["soc"]is None or s["load_p"]is None:
            s["backup_s"]=None;return
        cap=self.sp_cap.value()  # kWh
        soc_pct=s["soc"]/100.0
        load_kw=s["load_p"]/1000.0 if s["load_p"]else 0
        if load_kw<=0:s["backup_s"]=None;return
        s["backup_s"]=soc_pct*cap*3600.0/load_kw

    # ── 启停 ────────────────────────────────

    def start_monitoring(self):
        if not any(dm.is_connected for dm in self._dms):
            self._log("ERROR","无连接设备");return
        self._monitoring=True;self._reset_ev();self._chart.clear()
        self._btn_s.setEnabled(False);self._btn_t.setEnabled(True)
        self._lbl.setText("监测中");self._lbl.setStyleSheet("color:#059669;font-weight:bold;font-size:9pt")
        for r in range(self._tbl.rowCount()):self._tbl.item(r,7).setText("就绪")
        self._tm_ctrl.start(500);self._log("INFO","备电监测启动")

    def stop_monitoring(self):
        self._monitoring=False;self._btn_s.setEnabled(True);self._btn_t.setEnabled(False)
        self._lbl.setText("已停止");self._lbl.setStyleSheet("color:#909399;font-weight:bold;font-size:9pt")
        for r in range(self._tbl.rowCount()):self._tbl.item(r,7).setText("空闲")
        self._tm_ctrl.stop();self._log("INFO","监测停止")

    def clear_scene(self):
        self._chart.clear();self._reset_ev();self._log("INFO","已清除")

    # ── 算法入口 ──────────────────────────

    def _compute_control(self):
        """★ 用户替换此方法 ★ 返回 [{pcs_id,group,params,desc}]"""
        cmds=[]
        reserve=self.sp_resv.value()
        for pid,s in self._states.items():
            if s["soc"]is not None and s["soc"]<reserve:
                s["status"]="SOC过低报警"
            # 示例：掉电时下发最大放电（注释状态，用户自行打开）
            # if s["grid"]=="离网"and s["pf_start"]is not None:
            #     cmds.append({"pcs_id":pid,"group":"basic setting parameters",
            #         "params":{"active_power_regulation":100.0},"desc":"备电最大放电"})
        return cmds

    def _execute(self,cmds):
        for c in cmds:
            dm=next((d for d in self._dms if d.pcs_id==c["pcs_id"]),None)
            if dm and hasattr(dm,'enqueue_write_parameters'):
                try:dm.enqueue_write_parameters(c["group"],c["params"])
                except Exception:pass

    # ── 定时器 ────────────────────────────

    def _on_ctrl_tick(self):
        if not self._monitoring:return
        cmds=self._compute_control();self._execute(cmds);self._update_ev()

    def _on_ui_tick(self):
        if not self._monitoring:return
        self._refresh_table();self._refresh_chart()

    # ── UI ────────────────────────────────

    def _build_ui(self):
        o=QVBoxLayout(self);o.setContentsMargins(12,12,12,12);o.setSpacing(10)
        o.addWidget(self._ui_params());o.addWidget(self._ui_table())
        o.addLayout(self._ui_bar());o.addWidget(self._ui_chart(),2)
        r=QHBoxLayout();r.setSpacing(10);r.addWidget(self._ui_eval());r.addWidget(self._ui_log(),1);o.addLayout(r)

    def _ui_params(self):
        g=QGroupBox("备电参数");bar=QHBoxLayout(g);bar.setSpacing(16)
        l1=QLabel("电池容量");l1.setStyleSheet("font-weight:bold;color:#2F3E4C")
        self.sp_cap=QDoubleSpinBox();self.sp_cap.setRange(1,10000);self.sp_cap.setValue(100);self.sp_cap.setSuffix(" kWh");self.sp_cap.setFixedWidth(90)
        bar.addWidget(l1);bar.addWidget(self.sp_cap)
        l2=QLabel("SOC保留%");l2.setStyleSheet("font-weight:bold;color:#2F3E4C")
        self.sp_resv=QDoubleSpinBox();self.sp_resv.setRange(1,100);self.sp_resv.setValue(20);self.sp_resv.setSuffix(" %");self.sp_resv.setFixedWidth(90)
        bar.addWidget(l2);bar.addWidget(self.sp_resv)
        l3=QLabel("关键负载");l3.setStyleSheet("font-weight:bold;color:#2F3E4C")
        self.sp_load=QDoubleSpinBox();self.sp_load.setRange(0,10000);self.sp_load.setValue(50);self.sp_load.setSuffix(" kW");self.sp_load.setFixedWidth(90)
        bar.addWidget(l3);bar.addWidget(self.sp_load)
        l4=QLabel("检测超时");l4.setStyleSheet("font-weight:bold;color:#2F3E4C")
        self.sp_to=QDoubleSpinBox();self.sp_to.setRange(50,10000);self.sp_to.setValue(500);self.sp_to.setSuffix(" ms");self.sp_to.setFixedWidth(90)
        bar.addWidget(l4);bar.addWidget(self.sp_to);bar.addStretch();return g

    def _ui_table(self):
        g=QGroupBox("各 PCS 实时监测");lay=QVBoxLayout(g);lay.setContentsMargins(8,4,8,4)
        self._tbl=QTableWidget();self._tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self._tbl.setSelectionBehavior(QTableWidget.SelectRows);self._tbl.setAlternatingRowColors(True)
        cols=["PCS","名称","电网状态","SOC(%)","备电时长(s)","负载(kW)","状态"]
        self._tbl.setColumnCount(len(cols));self._tbl.setHorizontalHeaderLabels(cols)
        hdr=self._tbl.horizontalHeader();hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter);self._tbl.verticalHeader().setVisible(False)
        self._tbl.setMaximumHeight(28*6+26);self._tbl.setMinimumHeight(28*2+26)
        self._tbl.setStyleSheet(
            "QTableWidget{background:#FFF;border:1px solid #E4E7ED;border-radius:4px;gridline-color:#F0F0F0}"
            "\nQTableWidget::item{padding:3px 6px}"
            "\nQHeaderView::section{background:#F5F7FA;color:#4A5D73;font-weight:bold;font-size:9pt;"
            "border:none;border-bottom:2px solid #E4E7ED;padding:4px;}")
        lay.addWidget(self._tbl);return g

    def _rebuild_table(self):
        self._tbl.setRowCount(len(self._dms))
        for r,dm in enumerate(self._dms):
            st="就绪" if dm.is_connected else "未连接"
            items=[str(dm.pcs_id),dm.pcs_name,"--","-- %","-- s","-- kW",st]
            for c,t in enumerate(items):
                it=QTableWidgetItem(t);it.setTextAlignment(Qt.AlignmentFlag.AlignCenter);self._tbl.setItem(r,c,it)

    def _ui_bar(self):
        bar=QHBoxLayout();bar.setSpacing(10)
        self._btn_s=QPushButton("▶ 开始监测");self._btn_s.setFixedWidth(100);self._btn_s.clicked.connect(lambda:self.start_monitoring())
        self._btn_t=QPushButton("■ 停止");self._btn_t.setFixedWidth(100);self._btn_t.setEnabled(False);self._btn_t.clicked.connect(lambda:self.stop_monitoring())
        self._btn_c=QPushButton("↺ 清除");self._btn_c.setFixedWidth(100);self._btn_c.clicked.connect(lambda:self.clear_scene())
        bar.addWidget(self._btn_s);bar.addWidget(self._btn_t);bar.addWidget(self._btn_c);bar.addSpacing(16)
        self._chk=QCheckBox("显示负载功率");bar.addWidget(self._chk);bar.addStretch()
        self._lbl=QLabel("就绪");self._lbl.setStyleSheet("color:#909399;font-weight:bold;font-size:9pt")
        bar.addWidget(self._lbl);return bar

    def _ui_chart(self):
        g=QGroupBox("录波曲线（SOC% + 各 PCS 功率）");l=QVBoxLayout(g);l.setContentsMargins(8,8,8,8)
        self._chart=_BKChart();l.addWidget(self._chart);return g

    def _ui_eval(self):
        g=QGroupBox("评价指标");g.setFixedWidth(340);grid=QGridLayout(g);grid.setSpacing(6)
        hs=["掉电次数","平均掉电时长","最大掉电时长","最小备电时长"]
        for c,h in enumerate(hs):
            lb=QLabel(h);lb.setStyleSheet("color:#909399;font-size:8pt");lb.setAlignment(Qt.AlignmentFlag.AlignCenter);grid.addWidget(lb,0,c)
        self._ev={}
        ds=["0","-- ms","-- ms","-- s"]
        for c,t in enumerate(ds):
            lb=QLabel(t);lb.setStyleSheet("font:14pt'Microsoft YaHei UI';color:#2F3E4C;font-weight:bold")
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter);grid.addWidget(lb,1,c);self._ev[hs[c]]=lb
        return g

    def _ui_log(self):
        g=QGroupBox("日志");l=QVBoxLayout(g);l.setContentsMargins(8,8,8,8)
        self._le=QTextEdit();self._le.setReadOnly(True)
        self._le.setStyleSheet("QTextEdit{background:#1E293B;color:#A5B4CB;font-family:'Consolas','Microsoft YaHei UI';font-size:9pt;border:1px solid #D7DCE3;border-radius:2px;}")
        self._le.setMaximumHeight(120);l.addWidget(self._le);return g

    # ── 刷新 ────────────────────────────────

    def _refresh_table(self):
        for r in range(self._tbl.rowCount()):
            it=self._tbl.item(r,0)
            if not it:continue
            pid=int(it.text());s=self._states.get(pid)
            if not s:continue
            vals=[s["grid"]or"--",
                  f"{s['soc']:.0f} %"if s["soc"]is not None else"-- %",
                  f"{s['backup_s']:.0f} s"if s["backup_s"]is not None else"-- s",
                  f"{s['load_p']/1000:.1f} kW"if s["load_p"]is not None else"-- kW",
                  s["status"]]
            for c,t in enumerate(vals,start=2):
                item=self._tbl.item(r,c)
                if item:item.setText(t)
                # SOC过低标红
                if c==2 and s["soc"]is not None and s["soc"]<self.sp_resv.value():
                    item.setForeground(QColor("#DC2626"))

    def _refresh_chart(self):
        if not self._states:return
        socs={pid:s["soc"]for pid,s in self._states.items()}
        pws={pid:s["bat_kw"]for pid,s in self._states.items()}
        lp=next((s["load_p"]for s in self._states.values()if s["load_p"]is not None),None)
        self._chart.append(time.time(),socs,pws,lp,self.sp_resv.value())

    def _update_ev(self):
        n=self._pf_count; avg=sum(self._pf_durs)/len(self._pf_durs)if self._pf_durs else 0
        mx=max(self._pf_durs)if self._pf_durs else 0
        bk=[s["backup_s"]for s in self._states.values()if s["backup_s"]is not None]
        mn=min(bk)if bk else None
        self._ev["掉电次数"].setText(str(n))
        self._ev["平均掉电时长"].setText(f"{avg:.0f} ms")
        self._ev["最大掉电时长"].setText(f"{mx:.0f} ms")
        self._ev["最小备电时长"].setText(f"{mn:.0f} s"if mn is not None else"-- s")

    def _reset_ev(self):
        self._pf_durs.clear();self._pf_count=0
        _m = {"掉电次数":"0","平均掉电时长":"-- ms","最大掉电时长":"-- ms","最小备电时长":"-- s"}
        for k in self._ev: self._ev[k].setText(_m.get(k,"--"))

    def _log(self,lvl,msg):
        ts=time.strftime("%H:%M:%S")
        if hasattr(self,'_le'):self._le.append(f"[{ts}]{self._log_prefix}[{lvl}] {msg}")
