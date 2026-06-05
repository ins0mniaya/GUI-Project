# data_storage.py
# -*- coding: utf-8 -*-
"""
EMS 数据存储引擎

功能：
  1. 可配置选择存储哪些数据类型（电池参数 / BMS组参数 / 单体电压 / PV / 电网 / 负载）
  2. 降采样写入（默认每 10 次轮询存一条，约 3 秒）
  3. CSV 按设备 + 日期分文件，单体电压单独存（一行 16 列 + pcs_id）
  4. 定期自动删除过期文件（默认 15 天，可配置）

用法：
    engine = DataStorageEngine(storage_dir="./runing_data/storage")
    engine.set_config(enabled=True, sample_interval=10, retention_days=15)
    engine.set_data_types(["battery", "bms_group1", "cell_voltage"])
    engine.on_data_received(pcs_id, data_type, data_dict)   # 每次轮询调用
"""

import csv
import glob
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DataStorageEngine:
    """数据存储引擎：接收轮询数据，按配置降采样后写入 CSV 文件。"""

    # ── 数据类型 → (CSV文件名前缀, 需要提取的字段列表) ──────────────────
    FIELD_MAP = {
        "battery": (
            "battery",
            [
                "v_bat", "i_bat", "p_bat", "soc_bat", "temp_bat",
                "bat_status", "e_bat_chg_day", "e_bat_dis_day",
            ],
        ),
        "bms_group1": (
            "bms_group1",
            [
                "v_cell_mean", "i_cell_total", "soc_bms", "soh_bms",
                "temp_cell_avg", "charging_voltage", "discharge_voltage",
                "charging_current_limiting", "discharge_current_limiting",
                "module_numbers",
            ],
        ),
        # 单体电压特殊处理（CAN 数据），一行 16 列
        "cell_voltage": ("cell_voltage", None),
        # 以下可选
        "pv": (
            "pv",
            ["p_pv_total", "e_pv_day", "v_pv1", "i_pv1", "p_pv1",
             "v_pv2", "i_pv2", "p_pv2", "v_pv3", "i_pv3", "p_pv3"],
        ),
        "grid": (
            "grid",
            ["p_grid", "freq_grid", "v_grid_a", "v_grid_b", "v_grid_c",
             "i_grid_a", "i_grid_b", "i_grid_c"],
        ),
        "load": (
            "load",
            ["p_load_total", "e_load_day", "v_load_l1", "p_load_l1",
             "i_load_l1", "freq_load_1"],
        ),
    }

    def __init__(self, storage_dir: str = "./data/storage"):
        self._storage_dir = storage_dir
        self._enabled     = False
        self._sample_interval = 10   # 每 N 次调用存一次（降采样）
        self._retention_days = 15    # 保留天数
        self._selected_types: set[str] = set()   # 用户勾选的数据类型

        # 内部计数器（每台 PCS 独立）
        self._poll_counters: dict[int, int] = {}

        # 单体电压缓存（需要从多个 CAN 帧聚合为完整 16 路）
        self._cell_voltage_cache: dict[int, dict] = {}

        # 当前日期（用于检测跨天换文件）
        self._current_date: str = ""

        # 已打开的文件句柄缓存 {filename: (file_obj, writer)}
        self._file_handles: dict[str, tuple] = {}

    # ================================================================
    # 配置接口（由 UI 调用）
    # ================================================================

    def set_config(self, enabled: bool, sample_interval: int = 10,
                   retention_days: int = 15):
        """设置全局开关、降采样倍率、保留天数。"""
        self._enabled         = enabled
        self._sample_interval = max(1, sample_interval)
        self._retention_days = max(1, retention_days)
        logger.info("数据存储配置更新: enabled=%s, interval=%d, retention=%dd",
                    enabled, sample_interval, retention_days)

    def set_data_types(self, types: list[str]):
        """设置要存储的数据类型列表，如 ["battery", "bms_group1", "cell_voltage"]"""
        self._selected_types = set(types)
        logger.info("数据类型选择: %s", types)

    def get_status(self) -> dict:
        """返回当前状态摘要供 UI 显示。"""
        return {
            "enabled":          self._enabled,
            "sample_interval":  self._sample_interval,
            "retention_days":   self._retention_days,
            "selected_types":   sorted(self._selected_types),
            "storage_dir":      self._storage_dir,
            "total_files":      len(self._get_all_csv_files()),
            "total_size_mb":    round(self._get_storage_size() / (1024 * 1024), 2),
        }

    # ================================================================
    # 核心入口：由 MainWindow 路由 DeviceManager.data_received 调用
    # ================================================================

    def on_data_received(self, pcs_id: int, data_type: str, data: dict):
        """
        每次轮询数据到达时调用。
        内部做降采样判断和类型路由，线程安全（仅写文件）。
        """
        if not self._enabled or not self._selected_types:
            return

        # ── 降采样计数 ──
        cnt = self._poll_counters.get(pcs_id, 0) + 1
        self._poll_counters[pcs_id] = cnt
        if cnt % self._sample_interval != 0:
            return

        # ── 检测跨天，清理旧文件 ──
        today_str = datetime.now().strftime("%Y-%m-%d")
        if today_str != self._current_date:
            self._cleanup_old_files()
            self._current_date = today_str

        # ── 按 data_type 路由到对应存储逻辑 ──
        # RS485 数据类型映射
        _TYPE_ROUTING = {
            "battery parameters":           "battery",
            "group1 BMS parameters":        "bms_group1",
            "PV parameters":                "pv",
            "grid parameters":              "grid",
            "load parameters":              "load",
        }

        # 单体电压来自 CAN（data_type 是 BMS_SingleVoltage*_*）
        if data_type.startswith("BMS_SingleVoltage") or data_type == "cell_voltages":
            if "cell_voltage" in self._selected_types:
                self._accumulate_cell_voltage(pcs_id, data_type, data)

        routed = _TYPE_ROUTING.get(data_type)
        if routed and routed in self._selected_types:
            self._write_row(pcs_id, routed, data)

    # ================================================================
    # 写入逻辑
    # ================================================================

    def _write_row(self, pcs_id: int, storage_key: str, data: dict):
        """提取目标字段，追加一行 CSV。"""
        prefix, fields = self.FIELD_MAP[storage_key]
        filename = self._make_filename(pcs_id, prefix)

        row = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "pcs_id":   pcs_id}

        for f in fields:
            val = data.get(f, "")
            row[f] = f"{val:.4f}" if isinstance(val, (int, float)) else str(val)

        self._append_csv(filename, list(row.keys()), row)

    def _accumulate_cell_voltage(self, pcs_id: int, can_data_type: str, data: dict):
        """聚合 CAN 单体电压帧（4 帧拼成完整 16 路）。"""
        if pcs_id not in self._cell_voltage_cache:
            self._cell_voltage_cache[pcs_id] = {}

        cache = self._cell_voltage_cache[pcs_id]
        cache.update(data)   # 合入 cell_voltage_1 ~ cell_voltage_16

        # 当收集齐 16 路或收到最后一帧时写出
        voltages = [cache.get(f"cell_voltage_{i}") for i in range(1, 17)]
        if all(v is not None for v in voltages):
            prefix = "cell_voltage"
            filename = self._make_filename(pcs_id, prefix)

            row = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pcs_id":   pcs_id,
            }
            for i in range(1, 17):
                v = voltages[i - 1]
                row[f"v{i}"] = f"{v:.0f}" if isinstance(v, (int, float)) else ""

            self._append_csv(filename, list(row.keys()), row)
            # 清空缓存，等待下一轮
            self._cell_voltage_cache[pcs_id] = {}

    # ================================================================
    # 文件操作
    # ================================================================

    def _make_filename(self, pcs_id: int, prefix: str) -> str:
        date_str = datetime.now().strftime("%Y-%m-%d")
        name = f"PCS-{pcs_id}_{prefix}_{date_str}.csv"
        return os.path.join(self._storage_dir, name)

    def _append_csv(self, filename: str, fieldnames: list, row: dict):
        """高效追加单行 CSV（带文件句柄缓存）。"""
        os.makedirs(os.path.dirname(filename), exist_ok=True)

        need_header = not os.path.exists(filename)

        try:
            with open(filename, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if need_header:
                    writer.writeheader()
                writer.writerow(row)
        except OSError as e:
            logger.error("写入 CSV 失败: %s (%s)", filename, e)

    # ================================================================
    # 清理与维护
    # ================================================================

    def cleanup_now(self):
        """立即执行一次过期文件清理。"""
        self._cleanup_old_files()

    def _cleanup_old_files(self):
        """删除超过 retention_days 的 CSV 文件。"""
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        all_files = self._get_all_csv_files()
        removed = 0
        for fpath in all_files:
            # 从文件名提取日期: PCS-N_xxx_2026-05-20.csv
            basename = os.path.basename(fpath)
            parts = basename.rsplit("_", 1)
            if len(parts) == 2 and parts[1].endswith(".csv"):
                file_date = parts[1].replace(".csv", "")
                if file_date < cutoff_str:
                    try:
                        os.remove(fpath)
                        removed += 1
                    except OSError as e:
                        logger.warning("删除旧文件失败: %s (%s)", fpath, e)

        if removed > 0:
            logger.info("已清理 %d 个过期数据文件（保留 %d 天）",
                       removed, self._retention_days)

    def _get_all_csv_files(self) -> list[str]:
        pattern = os.path.join(self._storage_dir, "*.csv")
        return glob.glob(pattern)

    def _get_storage_size(self) -> int:
        total = 0
        for fpath in self._get_all_csv_files():
            try:
                total += os.path.getsize(fpath)
            except OSError:
                pass
        return total

    # ================================================================
    # 关闭
    # ================================================================

    def shutdown(self):
        """关闭引擎，释放资源。"""
        self._enabled = False
        self._file_handles.clear()
        logger.info("数据存储引擎已关闭")
