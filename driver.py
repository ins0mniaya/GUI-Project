# driver.py
import can
import copy
import sys
import logging
import threading
import time
import struct
from PySide6.QtCore import QObject, Signal, QCoreApplication, QTimer
from config import CAN_CONFIG, EMS_PROTOCOL_CONFIG

logger = logging.getLogger(__name__)

# ======================== CAN EMS 驱动 ========================
class CANEMSDriver(QObject):
    # 信号定义：(数据类型, 解析后的字典)
    data_received = Signal(str, dict)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.bus = None
        self.recv_thread = None
        self.running = False
        self._state_lock = threading.Lock()

        # ISO-TP 组包状态
        self._msg_buffer = bytearray()
        self._expect_len = 0

        # EMS 命令级别等待状态：允许不同命令并行（差异化轮询）
        self._cmd_state = {
            "get_EMS_Running_Data": {"waiting": False, "last_send_time": 0.0},
            "get_EMS_Realtime_Data": {"waiting": False, "last_send_time": 0.0},
        }
        self._last_rx_time = 0.0
        self._last_rx_id = None
        self._last_rx_len = 0
        self._last_dispatch_time = 0.0
        self._last_dispatch_len = 0
        timeout_cfg = float(self.config.get("timeout", 0.2) or 0.2)
        self.response_timeout = min(2.0, max(0.05, timeout_cfg))

        self._running_len = int(
            EMS_PROTOCOL_CONFIG.get("EMS_Running_Data", {}).get("data_length", 17)
        )
        self._realtime_len = int(
            EMS_PROTOCOL_CONFIG.get("EMS_Realtime_Data", {}).get("data_length", 47)
        )
        # 兼容部分设备尾帧少字节场景：允许补齐少量 0x00 后继续解析
        max_missing = int(self.config.get("max_missing_tail_bytes", 1) or 1)
        self._max_missing_tail_bytes = min(2, max(0, max_missing))

    def open_bus(self):
        """打开 EMS CAN 总线并启动接收线程"""
        try:
            bustype = self.config.get("bustype", "socketcan")
            self.bus = can.interface.Bus(
                channel=self.config["channel"],
                bustype=bustype,
                bitrate=self.config["bitrate"],
            )
            self.running = True
            self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
            self.recv_thread.start()
            logger.info(f"CAN总线链路激活: {self.config['channel']}")
            return True
        except Exception as e:
            logger.error(f"CAN总线连接失败: {e}")
            return False

    def _recv_loop(self):
        recv_timeout = float(self.config.get("timeout", 1.0) or 1.0)
        recv_timeout = min(2.0, max(0.05, recv_timeout))
        consecutive_errors = 0

        while self.running:
            if self.bus is None:
                time.sleep(0.05)
                continue

            try:
                msg = self.bus.recv(timeout=recv_timeout)
                consecutive_errors = 0
            except Exception as e:
                if not self.running:
                    break

                consecutive_errors += 1
                backoff_s = min(1.0, 0.05 * (2 ** min(consecutive_errors, 5)))
                logger.error(
                    "接收线程异常: %s (连续异常=%d, %.2fs后重试)",
                    e,
                    consecutive_errors,
                    backoff_s,
                )
                time.sleep(backoff_s)
                continue

            if not msg:
                continue

            try:
                with self._state_lock:
                    self._last_rx_time = time.time()
                    self._last_rx_id = msg.arbitration_id
                    self._last_rx_len = len(msg.data or [])
                logger.debug(
                    "EMS RX raw: id=0x%X, len=%d, data=%s",
                    msg.arbitration_id,
                    len(msg.data or []),
                    (msg.data or b"").hex(),
                )
                self._process_raw_message(msg)
            except Exception as e:
                logger.error("报文处理异常: %s", e)

    def _process_raw_message(self, msg):
        """处理原始 EMS 报文，实现 ISO-TP 组包逻辑，并最终分发完整载荷"""
        if msg.arbitration_id not in self._get_rx_ids():
            return
 
        data = msg.data
        if not data:
            return

        # 处理原始数据，按照 ISO-TP 规范进行组包
        pci = data[0]   # Protocol Control Information

        if (pci & 0xF0) == 0x00:    # 单帧
            biz_len = min((pci & 0x0F), max(0, len(data) - 1))
            biz_data = bytes(data[1 : 1 + biz_len])
            if biz_data:
                self._dispatch_data(biz_data)
            return

        if (pci & 0xF0) == 0x10:    # 首帧
            if len(data) < 2:
                self._clear_multiframe_buffer()
                return
            self._expect_len = ((pci & 0x0F) << 8) | data[1]
            if self._expect_len <= 0:
                self._clear_multiframe_buffer()
                return
            self._msg_buffer = bytearray()
            self._msg_buffer.extend(data[2:])
        elif (pci & 0xF0) == 0x20:  # 连续帧
            if self._expect_len <= 0:
                return
            self._msg_buffer.extend(data[1:])
        else:
            return

        current_len = len(self._msg_buffer)
        if self._expect_len > 0 and current_len >= self._expect_len:
            biz_data = bytes(self._msg_buffer[:self._expect_len])
            self._clear_multiframe_buffer()
            self._dispatch_data(biz_data)
            return

        # 某些设备会在最后一个连续帧少发 1 字节，导致永远凑不齐首帧声明长度。
        # 若是短尾帧且仅缺少极少字节，则补 0 继续分发，避免持续超时。
        if (pci & 0xF0) == 0x20 and len(data) < 8 and self._expect_len > 0:
            missing = self._expect_len - current_len
            if 0 < missing <= self._max_missing_tail_bytes:
                logger.warning(
                    "EMS多帧尾帧疑似缺失 %d 字节，采用 0x00 补齐后继续解析: expect=%d, got=%d, frame=%s",
                    missing,
                    self._expect_len,
                    current_len,
                    data.hex(),
                )
                biz_data = bytes(self._msg_buffer) + bytes(missing)
                self._clear_multiframe_buffer()
                self._dispatch_data(biz_data)

    def _clear_multiframe_buffer(self):
        self._msg_buffer = bytearray()
        self._expect_len = 0

    def _get_rx_ids(self):
        """返回当前 EMS 接收使用的 CAN ID 集合。"""
        rx_can_ids = EMS_PROTOCOL_CONFIG.get("rx_can_ids")
        if rx_can_ids is not None:
            valid_rx_can_ids = set(id for id in rx_can_ids if id is not None)
            if valid_rx_can_ids:
                return valid_rx_can_ids
        return
 
    COMMAND_TO_DATA_TYPE = {
        # 共享映射表：命令名 -> 配置数据类型（唯一真实源）
        "get_EMS_Running_Data": "EMS_Running_Data",
        "get_EMS_Realtime_Data": "EMS_Realtime_Data",
        # 待添加
        # "get_EMS_VarParam_Data": "EMS_VarParam_Data",
    }
    
    def _resolve_command_frame(self, command_name):
        """解析命令名称对应的业务数据帧"""
        cfg_key = self.COMMAND_TO_DATA_TYPE.get(command_name)
        if cfg_key is None:
            return None
        
        cfg = EMS_PROTOCOL_CONFIG.get(cfg_key, {})
        return cfg.get("tx_data")

    def _expected_data_type_from_command(self, command_name):
        """获取命令期望的响应数据类型"""
        return self.COMMAND_TO_DATA_TYPE.get(command_name)

    def send_command(self, command_name, command_biz_data=None):
        """发送EMS请求命令。
        
        Args:
            command_name: 命令名称，必须在 COMMAND_TO_DATA_TYPE 中定义
            command_biz_data: 业务数据，若提供则优先级高于配置中的 tx_data
        
        Returns:
            bool: 发送成功返回 True，否则返回 False
        """
        if not self.bus:
            logger.warning("EMS发送失败：CAN未连接")
            return False

        if command_name not in self._cmd_state:
            logger.error(f"未知命令: {command_name}")
            return False

        # 检查该命令的等待状态（不影响其他命令）
        now = time.time()
        with self._state_lock:
            cmd_status = self._cmd_state[command_name]
            if cmd_status["waiting"]:
                if now - cmd_status["last_send_time"] < self.response_timeout:
                    logger.debug(
                        "命令 %s 仍在等待(%.3fs<%.3fs)，丢弃本次",
                        command_name,
                        now - cmd_status["last_send_time"],
                        self.response_timeout,
                    )
                    return False
                waiting_state = {
                    cmd: bool(state.get("waiting", False))
                    for cmd, state in self._cmd_state.items()
                }
                last_rx_id = self._last_rx_id
                last_rx_len = self._last_rx_len
                last_rx_age = (now - self._last_rx_time) if self._last_rx_time > 0 else None
                last_dispatch_len = self._last_dispatch_len
                last_dispatch_age = (now - self._last_dispatch_time) if self._last_dispatch_time > 0 else None
                logger.warning(
                    "命令 %s 应答超时释放，source=send_command, waiting=%s, "
                    "last_rx_id=%s, last_rx_len=%d, last_rx_age=%s, "
                    "last_dispatch_len=%d, last_dispatch_age=%s",
                    command_name,
                    waiting_state,
                    f"0x{last_rx_id:X}" if last_rx_id is not None else "None",
                    last_rx_len,
                    f"{last_rx_age:.3f}s" if last_rx_age is not None else "None",
                    last_dispatch_len,
                    f"{last_dispatch_age:.3f}s" if last_dispatch_age is not None else "None",
                )
                cmd_status["waiting"] = False

        tx_id = EMS_PROTOCOL_CONFIG.get("tx_can_id", 0x305)

        if command_biz_data is None:
            command_biz_data = self._resolve_command_frame(command_name)

        if command_biz_data is None:
            supported_commands = list(self.COMMAND_TO_DATA_TYPE.keys())
            logger.error(
                f"EMS发送失败：命令 '{command_name}' 不支持。"
                f"支持的命令列表：{supported_commands}"
            )
            return False

        biz_bytes = bytes(command_biz_data)
        if len(biz_bytes) > 8:
            logger.error(f"EMS发送失败：命令 {command_name} 数据长度超过8字节")
            return False
        if len(biz_bytes) < 8:
            biz_bytes = biz_bytes + bytes(8 - len(biz_bytes))

        try:
            msg = can.Message(
                arbitration_id=tx_id,
                data=biz_bytes,
                is_extended_id=tx_id > 0x7FF,
            )
            self.bus.send(msg)
            # 标记此命令等待应答
            with self._state_lock:
                self._cmd_state[command_name]["waiting"] = True
                self._cmd_state[command_name]["last_send_time"] = time.time()
            logger.info(
                f"EMS命令已发送: name={command_name}, tx_id=0x{tx_id:X}, biz_data={biz_bytes.hex()}"
            )
            return True
        except Exception as e:
            logger.error(f"EMS发送命令失败: {e}")
            return False

    def clear_stale_waiting(self, command_name: str, source: str = "unknown") -> bool:
        """若该命令应答已超时则释放等待状态。"""
        if command_name not in self._cmd_state:
            return False
        with self._state_lock:
            cmd_status = self._cmd_state[command_name]
            if not cmd_status["waiting"]:
                return False
            elapsed = time.time() - cmd_status["last_send_time"]
            if elapsed < self.response_timeout:
                return False
            now = time.time()
            waiting_state = {
                cmd: bool(state.get("waiting", False))
                for cmd, state in self._cmd_state.items()
            }
            last_rx_id = self._last_rx_id
            last_rx_len = self._last_rx_len
            last_rx_age = (now - self._last_rx_time) if self._last_rx_time > 0 else None
            last_dispatch_len = self._last_dispatch_len
            last_dispatch_age = (now - self._last_dispatch_time) if self._last_dispatch_time > 0 else None
            logger.warning(
                "命令 %s 应答超时(%.3fs)清除，source=%s, waiting=%s, "
                "last_rx_id=%s, last_rx_len=%d, last_rx_age=%s, "
                "last_dispatch_len=%d, last_dispatch_age=%s",
                command_name,
                elapsed,
                source,
                waiting_state,
                f"0x{last_rx_id:X}" if last_rx_id is not None else "None",
                last_rx_len,
                f"{last_rx_age:.3f}s" if last_rx_age is not None else "None",
                last_dispatch_len,
                f"{last_dispatch_age:.3f}s" if last_dispatch_age is not None else "None",
            )
            cmd_status["waiting"] = False
            return True

    def _dispatch_data(self, data):
        """根据业务数据长度分发解析器"""
        # 收到完整数据时清除对应命令的等待状态
        with self._state_lock:
            self._last_dispatch_time = time.time()
            self._last_dispatch_len = len(data)
            pending_cmd = None
            # 根据数据类型推断是哪个命令的应答，并清除等待
            for cmd, status in self._cmd_state.items():
                if status["waiting"]:
                    pending_cmd = cmd
                    status["waiting"] = False
                    break
        length = len(data)
        # 调试：观察组包后业务数据长度和内容
        logger.debug(
            "EMS biz_data assembled: len=%d, pending_cmd=%s, hex=%s",
            length,
            pending_cmd,
            data.hex(),
        )

        expected_type = self._expected_data_type_from_command(pending_cmd)

        # 命令名优先，长度作为兜底。
        if expected_type == "EMS_Running_Data" or (expected_type is None and 8 <= length < self._realtime_len):
            parsed = self._parse_EMS_Running_Data(data)
            logger.debug(
                "EMS dispatch as EMS_Running_Data: pending_cmd=%s, parsed_system_time=%s, parsed_keys=%s",
                pending_cmd,
                parsed.get("system_time"),
                sorted(parsed.keys()),
            )
            self.data_received.emit("EMS_Running_Data", parsed)
        elif expected_type == "EMS_Realtime_Data" or (expected_type is None and length >= self._realtime_len):
            parsed = self._parse_EMS_Realtime_Data(data)
            logger.debug(
                "EMS dispatch as EMS_Realtime_Data: pending_cmd=%s, parsed_keys_sample=%s",
                pending_cmd,
                sorted(parsed.keys())[:8],
            )
            self.data_received.emit("EMS_Realtime_Data", parsed)
        
        # 待添加

        else:
            # 长度与当前解析器不匹配，先仅记录日志，后续根据协议再扩展
            logger.warning(
                "EMS payload length %d not handled, pending_cmd=%s, hex=%s",
                length,
                pending_cmd,
                data.hex(),
            )

    def _parse_EMS_Running_Data(self, data: bytes) -> dict:
        """根据 config 的 map 配置解析 EMS 运行参数。
        
        系统时间从配置读取，其他未解析的字节保存为 extra_raw。
        """
        try:
            if len(data) < 8:
                logger.warning(f"EMS_Running_Data 长度异常: {len(data)} < 8, 原始: {data.hex()}")
                return {"raw": data.hex()}

            result = {"raw": data.hex()}
            
            # 从 config 的 map 中读取字段定义
            cfg = EMS_PROTOCOL_CONFIG.get("EMS_Running_Data", {})
            field_map = cfg.get("map", {})  # 字段映射配置
            
            # 根据 map 配置解析 system_time 字段
            if "system_time" in field_map:
                year = struct.unpack(">H", data[0:2])[0]
                month = data[2]
                day = data[3]
                weekday = data[4]
                hour = data[5]
                minute = data[6]
                second = data[7]
                
                result.update({
                    "year": year,
                    "month": month,
                    "day": day,
                    "weekday": weekday,
                    "hour": hour,
                    "minute": minute,
                    "second": second,
                })
                
                # 仅在字段落在合理范围内时，生成可读的时间字符串
                system_time_str = None
                if (
                    2000 <= year <= 2200
                    and 1 <= month <= 12
                    and 1 <= day <= 31
                    and 0 <= hour < 24
                    and 0 <= minute < 60
                    and 0 <= second < 60
                ):
                    system_time_str = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
                result["system_time"] = system_time_str
            
            # 把后面未解析的字节整体保留，便于后续协议完善
            # 待添加
            if len(data) > 8:
                result["extra_raw"] = data[8:].hex()

            logger.info(
                "EMS_Running_Data parsed: system_time=%s, raw=%s",
                result.get("system_time"),
                data.hex(),
            )

            return result
        except Exception as e:
            logger.error(f"EMS_Running_Data 解析失败: {e}, 原始: {data.hex()}")
            return {"raw": data.hex()}

    def _parse_EMS_Realtime_Data(self, data: bytes) -> dict:
        """根据 config 的 map 配置解析 EMS 实时数据。
        
        47+ 字节实时数据，字段定义在 EMS_PROTOCOL_CONFIG["EMS_Realtime_Data"]["map"] 中。
        """
        try:
            d_len = len(data)
            if d_len < 2:
                logger.warning(f"EMS_Realtime_Data 长度异常: {d_len} < 2, 原始: {data.hex()}")
                return {"raw": data.hex()}

            result = {"raw": data.hex()}
            
            # 从 config 的 map 中读取字段定义
            cfg = EMS_PROTOCOL_CONFIG.get("EMS_Realtime_Data", {})
            field_map = cfg.get("map", {})
            
            # 按配置 map 解析每个字段
            for field_name, field_cfg in field_map.items():
                try:
                    byte_offset = field_cfg.get("byte", 0)
                    byte_length = field_cfg.get("length", 2)
                    resolution = field_cfg.get("resolution", 1)
                    offset_val = field_cfg.get("offset", 0)
                    endian = field_cfg.get("endian", "big")  # 默认大端序
                    
                    # 检查数据长度是否足够
                    if byte_offset + byte_length > d_len:
                        continue
                    
                    # 提取字段数据切片
                    field_data = data[byte_offset:byte_offset + byte_length]
                    
                    # 根据长度和端序解析
                    if byte_length == 1:
                        raw_val = field_data[0]
                    elif byte_length == 2:
                        fmt = ">H" if endian == "big" else "<H"
                        raw_val = struct.unpack(fmt, field_data)[0]
                    elif byte_length == 4:
                        fmt = ">I" if endian == "big" else "<I"
                        raw_val = struct.unpack(fmt, field_data)[0]
                    else:
                        # 不支持的长度，保存为原始 hex
                        result[field_name + "_raw"] = field_data.hex()
                        continue
                    
                    # 应用 resolution 和 offset
                    parsed_val = raw_val * resolution + offset_val
                    
                    # 根据值的类型决定存储格式
                    if resolution == 1 and offset_val == 0:
                        result[field_name] = raw_val
                    else:
                        # 有精度转换，保存浮点值
                        result[field_name] = round(parsed_val, 3) if isinstance(parsed_val, float) else parsed_val
                
                except Exception as e:
                    logger.warning(f"字段 {field_name} 解析失败: {e}")
                    continue
            
            # 保存未解析的字节
            if d_len > 44:
                result["extra_raw"] = data[44:].hex()

            logger.info(
                "EMS_Realtime_Data parsed: parsed_fields=%d, raw=%s",
                len([k for k in result.keys() if k not in ("raw", "extra_raw")]),
                data.hex(),
            )

            return result
        except Exception as e:
            logger.error(f"EMS_Realtime_Data 解析失败: {e}, 原始: {data.hex()}")
            return {"raw": data.hex()}

    def close(self):
        """关闭 EMS 驱动并释放资源"""
        self.running = False
        
        # 先关闭总线，让 recv() 立即失败而不是长时间阻塞
        if self.bus:
            try:
                self.bus.shutdown()
            except Exception as e:
                logger.warning(f"CAN总线关闭异常: {e}")
            self.bus = None
        
        # 等待接收线程安全退出
        if self.recv_thread and self.recv_thread.is_alive():
            self.recv_thread.join(timeout=1.5)
            if self.recv_thread.is_alive():
                logger.warning("接收线程未能及时退出，可能仍在执行")
        
        # 清理缓冲和状态
        self._clear_multiframe_buffer()
        with self._state_lock:
            for cmd_status in self._cmd_state.values():
                cmd_status["waiting"] = False

# ======================== Device Manager ========================
class DeviceManager(QObject):
    status_changed = Signal(bool, str, str)

    def __init__(self):
        super().__init__()

        # 去节点化缓存：按设备角色组织数据
        self.data_cache = {
            "ems": {
                "EMS_Realtime_Data": {},
                "EMS_Running_Data": {},
                "last_seen": 0.0,
            },
        }

        # 初始化 EMS 驱动
        self.pcs_driver = CANEMSDriver(CAN_CONFIG)
        self.pcs_driver.data_received.connect(self._on_data_arrived)

        # EMS 轮询定时器（Qt 定时器放在管理层）
        # self.realtime_poll_timer = QTimer(self)
        # self.realtime_poll_timer.setInterval(200)
        # self.realtime_poll_timer.timeout.connect(self.get_EMS_Realtime_Data)

        self.slow_poll_timer = QTimer(self)
        self.slow_poll_timer.setInterval(1000)
        self.slow_poll_timer.timeout.connect(self._poll_slow_params)
        
        # 线程安全：数据缓存和标志位的锁
        self._cache_lock = threading.Lock()
        
        # 超时检测循环控制
        self._timeout_stop_event = threading.Event()
        self._timeout_thread = None
        
        self.is_connected = False
        self._ems_stale_logged = False

    def connect_can(self, channel, bitrate_str):
        try:
            bitrate = int(bitrate_str.replace('K', '')) * 1000
            bustype = "virtual" if "vbus" in channel or "test" in channel else "socketcan"
            config = {"channel": channel, "bustype": bustype, "bitrate": bitrate}
            # 配置并启动 EMS 驱动
            self.pcs_driver.config = config

            pcs_connected = self.pcs_driver.open_bus()
            
            if pcs_connected:
                self.is_connected = True
                self._ems_stale_logged = False
                # 启动超时检测定时器
                self._start_timeout_check()
                # 启动 EMS 轮询：周期性读取运行数据和实时数据
                self._start_ems_polling()
                self.status_changed.emit(True, channel, "已连接")
            else:
                self.status_changed.emit(False, channel, "连接失败")
        except Exception as e:
            self.status_changed.emit(False, channel, f"错误: {e}")

    def _on_data_arrived(self, data_type, data):
        """线程安全：接收线程调用此方法更新数据缓存"""
        now = time.time()
        with self._cache_lock:
            if data_type in ("EMS_Realtime_Data", "EMS_Running_Data"):
                self.data_cache["ems"][data_type] = data
                self.data_cache["ems"]["last_seen"] = now
                if data_type == "EMS_Running_Data":
                    logger.info("EMS time parsed: %s", data.get("system_time"))

    def send_ems_command(self, command_name, biz_data=None):
        """透传EMS命令发送接口，供UI层按业务动作触发请求。"""
        return self.pcs_driver.send_command(command_name, biz_data)

    def get_EMS_Running_Data(self):
        return self.send_ems_command("get_EMS_Running_Data")

    # def get_EMS_Realtime_Data(self):
    #     self.pcs_driver.clear_stale_waiting("get_EMS_Realtime_Data", "realtime_poll")
    #     if self.pcs_driver._cmd_state["get_EMS_Realtime_Data"]["waiting"]:
    #         return False
    #     return self.send_ems_command("get_EMS_Realtime_Data")

    def _poll_slow_params(self):
        """慢速轮询：轮询读取运行参数。"""
        if not self.is_connected:
            return

        self.pcs_driver.clear_stale_waiting("get_EMS_Running_Data", "slow_poll")
        if not self.pcs_driver._cmd_state["get_EMS_Running_Data"]["waiting"]:
            self.get_EMS_Running_Data()

    def _start_ems_polling(self):
        """启动 EMS 轮询定时器"""
        # if not self.realtime_poll_timer.isActive():
        #     self.realtime_poll_timer.start()
        if not self.slow_poll_timer.isActive():
            self.slow_poll_timer.start()

    def _stop_ems_polling(self):
        """停止 EMS 轮询定时器"""
        # if self.realtime_poll_timer.isActive():
        #     self.realtime_poll_timer.stop()
        if self.slow_poll_timer.isActive():
            self.slow_poll_timer.stop()

    def disconnect_can(self):
        self.is_connected = False
        self._stop_timeout_check()
        self._stop_ems_polling()
        self.pcs_driver.close()
        self.data_cache["ems"]["last_seen"] = 0.0
        self.status_changed.emit(False, "", "已断开")
    
    def _start_timeout_check(self):
        """启动超时检测线程（事件驱动，无泄漏）"""
        if self._timeout_thread is None or not self._timeout_thread.is_alive():
            self._timeout_stop_event.clear()  # 清除停止事件
            self._timeout_thread = threading.Thread(
                target=self._timeout_check_loop,
                name="DeviceManager-TimeoutCheck",
                daemon=True
            )
            self._timeout_thread.start()
            logger.debug("超时检测线程已启动")
    
    def _stop_timeout_check(self):
        """停止超时检测线程（等待线程安全退出）"""
        if self._timeout_thread is not None:
            logger.debug("请求停止超时检测线程...")
            self._timeout_stop_event.set()  # 设置停止事件
            self._timeout_thread.join(timeout=2.0)  # 等待线程退出
            if self._timeout_thread.is_alive():
                logger.warning("超时检测线程未能在 2s 内退出")
            else:
                logger.debug("超时检测线程已安全退出")
            self._timeout_thread = None
    
    def _timeout_check_loop(self):
        """超时检测循环（事件驱动，避免定时器泄漏）"""
        timeout_threshold = 3.0
        check_interval = 1.0  # 每 1 秒检查一次
        
        while not self._timeout_stop_event.is_set():
            if not self.is_connected:
                # 未连接时，每隔 1 秒检查一次是否需要恢复
                self._timeout_stop_event.wait(timeout=check_interval)
                continue
            
            current_time = time.time()
            
            # 线程安全：读取缓存
            with self._cache_lock:
                ems_last = self.data_cache["ems"].get("last_seen", 0.0)
            
            ems_stale = ems_last > 0 and (current_time - ems_last > timeout_threshold)
            
            # 更新标志位（操作简单，但为了完全的安全也可加锁）
            if ems_stale and not self._ems_stale_logged:
                logger.warning("EMS 数据超时：超过 %.1fs 未收到新数据", timeout_threshold)
                self._ems_stale_logged = True
            elif not ems_stale:
                self._ems_stale_logged = False
            
            # 等待下一次检查周期（可被 _timeout_stop_event 唤醒）
            self._timeout_stop_event.wait(timeout=check_interval)
