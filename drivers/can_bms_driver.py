import logging
import struct
from PySide6.QtCore import QObject, Signal
from config import BMS_PROTOCOL_CONFIG

logger = logging.getLogger(__name__)


class CANBMSDriver(QObject):
    """BMS CAN 驱动：被动接收 BMS 广播帧，解析后通过信号推送给上层。"""

    # 信号：(消息名称, 解析后的数据字典)
    data_received = Signal(str, dict)

    def __init__(self):
        super().__init__()
        self.bus = None
        self.running = False

    def open_bus(self, bus):
        """激活驱动：由 CANBMSWorker 注入共享总线对象。"""
        self.bus = bus
        self.running = True

    def close_bus(self):
        """停止驱动：清理自身状态，总线由 CANBMSWorker 负责关闭。"""
        self.running = False
        self.bus = None

    def process_msg(self, msg):
        """解析一帧 BMS CAN 报文，解析成功则 emit data_received 信号。"""
        can_id = msg.arbitration_id
        data = msg.data
        if not data:
            return

        # 在配置表中查找匹配该 ID 的消息定义
        msg_cfg = None
        msg_name = ""
        for name, cfg in BMS_PROTOCOL_CONFIG.items():
            if isinstance(cfg, dict) and cfg.get("can_id") == can_id:
                msg_cfg = cfg
                msg_name = name
                break

        if not msg_cfg:
            return

        # 解析字段
        parsed_data = {}
        byte_order = BMS_PROTOCOL_CONFIG.get("byte_order", "little")

        for field_name, rules in msg_cfg.get("map", {}).items():
            byte_idx = rules.get("byte", 0)
            length = rules.get("length", 1)

            if byte_idx + length > len(data):
                continue

            if "bit" in rules:
                # 单 bit 提取
                val = (data[byte_idx] >> rules["bit"]) & 0x01
            else:
                raw_bytes = data[byte_idx: byte_idx + length]
                if length == 1:
                    val = raw_bytes[0]
                elif length == 2:
                    fmt = "<H" if byte_order == "little" else ">H"
                    val = struct.unpack(fmt, raw_bytes)[0]
                else:
                    val = int.from_bytes(raw_bytes, byteorder=byte_order)

                val = val * rules.get("resolution", 1) + rules.get("offset", 0)

            parsed_data[field_name] = val

        if parsed_data:
            logger.debug("CAN BMS 解析成功: [%s] ID=0x%X -> %s", msg_name, can_id, parsed_data)
            self.data_received.emit(msg_name, parsed_data)
