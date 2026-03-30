# config.py

# 日志全局配置
LOG_CONFIG = {
	"log_dir": "./logs",
	"log_file": "system_run.log",
	"max_bytes": 5 * 1024 * 1024,
	"backup_count": 10,
	"log_level": "DEBUG",
}

# CAN总线全局配置
CAN_CONFIG = {
	"channel": "can0",	# can0 can1 can2
	"bustype": "socketcan", # socketcan(Linux), kvaser, slcan, etc.
    "bitrate": 500000,	# 125000 250000 500000 1000000
	"timeout": 0.2,
    "max_missing_tail_bytes": 1,	# 允许多帧尾帧最多缺失字节数，0表示关闭容错
}

# EMS协议全局配置
EMS_PROTOCOL_CONFIG = {
    # EMS CAN ID 配置
    "tx_can_id": 0x305,
    "rx_can_ids": [0x305,None,...],  # 可以包含多个接收ID，None表示占位符

    # EMS_Running_Data
    "EMS_Running_Data": {
        "tx_data": [0x02, 0x03, 0x01],
        "data_length": 17,
        "map": {
            "system_time": {"byte": 0, "length": 7},
            # 待添加
            # "connection_state": {"byte": 7, "length": 2}, 
            # "fault_code": {"byte": 9, "length": 2},
        }
    },

    # EMS_Realtime_Data
    # "EMS_Realtime_Data": {
    #     "tx_data": [0x02, 0x03, 0x02],
    #     "data_length": 47,
    #     "map": {
    #         # 待添加
    #         "device_type": {"byte": 0, "length": 2, "resolution": 1, "offset": 0}, 
    #         "protocol_version": {"byte": 2, "length": 2, "resolution": 1, "offset": 0}, 
    #         "rated_power": {"byte": 4, "length": 2, "resolution": 1, "offset": 0}, 
    #         "rated_power_low": {"byte": 6, "length": 2, "resolution": 1, "offset": 0}, 
    #         "rated_power_high": {"byte": 8, "length": 2, "resolution": 1, "offset": 0}, 
    #         "grid_voltage_level": {"byte": 10, "length": 2, "resolution": 1, "offset": 0}, 
    #         "run_state_high": {"byte": 12, "length": 2, "resolution": 1, "offset": 0}, 
    #         "run_state_low": {"byte": 14, "length": 2, "resolution": 1, "offset": 0}, 
    #         "radiator_temp": {"byte": 16, "length": 2, "resolution": 1, "offset": 0}, 
    #         "igbt_temp": {"byte": 18, "length": 2, "resolution": 1, "offset": 0}, 
    #         "total_gen_wh_low": {"byte": 20, "length": 2, "resolution": 1, "offset": 0},
    #         "power_factor": {"byte": 22, "length": 2, "resolution": 0.001, "offset": 0}, 
    #         "warning_1": {"byte": 24, "length": 2}, 
    #         "warning_2": {"byte": 26, "length": 2}, 
    #         "fault_1_high": {"byte": 28, "length": 2}, 
    #         "fault_1_low": {"byte": 30, "length": 2}, 
    #         "dc_voltage_1": {"byte": 38, "length": 2, "resolution": 0.1, "offset": 0}, 
    #         "dc_current_1_high": {"byte": 40, "length": 2}, 
    #         "dc_current_1_low": {"byte": 42, "length": 2}, 
    #     }
    # },

    # EMS 其他参数
    ######################
    # 待添加
}
