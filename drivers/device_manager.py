import logging
import time
import queue
from PySide6.QtCore import QThread, Signal
from config import RS485_CONFIG, CAN_CONFIG, BMS_PROTOCOL_CONFIG, MULTI_PCS_CONFIG
from .rs485_ems_pcs_driver import RS485EMSPCSDriver
from .can_bms_driver import CANBMSDriver

logger = logging.getLogger(__name__)

class DeviceManager(QThread):
    """设备管理器：继承自 QThread，实现自带后台轮询的单类双线程架构。

    多台联调用法：
        dm = DeviceManager(pcs_config={"id": 1, "name": "PCS-1", "port": "COM1"})
    单台兼容用法（保留原接口）：
        dm = DeviceManager()   # 等效于使用 RS485_CONFIG 中的 port，pcs_id=0
    """
    # data_received: (pcs_id, data_type, data_dict)
    data_received = Signal(int, str, dict)
    # status_changed: (pcs_id, is_connected, description)
    status_changed = Signal(int, bool, str)

    def __init__(self, pcs_config: dict = None):
        super().__init__()

        # 支持单台兼容（pcs_config=None 时用全局配置）
        if pcs_config is None:
            pcs_config = {"id": 0, "name": "PCS", "port": RS485_CONFIG.get("port", "COM1")}

        self.pcs_id   = pcs_config.get("id",   0)
        self.pcs_name = pcs_config.get("name", "PCS")

        # 每台用独立的串口配置，port 由 pcs_config 覆盖，其余参数继承全局
        rs485_cfg = dict(RS485_CONFIG)
        rs485_cfg["port"] = pcs_config.get("port", RS485_CONFIG.get("port"))

        # 根据 EMS/PCS 协议解析信号 emit 过来的数据，并缓存到 data_cache 中
        self.data_cache = {
            "Total_PCS_Info_Drivers": {},
            "timestamps": {},
        }

        self.rs485_driver = RS485EMSPCSDriver(rs485_cfg)
        self.rs485_driver.data_received.connect(self._on_ems_data_arrived)

        # 写请求队列：由主线程入队，在轮询线程中串行执行，避免半双工总线冲突
        self._write_queue = queue.Queue(maxsize=32)

        self.is_connected = False
        self._is_running = False

    def connect_device(self):
        try:
            port = self.rs485_driver._config.get("port", self.pcs_name) if hasattr(self.rs485_driver, "_config") else self.pcs_name
            logger.info(f"[{self.pcs_name}] 开始连接 RS485: port={port}")

            self.rs485_driver.open_bus()

            self.is_connected = True
            self._is_running = True

            # 直接启动当前 QThread，会自动在后台线程执行 run() 方法
            self.start()

            self.status_changed.emit(self.pcs_id, True, "已连接")
        except Exception as e:
            logger.error(f"[{self.pcs_name}] RS485 连接异常: {str(e)}", exc_info=True)
            self.status_changed.emit(self.pcs_id, False, f"错误: {e}")

    def run(self):
        """三频轮询主循环：fast(300ms) / medium(1s) / slow(30s)，在同一后台线程中串行执行。
        
        轮询分组策略：
          - fast   (300ms)：PV参数、电网参数、负载参数、电池端口(SOC/V/I/P) —— 实时性要求高
          - medium (1s)   ：PCS设备信息、温度信息、逆变器信息、电池温度      —— 状态/诊断数据
          - slow   (30s)  ：对时、BMS各组单体电压                          —— 低频变化数据
        
        实现机制：使用绝对时间点调度（next_due），而不是“上一轮结束后再 sleep 固定时长”。
        这样可避免每轮执行耗时累积导致的相位漂移，让每个轮询组长期保持固定节拍。
        """
        poll_fast_s   = float(RS485_CONFIG.get("poll_ms", 300)) / 1000.0  # 快速轮询间隔
        poll_medium_s = 1.0    # 中频轮询间隔（秒）
        poll_slow_s   = 30.0   # 慢速轮询间隔（秒）

        # 固定每 tick 必执行的命令（时间显示需要按秒刷新，不能放入轮转队列）
        _fixed_cmd = "get_time_synchronization_parameters"

        # 快速组：每 tick 轮转执行其中一条（4条 × 250ms = 1s 完整刷一遍）
        _fast_cmds = [
            "get_PV_parameters",
            "get_grid_parameters",
            "get_load_parameters",
            "get_gen_port_parameters",
            "get_battery_parameters",
            "get_group1_BMS_parameters",
        ]
        # 中频组：每次到达中频间隔时依次执行其中一条
        _medium_cmds = [
            "get_PCS_device_information_parameters",
            "get_other_information_parameters",
            "get_inverter_information_parameters",
            
        ]
        # 慢速组：每次到达慢速间隔时依次执行其中一条
        # 注：对时校准（写时间到PCS）是独立的主动操作，不在轮询中，由用户手动触发
        _slow_cmds = [
            # "get_BMS_group_parameters",
        ]

        fast_phase   = 0   # 快速组当前命令索引
        medium_phase = 0   # 中频组当前命令索引
        slow_phase   = 0   # 慢速组当前命令索引

        t0_ts = time.time()
        next_fixed_due_ts = t0_ts
        next_fast_due_ts = t0_ts
        next_medium_due_ts = t0_ts + poll_medium_s
        next_slow_due_ts = t0_ts + poll_slow_s

        logger.info("RS485 后台轮询线程已启动 (fast=%.1fs / medium=%.1fs / slow=%.1fs)",
                    poll_fast_s, poll_medium_s, poll_slow_s)

        while self._is_running and self.is_connected:
            now_ts = time.time()

            # 按“最早到期时间”唤醒，避免 medium/slow 被 fast tick 量化后错位。
            next_due_ts = min(next_fixed_due_ts, next_fast_due_ts, next_medium_due_ts)
            if now_ts < next_due_ts:
                time.sleep(max(0.0, next_due_ts - now_ts))
                continue

            now_ts = time.time()

            # ── 固定任务：每个 tick 必执行，保证时间显示按 300ms 刷新 ───
            if now_ts >= next_fixed_due_ts:
                logger.debug("--- [fixed       ] %s ---", _fixed_cmd)
                self.rs485_driver.send_command(_fixed_cmd)
                while next_fixed_due_ts <= now_ts:
                    next_fixed_due_ts += poll_fast_s

            # ── 快速轮询：每个 tick 轮转执行一条 fast 命令 ──────────────
            if now_ts >= next_fast_due_ts:
                cmd = _fast_cmds[fast_phase]
                logger.debug("--- [fast  phase %d/%d] %s ---", fast_phase, len(_fast_cmds) - 1, cmd)
                self.rs485_driver.send_command(cmd)
                fast_phase = (fast_phase + 1) % len(_fast_cmds)
                while next_fast_due_ts <= now_ts:
                    next_fast_due_ts += poll_fast_s

            # ── 写请求：每个 tick 最多执行一条，避免长时间挤占实时轮询 ───
            try:
                write_item = self._write_queue.get_nowait()
                if isinstance(write_item, dict):
                    write_cmd = write_item.get("cmd")
                    write_payload = write_item.get("payload")
                elif isinstance(write_item, tuple) and len(write_item) >= 2:
                    write_cmd, write_payload = write_item[0], write_item[1]
                else:
                    write_cmd, write_payload = write_item, None

                logger.debug("--- [write       ] %s ---", write_cmd)
                ok = self.rs485_driver.send_command(write_cmd, write_payload)
                # 写后给设备一个短暂恢复窗口，避免紧邻读轮询触发异常帧/短帧
                time.sleep(0.12)
                if not ok:
                    logger.warning("写请求执行失败: %s", write_cmd)
                # 本 tick 已执行写操作，跳过 medium/slow，优先让下一轮 fast 先恢复
                continue
            except queue.Empty:
                pass

            now_ts = time.time()

            # ── 中频轮询：基于绝对时间触发，执行后按固定周期推进 ──────────
            if now_ts >= next_medium_due_ts:
                cmd = _medium_cmds[medium_phase]
                logger.debug("--- [medium phase %d/%d] %s ---", medium_phase, len(_medium_cmds) - 1, cmd)
                self.rs485_driver.send_command(cmd)
                medium_phase = (medium_phase + 1) % len(_medium_cmds)
                while next_medium_due_ts <= now_ts:
                    next_medium_due_ts += poll_medium_s

            # ── 慢速轮询：基于绝对时间触发，执行后按固定周期推进 ────────────
            # if now_ts >= next_slow_due_ts:
            #     cmd = _slow_cmds[slow_phase]
            #     logger.debug("--- [slow   phase %d/%d] %s ---", slow_phase, len(_slow_cmds) - 1, cmd)
            #     self.rs485_driver.send_command(cmd)
            #     slow_phase = (slow_phase + 1) % len(_slow_cmds)
            #     while next_slow_due_ts <= now_ts:
            #         next_slow_due_ts += poll_slow_s

    def _on_ems_data_arrived(self, data_type, data):
        now = time.time()
        self.data_cache["Total_PCS_Info_Drivers"][data_type] = data
        self.data_cache["timestamps"][data_type] = now
        self.data_cache["Total_PCS_Info_Drivers"]["last_seen"] = now
        # 向外发射带 pcs_id 的信号，供 MainWindow 路由到正确的 Tab
        self.data_received.emit(self.pcs_id, data_type, data)

        # 检查是否有 partial_update 暂存待写入
        pending = getattr(self, '_partial_write_pending', {})
        if data_type in pending:
            changes = pending.pop(data_type)
            merged = {**data, **changes}
            logger.info("[partial_update] 数据已回填，自动执行合并写入: %s", data_type)
            self.enqueue_write_parameters(data_type, merged)

    def disconnect_device(self):
        self._is_running = False
        
        # 等待后台线程安全退出（避免串口强制断开导致的崩溃）
        if self.isRunning():
            self.wait()

        self.is_connected = False
        self.rs485_driver.close_bus()
        self.status_changed.emit(self.pcs_id, False, "已断开")

    def __del__(self):
        try:
            self._is_running = False
            if self.isRunning():
                self.wait()
        except RuntimeError:
            # 当程序关闭或主对象被销毁时，底层的 C++ QThread 对象可能已经被系统释放
            # 此时调用 isRunning() 就会抛出 RuntimeError，我们直接忽略即可
            pass

    def sync_time(self):
        """
        触发时间同步（对时）：向 PCS 写入当前系统时间
        注意：此操作在主线程仅入队，实际发送在线程安全队列中执行
        """
        if not self.is_connected:
            logger.warning("对时失败：设备未连接")
            return False

        logger.info("触发 PCS 时间同步（对时）...")
        try:
            self._write_queue.put_nowait({"cmd": "set_time_synchronization_parameters", "payload": None})
            logger.info("PCS 对时请求已入队，等待轮询线程发送")
            return True
        except queue.Full:
            logger.warning("对时失败：写请求队列已满")
            return False
        except Exception as e:
            logger.error(f"对时发送异常: {e}")
            return False

    def enqueue_read_command(self, cmd_name: str) -> bool:
        """将一次性读命令借用写队列通道入队，供 UI 主动触发读取设置组参数使用。
        结果通过 rs485_driver.data_received 信号异步返回。
        """
        if not self.is_connected:
            logger.warning("读命令入队失败：设备未连接, cmd=%s", cmd_name)
            return False
        try:
            self._write_queue.put_nowait({"cmd": cmd_name, "payload": None})
            logger.info("读命令已入队: %s", cmd_name)
            return True
        except queue.Full:
            logger.warning("读命令入队失败：队列已满, cmd=%s", cmd_name)
            return False
        except Exception as exc:
            logger.error("读命令入队异常: %s", exc)
            return False

    def enqueue_write_parameters(self, group_name: str, payload: dict):
        """通用写寄存器入队接口，供 UI 以后直接提交配置组参数。"""
        cmd_map = {
            "basic setting parameters": "set_basic_setting_parameters",
            "system work mode1 parameters": "set_system work mode1 parameters",
            "system work mode2 parameters": "set_system work mode2 parameters",
            "grid setting parameters": "set_grid_setting_parameters",
            "battery setting parameters": "set_battery_setting_parameters",
            "protection setting parameters": "set_protection_setting_parameters",
            "time synchronization parameters": "set_time_synchronization_parameters",
        }

        cmd_name = cmd_map.get(group_name)
        if not cmd_name:
            logger.warning("不支持的写寄存器组: %s", group_name)
            return False

        try:
            self._write_queue.put_nowait({"cmd": cmd_name, "payload": payload})
            logger.info("写寄存器请求已入队: %s", group_name)
            return True
        except queue.Full:
            logger.warning("写寄存器入队失败：队列已满, group=%s", group_name)
            return False
        except Exception as exc:
            logger.error("写寄存器入队异常: %s", exc)
            return False

    def partial_update(self, group_name: str, changes: dict) -> bool:
        """部分更新：只传要改的字段，自动从缓存取当前值合并后下发。

        如果缓存中没有该组当前值，会先入队一次读命令，等数据回填后
        再执行写入（通过 _partial_write_pending 队列暂存待写入的变更）。

        用法（模型/算法调用）：
            dm.partial_update("system work mode1 parameters", {"max_solar_power": 5000})
        """
        # 读命令名映射（group_name → get_xxx）
        read_cmd_map = {
            "basic setting parameters": "get_basic_setting_parameters",
            "system work mode1 parameters": "get_system_work_mode1_parameters",
            "system work mode2 parameters": "get_system_work_mode2_parameters",
            "grid setting parameters": "get_grid_setting_parameters",
            "battery setting parameters": "get_battery_setting_parameters",
            "protection setting parameters": "get_protection_setting_parameters",
        }

        # 读命令映射（data_type → group_name，用于回填检测）
        _data_type_to_group = {
            "basic setting parameters": "basic setting parameters",
            "system work mode1 parameters": "system work mode1 parameters",
            "system work mode2 parameters": "system work mode2 parameters",
            "grid setting parameters": "grid setting parameters",
            "battery setting parameters": "battery setting parameters",
            "protection setting parameters": "protection setting parameters",
        }

        # 检查缓存是否已有该组数据
        cached = self.data_cache["Total_PCS_Info_Drivers"].get(group_name)
        if cached:
            merged = {**cached, **changes}
            logger.info("[partial_update] 缓存命中，直接合并写入: %s", group_name)
            return self.enqueue_write_parameters(group_name, merged)

        # 缓存没有 → 先读再写，把待写变更暂存
        logger.info("[partial_update] 缓存未命中，先入队读取再写入: %s", group_name)
        if not hasattr(self, '_partial_write_pending'):
            self._partial_write_pending = {}
        self._partial_write_pending[group_name] = changes

        # 触发一次读命令
        read_cmd = read_cmd_map.get(group_name)
        if read_cmd:
            return self.enqueue_read_command(read_cmd)
        else:
            logger.warning("[partial_update] 无法找到读命令: %s", group_name)
            return False

class CANBMSWorker(QThread):
    """独立 CAN 接收线程：持续 recv() 并将原始帧交给 CANBMSDriver 解析。"""

    # 直接透传驱动层信号：(消息名称, 解析后的数据字典)
    data_received = Signal(str, dict)
    # CAN 连接状态变化信号：(is_connected, 描述)
    status_changed = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._driver = CANBMSDriver()
        self._driver.data_received.connect(self.data_received)
        self._bus = None
        self._running = False
        self.is_connected = False

    def connect(self):
        """打开 CAN 总线并启动接收线程。"""
        try:
            import can
            channel = CAN_CONFIG.get("channel", "can0")
            bustype = CAN_CONFIG.get("bustype", "socketcan")
            bitrate = CAN_CONFIG.get("bitrate", 500000)
            logger.info("正在连接 CAN 总线: channel=%s, bustype=%s, bitrate=%d", channel, bustype, bitrate)
            self._bus = can.interface.Bus(channel=channel, bustype=bustype, bitrate=bitrate)
            self._driver.open_bus(self._bus)
            self._running = True
            self.is_connected = True
            self.start()
            self.status_changed.emit(True, f"CAN 已连接 ({channel})")
            logger.info("CAN 总线连接成功，接收线程已启动")
            return True
        except Exception as e:
            logger.error("CAN 连接失败: %s", e, exc_info=True)
            self.status_changed.emit(False, f"CAN 连接失败: {e}")
            return False

    def disconnect(self):
        """停止接收线程并关闭 CAN 总线。"""
        self._running = False
        self._driver.close_bus()
        if self.isRunning():
            self.wait(2000)
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None
        self.is_connected = False
        self.status_changed.emit(False, "CAN 已断开")
        logger.info("CAN 总线已断开")

    def run(self):
        """CAN 接收主循环：阻塞 recv()，超时后检查 _running 标志。"""
        rx_ids = set(BMS_PROTOCOL_CONFIG.get("rx_can_ids", []))
        logger.info("CAN BMS 接收线程启动，监听 %d 个 CAN ID", len(rx_ids))
        while self._running:
            try:
                msg = self._bus.recv(timeout=0.2)
                if msg is None:
                    continue
                if rx_ids and msg.arbitration_id not in rx_ids:
                    continue
                self._driver.process_msg(msg)
            except Exception as e:
                if self._running:
                    logger.warning("CAN recv 异常: %s", e)
        logger.info("CAN BMS 接收线程已退出")

