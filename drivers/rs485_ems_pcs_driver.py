import datetime
import logging
import time

from PySide6.QtCore import QObject, Signal
from config import RS485_CONFIG, PCS_REGISTER_CONFIG

import serial



logger = logging.getLogger(__name__)

# 配置文件通过config传参，用device_manager调用驱动的接口。
class RS485EMSPCSDriver(QObject):
    """RS485 自定义协议驱动: 5A A5 + addr1(2) + addr2(2) + ctrl(1) + len(2) + data(寄存器地址+寄存器数量) + CRC16(2) + 16(1).
       格式示例：5A A5 00 00 00 00 03 00 04 58 01 00 06 CRC16_H CRC16_L 16
       注意：
       1、发送时CRC16使用大端（高字节在前），接收时CRC16使用小端（低字节在前）。
       2、CRC16算法为常见的Modbus CRC-16，初始值0xFFFF，生成多项式0xA001，对输入数据按字节处理，不进行位反转。
    """
    data_received = Signal(str, dict)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.client = None
        self.running = False

        protocol = dict(self.config.get("protocol", {}) or {})
        self.frame_head = bytes(protocol.get("head_frame", [0x5A, 0xA5]))
        self.frame_end = int(protocol.get("end_frame", 0x16)) & 0xFF
        self.read_ctrl = int(protocol.get("read_ctrl_frame", 0x03)) & 0xFF
        self.write_ctrl = int(protocol.get("write_ctrl_frame", 0x10)) & 0xFF
        self.max_frame_len = int(protocol.get("max_frame_len", 1024))
        self.inter_frame_delay = float(protocol.get("inter_frame_delay", 0.01))
        self.strict_addr_check = bool(protocol.get("strict_addr_check", False))

        # Protocol address fields (0~65535)
        self.addr1 = int(protocol.get("addr1", 0x0000)) & 0xFFFF
        self.addr2 = int(protocol.get("addr2", 0x0000)) & 0xFFFF

    def open_bus(self, _bus=None):
        if serial is None:
            raise RuntimeError("Missing pyserial, please install: pip install pyserial")

        self.client = serial.Serial(
            port=self.config.get("port", "COM3"),
            baudrate=int(self.config.get("baudrate", 9600)),
            bytesize=int(self.config.get("bytesize", 8)),
            parity=str(self.config.get("parity", "N")),
            stopbits=int(self.config.get("stopbits", 1)),
            timeout=float(self.config.get("timeout", 1.0)),
            write_timeout=float(self.config.get("timeout", 1.0)),
        )

        if not self.client or not self.client.is_open:
            raise ConnectionError(f"RS485 serial open failed: {self.config.get('port', 'COM3')}")

        self.running = True
        logger.info("RS485 connected")
        return True

    def close_bus(self):
        self.running = False
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None

    def send_command(self, cmd_name, payload=None):
        if not self.running or self.client is None:
            logger.error("RS485 driver not running or client is None")
            return False

        try:
            logger.debug("RS485 execute command: %s", cmd_name)

            # ── 快速轮询组（fast） ──────────────────────────────────────
            # PV 直流信息参数
            if cmd_name == "get_PV_parameters":
                self._get_PV_parameters()
                return True

            # 电网信息参数
            if cmd_name == "get_grid_parameters":
                self._get_grid_parameters()
                return True

            # 负载/逆变输出信息参数
            if cmd_name == "get_load_parameters":
                self._get_load_parameters()
                return True

            # 电池端口信息参数（SOC / V / I / P）
            if cmd_name == "get_gen_port_parameters":
                self._get_gen_port_parameters()
                return True

            # ── 中频轮询组（medium） ────────────────────────────────────
            # PCS基本信息参数
            if cmd_name == "get_PCS_device_information_parameters":
                self._get_PCS_device_information_parameters()
                return True

            # 其他信息参数（温度）
            if cmd_name == "get_other_information_parameters":
                self._get_other_information_parameters()
                return True

            # 逆变器温度信息参数
            if cmd_name == "get_inverter_information_parameters":
                self._get_inverter_information_parameters()
                return True

            # 电池组温度信息参数
            if cmd_name == "get_battery_parameters":
                self._get_battery_parameters()
                return True

            # ── 慢速轮询组（slow） ──────────────────────────────────────
            # 对时参数
            if cmd_name == "get_time_synchronization_parameters":
                self._get_time_synchronization_parameters()
                return True

            # BMS group1 单体电压
            if cmd_name == "get_group1_BMS_parameters":
                self._get_group1_BMS_parameters()
                return True
            
            # ── 设置组读取（由 UI 主动触发）──────────────────
            if cmd_name == "get_basic_setting_parameters":
                self._get_basic_setting_parameters()
                return True

            if cmd_name == "get_grid_setting_parameters":
                self._get_grid_setting_parameters()
                return True

            if cmd_name == "get_battery_setting_parameters":
                self._get_battery_setting_parameters()
                return True

            if cmd_name == "get_protection_setting_parameters":
                self._get_protection_setting_parameters()
                return True

            if cmd_name == "get_system_work_mode1_parameters":
                self._get_system_work_mode1_parameters()
                return True

            if cmd_name == "get_system_work_mode2_parameters":
                self._get_system_work_mode2_parameters()
                return True

            if cmd_name == "get_advanced_setting_parameters":
                self._get_advanced_setting_parameters()
                return True

            if cmd_name == "get_grid_setting_parameters":
                self._get_grid_setting_parameters()
                return True
            
            # ── 写入组 ──────────────────────────────────────
            # 写入时间同步参数（对时）
            if cmd_name == "set_time_synchronization_parameters":
                self._set_time_synchronization_parameters()
                return True

            # 系统基础设置参数写入
            if cmd_name == "set_basic_setting_parameters":
                return self._set_config_group_parameters("basic setting parameters", payload)

            # 电网设置参数写入
            if cmd_name == "set_grid_setting_parameters":
                return self._set_config_group_parameters("grid setting parameters", payload)

            # 电池设置参数写入
            if cmd_name == "set_battery_setting_parameters":
                return self._set_config_group_parameters("battery setting parameters", payload)

            # 保护参数设置写入
            if cmd_name == "set_protection_setting_parameters":
                return self._set_config_group_parameters("protection setting parameters", payload)

            # 通用配置组写入：兼容未来新增的 set_xxx 命令
            if cmd_name.startswith("set_") and cmd_name.endswith("_parameters"):
                group_name = cmd_name[4:-11].replace("_", " ") + " parameters"
                return self._set_config_group_parameters(group_name, payload)
            
            logger.warning("Unknown RS485 command: %s", cmd_name)
            return False
        except Exception as exc:
            logger.error("RS485 command failed [%s]: %s", cmd_name, exc, exc_info=True)
            return False

    @staticmethod
    def _calculate_crc16_checksum(data: bytes) -> int:
        """
        计算 Modbus CRC-16 校验码。
        初始值: 0xFFFF, 多项式: 0xA001。
        """
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF

    def _pack_request_frame_bytes(self, addr1: int, addr2: int, ctrl: int, data: bytes) -> bytes:
        """将地址、控制位和数据封装成一个完整的待发送字节流"""
        data_len = len(data) & 0xFFFF
        full_payload_no_crc = self.frame_head + bytes(
            [
                (addr1 >> 8) & 0xFF,
                addr1 & 0xFF,
                (addr2 >> 8) & 0xFF,
                addr2 & 0xFF,
                ctrl & 0xFF,
                (data_len >> 8) & 0xFF,
                data_len & 0xFF,
            ]
        ) + data
        crc = self._calculate_crc16_checksum(full_payload_no_crc)
        # 发送使用大端 CRC：高字节在前
        crc_high = (crc >> 8) & 0xFF
        crc_low = crc & 0xFF
        return full_payload_no_crc + bytes([crc_high, crc_low, self.frame_end])

    def _unpack_response_frame_bytes(self, frame: bytes) -> tuple[int, int, int, bytes]:
        """将接收到的原始字节流拆解回地址、控制位和有效负载数据"""
        if len(frame) < 12:
            raise RuntimeError(f"Invalid response length: {len(frame)}")
        if frame[:2] != self.frame_head:
            raise RuntimeError(f"Invalid frame head: {frame[:2].hex(' ')}")
        if frame[-1] != self.frame_end:
            raise RuntimeError(f"Invalid frame tail: 0x{frame[-1]:02X}")

        addr1 = (frame[2] << 8) | frame[3]
        addr2 = (frame[4] << 8) | frame[5]
        ctrl = frame[6]
        data_len = (frame[7] << 8) | frame[8]
        data_start = 9
        data_end = data_start + data_len
        if data_end + 3 != len(frame):
            raise RuntimeError("Response len field mismatch")

        data = frame[data_start:data_end]
        # 接收使用小端 CRC：低字节在前
        recv_crc_low = frame[data_end]
        recv_crc_high = frame[data_end + 1]
        recv_crc = recv_crc_low | (recv_crc_high << 8)
        
        calc_crc = self._calculate_crc16_checksum(frame[:data_end])
        if recv_crc != calc_crc:
            raise RuntimeError(f"CRC mismatch: recv=0x{recv_crc:04X}, calc=0x{calc_crc:04X}")
        return addr1, addr2, ctrl, data

    def _read_raw_bytes_from_serial(self) -> bytes:
        """从串口持续读取数据，直到识别出一个完整的自定义协议帧"""
        timeout = float(self.config.get("timeout", 1.0))
        deadline = time.monotonic() + timeout
        buf = bytearray()

        while time.monotonic() < deadline:
            chunk = self.client.read(1)
            if not chunk:
                continue
            buf += chunk

            if len(buf) == 1 and buf[0] != self.frame_head[0]:
                buf.clear()
                continue
            if len(buf) == 2 and bytes(buf[:2]) != self.frame_head:
                buf = bytearray([buf[-1]])
                continue

            if len(buf) >= 9:
                data_len = (buf[7] << 8) | buf[8]
                expected_len = 2 + 2 + 2 + 1 + 2 + data_len + 2 + 1
                if expected_len > self.max_frame_len:
                    raise RuntimeError(f"Frame too long: {expected_len}")
                if len(buf) >= expected_len:
                    return bytes(buf[:expected_len])

        raise TimeoutError("Read response frame timeout")

    def _send_request_and_wait_for_response(self, ctrl: int, data: bytes) -> tuple[int, int, int, bytes]:
        """【核心交互】发送请求字节流并等待并解析返回的响应"""
        frame = self._pack_request_frame_bytes(addr1=self.addr1, addr2=self.addr2, ctrl=ctrl, data=data)
        logger.info("RS485 发送帧: %s", frame.hex(' '))
        self.client.reset_input_buffer()
        self.client.write(frame)
        self.client.flush()
        if self.inter_frame_delay > 0:
            time.sleep(self.inter_frame_delay)

        resp = self._read_raw_bytes_from_serial()
        logger.info("RS485 接收帧: %s", resp.hex(' '))
        r_addr1, r_addr2, r_ctrl, r_data = self._unpack_response_frame_bytes(resp)
        if self.strict_addr_check and (r_addr1 != self.addr1 or r_addr2 != self.addr2):
            raise RuntimeError(
                f"Addr mismatch: req=({self.addr1},{self.addr2}), resp=({r_addr1},{r_addr2})"
            )

        return r_addr1, r_addr2, r_ctrl, r_data

    @staticmethod
    def _extract_register_values_from_payload(data: bytes, start_addr: int, count: int) -> list[int]:
        """从响应报文的有效负载(Payload)中提取出真正的寄存器数值列表"""
        expected_bytes = count * 2
        # 自动匹配：[寄存器数量(2)+数据] 或 [纯数据] 或 [数据长度(1)+数据] 或 [起始地址(2)+数量(2)+数据]
        if len(data) == expected_bytes + 2: raw = data[2:]
        elif len(data) == expected_bytes: raw = data
        elif len(data) == expected_bytes + 1: raw = data[1:]
        elif len(data) >= expected_bytes + 4: raw = data[4:4 + expected_bytes]
        else: raise RuntimeError(f"数据长度错误: 预期 {expected_bytes}, 实际 {len(data)}")

        return [(raw[i] << 8) | raw[i + 1] for i in range(0, len(raw), 2)]

    def _read_holding_registers(self, address, count):
        """基础读取功能：封装请求 -> 发送并获取返回 -> 提取数值"""
        req_data = bytes([(address >> 8) & 0xFF, address & 0xFF, (count >> 8) & 0xFF, count & 0xFF])
        _, _, _, resp_data = self._send_request_and_wait_for_response(ctrl=self.read_ctrl, data=req_data)
        return self._extract_register_values_from_payload(resp_data, start_addr=address, count=count)

    def _write_holding_registers(self, address: int, values: list[int]) -> bool:
        """
        基础写入功能（功能码 0x10）：写入多个保持寄存器
        :param address: 起始寄存器地址
        :param values: 要写入的寄存器值列表
        :return: 写入成功返回 True
        """
        count = len(values)
        # 构建请求数据：起始地址(2) + 寄存器数量(2) + 数据(N)
        req_data = bytes([
            (address >> 8) & 0xFF, address & 0xFF,  # 起始地址
            (count >> 8) & 0xFF, count & 0xFF,       # 寄存器数量
        ])
        # 添加寄存器数据（每个寄存器2字节，大端序）
        for val in values:
            req_data += bytes([(val >> 8) & 0xFF, val & 0xFF])

        # 发送请求并等待响应
        resp_addr1, resp_addr2, resp_ctrl, resp_data = self._send_request_and_wait_for_response(
            ctrl=self.write_ctrl, data=req_data
        )

        # 写入响应控制码：
        # 0x90 = 正常应答，数据区2字节为寄存器个数
        # 0xD0 = 否认应答，数据区2字节为错误标识
        ack_ctrl = (self.write_ctrl | 0x80) & 0xFF
        nack_ctrl = (self.write_ctrl | 0xC0) & 0xFF

        if resp_ctrl == nack_ctrl:
            if len(resp_data) >= 2:
                err_code = (resp_data[0] << 8) | resp_data[1]
                logger.warning("寄存器写入被设备否认: 错误标识=0x%04X", err_code)
            else:
                logger.warning("寄存器写入被设备否认: 响应数据长度异常(len=%d)", len(resp_data))
            return False

        expected_ctrl_set = {self.write_ctrl, ack_ctrl}
        if resp_ctrl not in expected_ctrl_set:
            logger.warning("寄存器写入响应控制码异常: resp_ctrl=0x%02X, expected=%s", resp_ctrl, [f"0x{x:02X}" for x in expected_ctrl_set])
            return False

        # 验证响应数据：
        # 1) 标准回显格式: [起始地址(2) + 寄存器数量(2)]
        # 2) 厂商简化格式: [寄存器数量(2)]
        if len(resp_data) == 4:
            resp_addr = (resp_data[0] << 8) | resp_data[1]
            resp_count = (resp_data[2] << 8) | resp_data[3]
            if resp_addr == address and resp_count == count:
                logger.info("寄存器写入成功: 地址=%d, 数量=%d", address, count)
                return True
        elif len(resp_data) == 2:
            resp_count = (resp_data[0] << 8) | resp_data[1]
            if resp_count == count:
                logger.info("寄存器写入成功(简化ACK): 地址=%d, 数量=%d", address, count)
                return True

        logger.warning("寄存器写入响应验证失败")
        return False

    @staticmethod
    def _to_register_words(value, size: int, gain: float, write_offset: int = 0) -> list[int]:
        """把配置值转换成 16 位寄存器字序列。"""
        if size < 1:
            raise ValueError(f"Invalid register size: {size}")

        if size == 1:
            if gain == 1:
                raw_value = int(round(value))
            else:
                raw_value = int(round(float(value) / gain))
            raw_value += write_offset
            return [raw_value & 0xFFFF]

        if gain == 1:
            raw_value = int(round(value))
        else:
            raw_value = int(round(float(value) / gain))

        raw_value += write_offset

        bit_width = size * 16
        raw_value &= (1 << bit_width) - 1
        return [
            (raw_value >> shift) & 0xFFFF
            for shift in range((size - 1) * 16, -1, -16)
        ]

    def _set_config_group_parameters(self, group_name: str, values: dict | None) -> bool:
        """按 PCS_REGISTER_CONFIG 的写组配置，写入一组保持寄存器。"""
        cfg = PCS_REGISTER_CONFIG.get(group_name)
        if not cfg or "registers" not in cfg:
            logger.error("未知写配置组: %s", group_name)
            return False

        if not isinstance(values, dict):
            logger.error("写配置组参数格式错误: group=%s, payload=%r", group_name, values)
            return False

        ordered_words: list[int] = []
        missing_keys: list[str] = []
        
        # 预处理：按off排序，计算每个字段的size（根据下一个字段的off自动判断）
        sorted_items = sorted(cfg["registers"].items(), key=lambda x: x[1].get("off", 0))
        size_map = {}
        for i, (key, info) in enumerate(sorted_items):
            off = info.get("off", 0)
            if i < len(sorted_items) - 1:
                next_off = sorted_items[i + 1][1].get("off", 0)
                size_map[key] = max(1, next_off - off)
            else:
                size_map[key] = 1

        for key, info in cfg["registers"].items():
            if key not in values:
                missing_keys.append(key)
                continue

            size = size_map.get(key, 1)
            gain = float(info.get("gain", 1))
            write_offset = int(info.get("write_offset", 0))
            ordered_words.extend(self._to_register_words(values[key], size=size, gain=gain, write_offset=write_offset))

        if missing_keys:
            logger.error("写配置组缺少字段: group=%s, missing=%s", group_name, missing_keys)
            return False

        logger.info("开始写入配置组 [%s] 的寄存器", group_name)
        success = self._write_holding_registers(cfg["start_addr"], ordered_words)
        if success:
            logger.info("成功写入配置组 [%s] 的寄存器\n", group_name)
        else:
            logger.warning("写入配置组 [%s] 的寄存器失败", group_name)

        result_type = f"{group_name} write result"
        self.data_received.emit(result_type, {
            "success": success,
            "group_name": group_name,
            "start_addr": cfg["start_addr"],
            "count": len(ordered_words),
            "values": values,
        })
        return success

    def _parse_config_group(self, group_name, signal_name=None):
        """核心引擎：根据 config 自动计算长度、读取并转换数据"""
        cfg = PCS_REGISTER_CONFIG.get(group_name)
        if not cfg or "registers" not in cfg: return

        # 1. 自动计算该组需要读取的总寄存器数量
        max_idx = 0
        for item in cfg["registers"].values():
            off = item.get("off", 0)
            if off + 1 > max_idx:
                max_idx = off + 1
        
        try:
            logger.info("开始读取配置组 [%s] 的寄存器", group_name)
            regs = self._read_holding_registers(cfg["start_addr"], max_idx)
            logger.info("成功读取配置组 [%s] 的寄存器\n", group_name)
            
            # 预处理：按off排序，计算每个字段的size（根据下一个字段的off自动判断）
            sorted_items = sorted(cfg["registers"].items(), key=lambda x: x[1].get("off", 0))
            size_map = {}
            for i, (key, info) in enumerate(sorted_items):
                off = info.get("off", 0)
                if i < len(sorted_items) - 1:
                    next_off = sorted_items[i + 1][1].get("off", 0)
                    size_map[key] = max(1, next_off - off)
                else:
                    size_map[key] = 1
            
            payload = {}
            # 2. 自动遍历配置进行转换（支持变比 gain、多寄存器合并 size）
            for key, info in cfg["registers"].items():
                off = info.get("off", 0)
                size = size_map.get(key, 1)
                gain = info.get("gain", 1)
                
                # 处理多寄存器合并 (例如 size=2, (high << 16) | low)
                if size == 1:
                    raw_val = regs[off]
                else:
                    # 按照大端序合并：高位寄存器在前，低位在后
                    raw_val = 0
                    for i in range(size):
                        raw_val = (raw_val << 16) | regs[off + i]
                
                # 应用变比
                res = round(raw_val * gain, 3) if gain != 1 else raw_val
                
                # 防止超大整数导致 Qt Signal(QVariant) 报 OverflowError
                if isinstance(res, int) and res.bit_length() > 63:
                    res = f"{res:0{size*4}X}"  # 转为16进制字符串避免 C++ 溢出

                payload[key] = res
                if "alias" in info:
                    payload[info["alias"]] = res

            return payload
        except Exception as exc:
            logger.error(f"解析配置组 [{group_name}] 失败: {exc}")

    ##############################################################    
    # 寄存器函数：每个函数对应一个功能，内部调用 _parse_config_group 来自动处理寄存器读取和数据转换
    ##############################################################
    """读寄存器函数"""
    def _get_PCS_device_information_parameters(self):
        """获取 PCS 基本信息及运行状态，并针对特殊状态字段进行解析"""
        try:
            logger.debug("--- 开始执行: _get_PCS_device_information_parameters ---")
            data = self._parse_config_group("PCS device information parameters")
            if data:
                # 运行状态解析 (0=等待 1=自检 2=正常 4=故障)
                state_map = {0: "等待", 1: "自检", 2: "正常", 4: "故障"}
                run_state_code = data.get("run_state", 0)
                data["run_state_str"] = state_map.get(run_state_code, f"未知({run_state_code})")
                
                # 通讯协议版本处理 (例如 0x0102 -> 1.2)
                # ver_raw = data.get("version", 0)
                # data["version_str"] = f"{(ver_raw >> 8) & 0xFF}.{ver_raw & 0xFF}"
                
                # 故障码处理：将4个16位故障码合并为一个64位整数，并转为二进制字符串
                fc1 = data.get("fault_code_1", 0)
                fc2 = data.get("fault_code_2", 0)
                fc3 = data.get("fault_code_3", 0)
                fc4 = data.get("fault_code_4", 0)
                merged_fault_code = (fc1 << 48) | (fc2 << 32) | (fc3 << 16) | fc4
                data["merged_fault_code_binary"] = bin(merged_fault_code)[2:].zfill(64)
                
                # 重新发送包含 run_state_str 和 version_str 的完整对象
                self.data_received.emit("PCS device information parameters", data)
                logger.debug("--- 完成执行: _get_PCS_device_information_parameters, 发送数据: %s", data)
            else:
                logger.warning("--- 执行失败: _get_PCS_device_information_parameters 未获取到有效数据 (可能通讯失败) ---")
        except Exception as e:
            logger.error("--- 驱动执行错误: _get_PCS_device_information_parameters 崩溃: %s", e, exc_info=True)

    def _get_other_information_parameters(self):
        """获取其他信息参数（关于温度）"""
        data = self._parse_config_group("other information parameters")
        if data:
            self.data_received.emit("other information parameters", data)

    def _get_time_synchronization_parameters(self):
        """读取 PCS 时间"""
        data = self._parse_config_group("time synchronization parameters")
        if data:
            # Table: sec, min, hour, day, month, year
            s, m, h = data.get("sec", 0), data.get("min", 0), data.get("hour", 0)
            day, month, year = data.get("day", 0), data.get("month", 0), data.get("year", 0)

            if year < 100: year += 2000

            system_time_str = f"{year}-{month:02d}-{day:02d} {h:02d}:{m:02d}:{s:02d}"
            payload = {
                "system_time_str": system_time_str,
                "year": year,
                "month": month,
                "day": day,
                "hour": h,
                "minute": m,
                "second": s,
            }
            self.data_received.emit("time synchronization parameters", payload)

    def _get_PV_parameters(self):
        """获取直流(PV)信息参数：PV总功率、当日发电量、PV1/PV2 电压电流功率"""
        data = self._parse_config_group("PV parameters")
        if data:
            self.data_received.emit("PV parameters", data)
        else:
            logger.warning("--- 执行失败: _get_PV_parameters 未获取到有效数据 (可能通讯失败) ---")

    def _get_grid_parameters(self):
        """获取电网信息参数：并网状态、电网功率/频率/电压/电流"""
        data = self._parse_config_group("grid parameters")
        if data:
            # 并网状态解析 (0=断网, 1=并网)
            status_map = {0: "断网", 1: "并网"}
            grid_status_code = data.get("grid_status", 0)
            data["grid_status_str"] = status_map.get(grid_status_code, f"未知({grid_status_code})")
            
            # 高低字打包 (因 config 设定 gain=0.1, 取出值需乘 10 还原后拼装，最后再统一乘 0.1)
            def get_raw(key):
                return int(round(data.get(key, 0) * 10))

            # 累计购电
            data["e_grid_buy_total"] = round(((get_raw("e_grid_buy_total_high") << 16) | get_raw("e_grid_buy_total_low")) * 0.1, 3)

            # 当月购电
            data["e_grid_buy_month"] = round(((get_raw("e_grid_buy_month_high") << 16) | get_raw("e_grid_buy_month_low")) * 0.1, 3)

            # 当年购电
            data["e_grid_buy_year"] = round(((get_raw("e_grid_buy_year_high") << 16) | get_raw("e_grid_buy_year_low")) * 0.1, 3)

            # 累计卖电
            data["e_grid_sell_total"] = round(((get_raw("e_grid_sell_total_high") << 16) | get_raw("e_grid_sell_total_low")) * 0.1, 3)

            # 当月卖电
            data["e_grid_sell_month"] = round(((get_raw("e_grid_sell_month_high") << 16) | get_raw("e_grid_sell_month_low")) * 0.1, 3)

            # 当年卖电
            data["e_grid_sell_year"] = round(((get_raw("e_grid_sell_year_high") << 16) | get_raw("e_grid_sell_year_low")) * 0.1, 3)

            self.data_received.emit("grid parameters", data)

        else:
            logger.warning("--- 执行失败: _get_grid_parameters 未获取到有效数据 (可能通讯失败) ---")

    def _get_load_parameters(self):
        """获取负载/逆变输出信息参数：逆变状态、输出总功率/频率/电压/电流"""
        data = self._parse_config_group("load parameters")
        if data:
            # 逆变状态解析 (0=OFF, 1=ON)
            inv_status_code = data.get("inv_status", 0)
            data["inv_status_str"] = "ON" if inv_status_code == 1 else "OFF"
            self.data_received.emit("load parameters", data)
        else:
            logger.warning("--- 执行失败: _get_load_parameters 未获取到有效数据 (可能通讯失败) ---")

    def _get_gen_port_parameters(self):
        """获取发电机端口参数：继电器状态/开关信号/发电信号、功率/频率/发电量"""
        data = self._parse_config_group("gen port parameters")
        if data:
            # 31221 发电机侧继电器状态字: Bit0-3(继电器状态), Bit4-7(开关信号), Bit8-11(发电信号)
            status_word = int(data.get("gen_relay_status", 0)) & 0xFFFF
            relay_state_code = status_word & 0x0F
            switch_signal_code = (status_word >> 4) & 0x0F
            gen_signal_code = (status_word >> 8) & 0x0F

            relay_state_map = {
                0: "未吸合",
                1: "吸合动作",
                2: "空缺",
                3: "工作状态吸合",
            }
            switch_signal_map = {
                0: "关机",
                1: "开机",
            }

            data["gen_relay_status_hex"] = f"0x{status_word:04X}"
            data["gen_relay_state_code"] = relay_state_code
            data["gen_relay_state_str"] = relay_state_map.get(relay_state_code, f"未知({relay_state_code})")
            data["gen_switch_signal_code"] = switch_signal_code
            data["gen_switch_signal_str"] = switch_signal_map.get(switch_signal_code, f"未知({switch_signal_code})")
            data["gen_signal_code"] = gen_signal_code

            # 高低字打包：config 中该组发电量寄存器 gain=0.1，先还原原始字值再拼接
            def get_raw(key):
                return int(round(data.get(key, 0) * 10))

            data["e_gen_total"] = round(((get_raw("e_gen_total_high_word") << 16) | get_raw("e_gen_total_low_word")) * 0.1, 3)

            self.data_received.emit("gen port parameters", data)
        else:
            logger.warning("--- 执行失败: _get_gen_port_parameters 未获取到有效数据 (可能通讯失败) ---")

    def _get_inverter_information_parameters(self):
        """获取逆变器温度信息参数：逆变器温度、散热器温度、环境温度"""
        data = self._parse_config_group("inverter information parameters")
        if data:
            # 温度偏移处理：原始值偏移+1000，例如 1200 -> 20.0 ℃
            for key in ("temp_inv", "temp_heatsink", "temp_amb"):
                raw = data.get(key, 0)
                data[f"{key}_celsius"] = round((raw - 1000) * 0.1, 1)
            self.data_received.emit("inverter information parameters", data)
        else:
            logger.warning("--- 执行失败: _get_inverter_information_parameters 未获取到有效数据 (可能通讯失败) ---")

    def _get_battery_parameters(self):
        """获取电池组温度信息参数：电池1/2/3 温度"""
        data = self._parse_config_group("battery parameters")
        if data:
            # 电池端口状态：0=无连接，1=充电，2=放电，3=欠压
            bat_status_map = {0: "无连接", 1: "充电", 2: "放电", 3: "欠压"}
            bat_status_code = data.get("bat_status", 0)
            data["bat_status_str"] = bat_status_map.get(bat_status_code, f"未知({bat_status_code})")
            
            
            # 高低字打包：config 中电池累计充放电量寄存器 gain=0.1，先还原原始字值再拼接
            def get_raw(key):
                return int(round(data.get(key, 0) * 10))

            data["e_bat_chg_total"] = round(((get_raw("e_bat_chg_total_high") << 16) | get_raw("e_bat_chg_total_low")) * 0.1, 3)
            data["e_bat_dis_total"] = round(((get_raw("e_bat_dis_total_high") << 16) | get_raw("e_bat_dis_total_low")) * 0.1, 3)

            # 温度偏移处理：原始值偏移+1000，例如 1200 -> 20.0 ℃
            raw_temp = data.get("temp_bat", 0)
            data["temp_bat_celsius"] = round((raw_temp - 1000) * 0.1, 1)
            self.data_received.emit("battery parameters", data)
        else:
            logger.warning("--- 执行失败: _get_battery_parameters 未获取到有效数据 (可能通讯失败) ---")

    def _get_group1_BMS_parameters(self):
        """获取 BMS group1 单体电池电压并发送"""
        data = self._parse_config_group("group1 BMS parameters")
        if data:
            if "temp_cell_avg" in data:
                raw_temp = data["temp_cell_avg"]
                data["temp_cell_avg_raw"] = raw_temp
                # group1 的平均温度按 1000 为 0℃、1 个数值单位对应 1℃ 解析
                data["temp_cell_avg"] = round(raw_temp - 1000, 1)

            self.data_received.emit("group1 BMS parameters", data)
        else:
            logger.warning("--- 执行失败: _get_group1_BMS_parameters 未获取到有效数据 ---")

    def _get_grid_setting_parameters(self):
        data = self._parse_config_group("grid setting parameters")
        self.data_received.emit("grid setting parameters", data)

    """写寄存器函数"""
    def _get_basic_setting_parameters(self):
        """读取基础设置参数（16001），读回后 emit data_received 供 UI 回填"""
        data = self._parse_config_group("basic setting parameters")
        if data:
            self.data_received.emit("basic setting parameters", data)
        else:
            logger.warning("--- 执行失败: _get_basic_setting_parameters 未获取到有效数据 ---")

    def _get_system_work_mode1_parameters(self):
        """读取系统工作模式参数1（16067）"""
        data = self._parse_config_group("system work mode1 parameters")
        if data:
            self.data_received.emit("system work mode1 parameters", data)
        else:
            logger.warning("--- 执行失败: _get_system_work_mode1_parameters 未获取到有效数据 ---")

    def _get_system_work_mode2_parameters(self):
        """读取系统工作模式参数2（16075）"""
        data = self._parse_config_group("system work mode2 parameters")
        if data:
            self.data_received.emit("system work mode2 parameters", data)
        else:
            logger.warning("--- 执行失败: _get_system_work_mode2_parameters 未获取到有效数据 ---")

    def _get_advanced_setting_parameters(self):
        """读取高级设置参数（16031）"""
        data = self._parse_config_group("advanced setting parameters")
        if data:
            self.data_received.emit("advanced setting parameters", data)
        else:
            logger.warning("--- 执行失败: _get_advanced_setting_parameters 未获取到有效数据 ---")

    def _get_grid_setting_parameters(self):
        """读取电网设置参数（16021）"""
        data = self._parse_config_group("grid setting parameters")
        if data:
            self.data_received.emit("grid setting parameters", data)
        else:
            logger.warning("--- 执行失败: _get_grid_setting_parameters 未获取到有效数据 ---")

    def _get_battery_setting_parameters(self):
        """读取电池设置参数（16041）"""
        data = self._parse_config_group("battery setting parameters")
        if data:
            self.data_received.emit("battery setting parameters", data)
        else:
            logger.warning("--- 执行失败: _get_battery_setting_parameters 未获取到有效数据 ---")

    def _get_protection_setting_parameters(self):
        """读取保护参数设置（16061）"""
        data = self._parse_config_group("protection setting parameters")
        if data:
            self.data_received.emit("protection setting parameters", data)
        else:
            logger.warning("--- 执行失败: _get_protection_setting_parameters 未获取到有效数据 ---")

    def _set_time_synchronization_parameters(self):
        """设置 PCS 时间（对时）：写入当前系统时间到 PCS"""
        try:
            now = datetime.datetime.now()

            # 直接使用已有的 PCS_REGISTER_CONFIG 中的 time synchronization parameters 配置
            cfg = PCS_REGISTER_CONFIG["time synchronization parameters"]
            start_addr = cfg["start_addr"]

            # 构建写入数据：秒、分、时、日、月、年（按配置顺序）
            values = [
                now.second,       # 秒 [0,59]
                now.minute,       # 分 [0,59]
                now.hour,         # 时 [0,23]
                now.day,          # 日 [1,31]
                now.month,        # 月 [1,12]
                now.year,  # 年，偏移2000 [0,9999]
            ]

            success = self._write_holding_registers(start_addr, values)

            if success:
                logger.info(f"PCS对时成功: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                # 发送信号通知 UI
                self.data_received.emit("time_sync_result", {
                    "success": True,
                    "message": f"对时成功: {now.strftime('%Y-%m-%d %H:%M:%S')}"
                })
            else:
                logger.warning("PCS对时失败")
                self.data_received.emit("time_sync_result", {
                    "success": False,
                    "message": "对时失败，请检查通讯"
                })
        except Exception as e:
            logger.error(f"PCS对时异常: {e}", exc_info=True)
            self.data_received.emit("time_sync_result", {
                "success": False,
                "message": f"对时异常: {str(e)}"
            })