# config.py
# 日志全局配置
LOG_CONFIG = {
    "log_dir": "./data/logs",
    "log_file": "system_run.log",
    "max_bytes": 5 * 1024 * 1024,
    "backup_count": 10,
    "log_level": "INFO",
}

# ================================================================
# 多台 PCS 独立串口配置（五台联调）
# 每台对应一条物理 RS485 线，串口号按实际接线填写
# ================================================================
MULTI_PCS_CONFIG = [
    {"id": 1, "name": "PCS---1", "port": "/dev/com2"},
    {"id": 2, "name": "PCS---2", "port": "/dev/com4"},
    {"id": 3, "name": "PCS---3", "port": "COM3"},
    {"id": 4, "name": "PCS---4", "port": "COM4"},
    {"id": 5, "name": "PCS---5", "port": "COM5"},
]

# RS485 串口全局配置（保留兼容，多台联调时各台沿用这份通用参数，port 由 MULTI_PCS_CONFIG 覆盖）
RS485_CONFIG = {
    "port": "/dev/com2",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 0.5,
    "poll_ms": 250,
    "protocol": {
        "head_frame": [0x5A, 0xA5],
        "end_frame": 0x16,
        "read_ctrl_frame": 0x03,
        "write_ctrl_frame": 0x10,
        "addr1": 0x0000,
        "addr2": 0x0000,
        "strict_addr_check": False, # 调试阶段建议先关闭严格地址校验，联调稳定后可改为 True
        "max_frame_len": 1024,
        "inter_frame_delay": 0.005, # 5ms，EMS/PCS协议要求帧间至少3ms，留点余量避免时序问题
    },
}

# PCS 寄存器配置 (基于储能机通讯协议配置)
"""PCS寄存器配置，包含:
    【读取参数 - 功能码 03H】
    1. 对时参数 | 58001
    2. PCS基本信息参数（包含故障码） | 11001
    3. 其他信息参数(温度) | 11031
    4. 直流(PV)信息参数 | 21001 (PV1-PV8 电压/电流/功率)
    5. 交流电网信息参数 | 31001 (A/B/C相电压/电流/功率/频率/累计电量)
    6. 交流负载信息参数 | 31151 (L1/L2/L3电压/电流/功率/频率/累计电量)
    7. 发动机端口信息参数 | 31221 (SOC/电压/电流/功率/温度/累计电量)
    8. 逆变器信息参数 | 41001 (温度信息)
    9. 电池信息参数 | 43001 (SOC/电压/电流/功率/温度/累计电量)
    10. 第一组BMS参数 | 43064 (平均电压/总电流/SOC/SOH/电芯温度/充放电限值/告警故障位/模块数)
    11. 第二组BMS参数 | 43105 (单体电压17-32)
    12. 第三组BMS参数 | 43121 (单体电压33-36)
    13. 第四组BMS参数 | 43125 (单体电压37-40)
    14. 第五组BMS参数 | 43129 (单体电压41-44)
    15. 第六组BMS参数 | 43133 (单体电压45-48)
    
    【写入参数 - 功能码 10H】
    16. 基础设置参数 | 16001 (功率调节/开关机/自检时间/PV扫描/MPPT等) - 15个寄存器
    17. 系统工作模式参数1 | 16067 (电池类型/工作模式/光伏卖电/能量模式) - 8个寄存器
    18. 系统工作模式参数2 | 16075 (电网功率限制/分时电价/功率段/电压电量限制) - 39个寄存器
    18. 电网设置参数 | 26001 (并网模式/电压频率等级/保护点等)
    19. 电池设置参数 | 36001 (Battery_Type/Battery_Mode/充放电阈值等)
    20. 系统高级设置参数 | 16164 (并机使能/主从设置/相位选择等) - 8个寄存器
    21. 保护参数设置 | 46001 (过压/欠压/过流/过温保护等)
    
    【故障码对照表】
    20. 故障码对应表
    """
PCS_REGISTER_CONFIG = {
    # 对时参数
    "time synchronization parameters": {
        "start_addr": 58001,
        "registers": {
            "sec":   {"off": 0, "size": 1, "gain": 1, "unit": "s", "desc": "秒 [0,59]"},
            "min":   {"off": 1, "size": 1, "gain": 1, "unit": "m", "desc": "分 [0,59]"},
            "hour":  {"off": 2, "size": 1, "gain": 1, "unit": "h", "desc": "时 [0,23]"},
            "day":   {"off": 3, "size": 1, "gain": 1, "unit": "d", "desc": "日 [1,31]"},
            "month": {"off": 4, "size": 1, "gain": 1, "unit": "m", "desc": "月 [1,12]"},
            "year":  {"off": 5, "size": 1, "gain": 1, "unit": "y", "desc": "年 [2000,9999]"},
        }
    },
    # 基本信息参数
    "PCS device information parameters": {
        "start_addr": 11001,
        "registers": {
            "device_type":   {"off": 0, "size": 1, "gain": 1, "unit": "--", "desc": "设备类型"},
            "modbus_addr":   {"off": 1, "size": 1, "gain": 1, "unit": "--", "desc": "Modbus地址 [1-247]"},
            "version":       {"off": 2, "size": 1, "gain": 1, "unit": "--", "desc": "通讯协议版本 [0x0102代表1.2版]"},
            "run_state":     {"off": 3, "size": 1, "gain": 1, "unit": "--", "desc": "运行状态 [0=等待 1=自检 2=正常 4=故障]"},    
            "AC_output_type": {"off": 4, "size": 1, "gain": 1, "unit": "--", "desc": "交流输出类型"},
            # 0： LN：220VAC LL：380VAC
            # 1： LN：230VAC LL：400VAC
            # 2： LN：240VAC LL：420VAC
            # 3： LN：120VAC LL：208VAC
            # 4： LN：133VAC LL：230VAC
            "rated_power_low_word": {"off": 5, "size": 1, "gain": 0.1, "unit": "W", "desc": "额定功率低字"},
            "rated_power_high_word": {"off": 6, "size": 1, "gain": 0.1, "unit": "W", "desc": "额定功率高字"},
            "MPPT_nums": {"off": 7, "size": 1, "gain": 1, "unit": "--", "desc": "MPPT数量"},
            "phases": {"off": 8, "size": 1, "gain": 1, "unit": "--", "desc": "相数"},
            "reserved": {"off": 9, "size": 1, "gain": 1, "unit": "--", "desc": "保留"},
            "SN_code": {"off": 10, "size": 5, "gain": 1, "unit": "--", "desc": "序列号"},
            "comm_board_version": {"off": 15, "size": 1, "gain": 1, "unit": "--", "desc": "显示板ARM程序版本"},
            "control_board_version_low": {"off": 16, "size": 1, "gain": 1, "unit": "--", "desc": "控制板主DSP程序版本低字节"},
            "slave_control_board_version": {"off": 17, "size": 1, "gain": 1, "unit": "--", "desc": "控制板辅DSP程序版本"},
            "warning_message_1": {"off": 18, "size": 1, "gain": 1, "unit": "--", "desc": "告警信息第1字"},
            "warning_message_2": {"off": 19, "size": 1, "gain": 1, "unit": "--", "desc": "告警信息第2字"},
            "fault_code_1": {"off": 20, "size": 1, "gain": 1, "unit": "--", "desc": "故障码第1字"},
            "fault_code_2": {"off": 21, "size": 1, "gain": 1, "unit": "--", "desc": "故障码第2字"},
            "fault_code_3": {"off": 22, "size": 1, "gain": 1, "unit": "--", "desc": "故障码第3字"},
            "fault_code_4": {"off": 23, "size": 1, "gain": 1, "unit": "--", "desc": "故障码第4字"},
            "control_board_version_high": {"off": 24, "size": 1, "gain": 1, "unit": "--", "desc": "控制板主DSP程序版本高字节"},
        }
    },
    # 其他信息参数(关于温度)
    "other information parameters": {
        "start_addr": 11031,
        "registers": {
            "transformer_temperature": {"off": 0, "size": 1, "gain": 0.1, "unit": "℃", "desc": "变压器温度[0-3000]"},
            "BOOST_inductance _temperature": {"off": 1, "size": 1, "gain": 1, "unit": "℃", "desc": "BOOST电感温度"},
            "INV_inductance_temperature": {"off": 2, "size": 1, "gain": 1, "unit": "℃", "desc": "逆变电感温度"},
            "internal_temperature": {"off": 3, "size": 1, "gain": 1, "unit": "℃", "desc": "机箱内（PCB板）温度"},
            "radiator_temperature1":{"off": 4, "size": 1, "gain": 0.1, "unit": "℃", "desc": "散热器点1温度"},
            "radiator_temperature2":{"off": 5, "size": 1, "gain": 0.1, "unit": "℃", "desc": "散热器点2温度"},
            "radiator_temperature3":  {"off": 6, "size": 1, "gain": 0.1, "unit": "℃", "desc": "散热器点3温度"},
            "radiator_temperature4":   {"off": 7, "size": 1, "gain": 0.1, "unit": "℃", "desc": "散热器点4温度"},
        }
    },
    "PV parameters": {
        "start_addr": 21001,
        "registers": {
            "p_pv_total": {"off": 0, "gain": 1, "unit": "W", "desc": "PV总功率"},
            "e_pv_day":   {"off": 1, "gain": 0.1, "unit": "kWh", "desc": "当日PV发电量"},
            "e_pv_total_low_word": {"off": 2, "gain": 0.1, "unit": "kWh", "desc": "总有功发电量低字"},
            "e_pv_total_high_word": {"off": 3, "gain": 0.1, "unit": "kWh", "desc": "总有功发电量高字"},
            "v_pv1":      {"off": 4, "gain": 0.1, "unit": "V", "desc": "PV1电压"},
            "i_pv1":      {"off": 5, "gain": 0.1, "unit": "A", "desc": "PV1电流"},
            "p_pv1":      {"off": 6, "gain": 1, "unit": "W", "desc": "PV1输入功率"},
            "v_pv2":      {"off": 7, "gain": 0.1, "unit": "V", "desc": "PV2电压"},
            "i_pv2":      {"off": 8, "gain": 0.1, "unit": "A", "desc": "PV2电流"},
            "p_pv2":      {"off": 9, "gain": 1, "unit": "W", "desc": "PV2输入功率"},
            "v_pv3":      {"off": 10, "gain": 0.1, "unit": "V", "desc": "PV3电压"},
            "i_pv3":      {"off": 11, "gain": 0.1, "unit": "A", "desc": "PV3电流"},
            "p_pv3":      {"off": 12, "gain": 1, "unit": "W", "desc": "PV3输入功率"},
            "v_pv4":      {"off": 13, "gain": 0.1, "unit": "V", "desc": "PV4电压"},
            "i_pv4":      {"off": 14, "gain": 0.1, "unit": "A", "desc": "PV4电流"},
            "p_pv4":      {"off": 15, "gain": 1, "unit": "W", "desc": "PV4输入功率"},
            "v_pv5":      {"off": 16, "gain": 0.1, "unit": "V", "desc": "PV5电压"},
            "i_pv5":      {"off": 17, "gain": 0.1, "unit": "A", "desc": "PV5电流"},
            "p_pv5":      {"off": 18, "gain": 1, "unit": "W", "desc": "PV5输入功率"},
            "v_pv6":      {"off": 19, "gain": 0.1, "unit": "V", "desc": "PV6电压"},
            "i_pv6":      {"off": 20, "gain": 0.1, "unit": "A", "desc": "PV6电流"},
            "p_pv6":      {"off": 21, "gain": 1, "unit": "W", "desc": "PV6输入功率"},
            "v_pv7":      {"off": 22, "gain": 0.1, "unit": "V", "desc": "PV7电压"},
            "i_pv7":      {"off": 23, "gain": 0.1, "unit": "A", "desc": "PV7电流"},
            "p_pv7":      {"off": 24, "gain": 1, "unit": "W", "desc": "PV7输入功率"},
            "v_pv8":      {"off": 25, "gain": 0.1, "unit": "V", "desc": "PV8电压"},
            "i_pv8":      {"off": 26, "gain": 0.1, "unit": "A", "desc": "PV8电流"},
            "p_pv8":      {"off": 27, "gain": 1, "unit": "W", "desc": "PV8输入功率"},
        }
    },
    "grid parameters": {
        "start_addr": 31001,
        "registers": {
            "grid_status": {"off": 0, "gain": 1, "unit": "--", "desc": "并网状态 [0:Disconnect, 1:Closed]"},
            "p_grid":      {"off": 1, "gain": 1, "unit": "W", "desc": "电网功率 [>0购电, <0并网]"},
            "freq_grid":   {"off": 2, "gain": 0.01, "unit": "Hz", "desc": "电网频率"},
            "v_grid_a":    {"off": 3, "gain": 0.1, "unit": "V", "desc": "A相电网电压"},
            "v_grid_b":    {"off": 4, "gain": 0.1, "unit": "V", "desc": "B相电网电压"},
            "v_grid_c":    {"off": 5, "gain": 0.1, "unit": "V", "desc": "C相电网电压"},
            "i_grid_a":    {"off": 6, "gain": 0.1, "unit": "A", "desc": "A相电网电流"},
            "i_grid_b":    {"off": 7, "gain": 0.1, "unit": "A", "desc": "B相电网电流"},
            "i_grid_c":    {"off": 8, "gain": 0.1, "unit": "A", "desc": "C相电网电流"},
            "p_grid_a":    {"off": 9, "gain": 1, "unit": "W", "desc": "A相输出功率"},
            "p_grid_b":    {"off": 10, "gain": 1, "unit": "W", "desc": "B相输出功率"},
            "p_grid_c":    {"off": 11, "gain": 1, "unit": "W", "desc": "C相输出功率"},
            "freq_grid_a": {"off": 12, "gain": 0.01, "unit": "Hz", "desc": "A相电网频率"},
            "freq_grid_b": {"off": 13, "gain": 0.01, "unit": "Hz", "desc": "B相电网频率"},
            "freq_grid_c": {"off": 14, "gain": 0.01, "unit": "Hz", "desc": "C相电网频率"},
            "i_limiter_l1": {"off": 15, "gain": 0.1, "unit": "A", "desc": "L1防逆流Limiter1电流"},
            "i_limiter_l2": {"off": 16, "gain": 0.1, "unit": "A", "desc": "L2防逆流Limiter2电流"},
            "p_limiter_l1": {"off": 18, "gain": 1, "unit": "W", "desc": "L1-Limiter1功率"},
            "p_limiter_l2": {"off": 19, "gain": 1, "unit": "W", "desc": "L2-Limiter2功率"},
            "p_limiter_total": {"off": 21, "gain": 1, "unit": "W", "desc": "电网外置总功率"},
            "e_grid_buy_day": {"off": 22, "gain": 0.1, "unit": "kWh", "desc": "电网当日购电量"},
            "e_grid_buy_month_low": {"off": 23, "gain": 0.1, "unit": "kWh", "desc": "电网当月购电量低字"},
            "e_grid_buy_year_low": {"off": 24, "gain": 0.1, "unit": "kWh", "desc": "电网当年购电量低字"},
            "e_grid_buy_total_low": {"off": 25, "gain": 0.1, "unit": "kWh", "desc": "电网累计购电量低字"},
            "e_grid_buy_total_high": {"off": 26, "gain": 0.1, "unit": "kWh", "desc": "电网累计购电量高字"},
            "e_grid_sell_day": {"off": 27, "gain": 0.1, "unit": "kWh", "desc": "电网当日卖电量"},
            "e_grid_sell_month_low": {"off": 28, "gain": 0.1, "unit": "kWh", "desc": "电网当月卖电量低字"},
            "e_grid_sell_year_low": {"off": 29, "gain": 0.1, "unit": "kWh", "desc": "电网当年卖电量低字"},
            "e_grid_sell_total_low": {"off": 30, "gain": 0.1, "unit": "kWh", "desc": "电网累计卖电量低字"},
            "e_grid_sell_total_high": {"off": 31, "gain": 0.1, "unit": "kWh", "desc": "电网累计卖电量高字"},
            "e_grid_buy_month_high": {"off": 32, "gain": 0.1, "unit": "kWh", "desc": "电网当月购电量高字"},
            "e_grid_buy_year_high": {"off": 33, "gain": 0.1, "unit": "kWh", "desc": "电网当年购电量高字"},
            "e_grid_sell_month_high": {"off": 34, "gain": 0.1, "unit": "kWh", "desc": "电网当月卖电量高字"},
            "e_grid_sell_year_high": {"off": 35, "gain": 0.1, "unit": "kWh", "desc": "电网当年卖电量高字"},
        }
    },
    "load parameters": {
        "start_addr": 31151,
        "registers": {
            "p_load_total": {"off": 0, "gain": 1, "unit": "W", "desc": "负载侧总功率"},
            "e_load_day": {"off": 1, "gain": 0.1, "unit": "kWh", "desc": "当日用电量"},
            "e_load_total_low_word": {"off": 2, "gain": 0.1, "unit": "kWh", "desc": "累计用电量低字"},
            "e_load_total_high_word": {"off": 3, "gain": 0.1, "unit": "kWh", "desc": "累计用电量高字"},
            "v_load_l1": {"off": 4, "gain": 0.1, "unit": "V", "desc": "负载侧L1电压"},
            "p_load_l1": {"off": 7, "gain": 1, "unit": "W", "desc": "负载侧L1功率"},
            "i_load_l1": {"off": 10, "gain": 0.01, "unit": "A", "desc": "负载侧L1电流"},
            "freq_load_1": {"off": 13, "gain": 0.01, "unit": "Hz", "desc": "负载侧L1频率"},
            "freq_load_3": {"off": 15, "gain": 0.01, "unit": "Hz", "desc": "负载侧L3频率"},
            "e_load_month_low_word": {"off": 16, "gain": 0.1, "unit": "kWh", "desc": "当月用电量低字"},
            "e_load_year_low_word": {"off": 17, "gain": 0.1, "unit": "kWh", "desc": "当年用电量低字"},
            "e_load_year_high_word": {"off": 18, "gain": 0.1, "unit": "kWh", "desc": "当年用电量高字"},
            "e_load_month_high_word": {"off": 19, "gain": 0.1, "unit": "kWh", "desc": "当月用电量高字"},
        }
    },
    "gen port parameters": {
        "start_addr": 31221,
        "registers": {
            "gen_relay_status": {"off": 0, "gain": 1, "unit": "--", "desc": "发电机侧继电器状态 [低4位Bit0-3: 0未吸合/1吸合动作/2空闲/3工作状态; 高4位Bit4-7: 开关信号0关机/1开机; Bit8-11: 发电信号]"},
            "p_gen_total": {"off": 1, "gain": 1, "unit": "W", "desc": "发电机总功率"},
            "freq_gen": {"off": 2, "gain": 0.01, "unit": "Hz", "desc": "发电机频率"},
            "e_gen_day": {"off": 3, "gain": 0.1, "unit": "kWh", "desc": "当日发电机发电量"},
            "e_gen_total_low_word": {"off": 4, "gain": 0.1, "unit": "kWh", "desc": "总发电机发电量低位"},
            "e_gen_total_high_word": {"off": 5, "gain": 0.1, "unit": "kWh", "desc": "总发电机发电量高位"},
            "v_gen_l1": {"off": 6, "gain": 0.1, "unit": "V", "desc": "发电机L1电压"},
            "p_gen_l1": {"off": 9, "gain": 1, "unit": "W", "desc": "发电机L1功率"},
            "p_smart_load": {"off": 12, "gain": 1, "unit": "W", "desc": "Gen端口做负载输出的功率"},
        }
    },
    "inverter information parameters": {
        "start_addr": 41001,
        "registers": {
            "temp_inv":   {"off": 0, "gain": 0.1, "unit": "℃", "desc": "逆变器温度 [偏移+1000, 1200即20.0℃]"},
            "temp_heatsink":{"off": 1, "gain": 0.1, "unit": "℃", "desc": "散热器温度 [偏移+1000, 1200即20.0℃]"},
            "temp_amb":   {"off": 2, "gain": 0.1, "unit": "℃", "desc": "环境温度 [偏移+1000, 1200即20.0℃]"},
        }
    },
    "battery parameters": {
        "start_addr": 43001,
        "registers": {
            "bat_status": {"off": 0, "gain": 1, "unit": "--", "desc": "电池端口状态 [0:无连接 1:充电 2:放电 3:欠压]"},
            "v_bat":      {"off": 1, "gain": 0.01, "unit": "V", "desc": "电池电压"},
            "i_bat":      {"off": 2, "gain": 0.01, "unit": "A", "desc": "电池输出电流"},
            "p_bat":      {"off": 3, "gain": 1, "unit": "W", "desc": "电池输出功率"},
            "soc_bat":    {"off": 4, "gain": 1, "unit": "%", "desc": "电池剩余电量(SOC) [0-100]"},
            "temp_bat":   {"off": 5, "gain": 0.1, "unit": "℃", "desc": "电池温度 [偏移+1000, 1200即20.0℃]"},
            "e_bat_chg_day":  {"off": 6, "gain": 0.1, "unit": "kWh", "desc": "电池当日充电量"},
            "e_bat_dis_day":  {"off": 7, "gain": 0.1, "unit": "kWh", "desc": "电池当日放电量"},
            "e_bat_chg_total_low": {"off": 8, "gain": 0.1, "unit": "kWh", "desc": "电池累计充电量低字"},
            "e_bat_chg_total_high": {"off": 9, "gain": 0.1, "unit": "kWh", "desc": "电池累计充电量高字"},
            "e_bat_dis_total_low": {"off": 10, "gain": 0.1, "unit": "kWh", "desc": "电池累计放电量低字"},
            "e_bat_dis_total_high": {"off": 11, "gain": 0.1, "unit": "kWh", "desc": "电池累计放电量高字"},
        }
    },
    "group1 BMS parameters": {
        "start_addr": 43064,
        "registers": {
            "v_cell_mean": {"off": 0, "gain": 0.01, "unit": "V", "desc": "锂电池平均电压"},
            "i_cell_total": {"off": 1, "gain": 0.01, "unit": "A", "desc": "锂电池总电流"},
            "soc_bms": {"off": 2, "gain": 1, "unit": "%", "desc": "锂电池剩余电量百分比(SOC)"},
            "dump_energy": {"off": 3, "gain": 1, "unit": "Ah", "desc": "锂电池剩余电量"},
            "soh_bms": {"off": 4, "gain": 1, "unit": "%", "desc": "锂电池健康状态(SOH)"},
            "temp_cell_avg": {"off": 5, "gain": 0.1, "unit": "℃", "desc": "电芯平均温度 [1000对应0℃]"},
            "charging_voltage": {"off": 6, "gain": 0.01, "unit": "V", "desc": "锂电池充电电压限值"},
            "discharge_voltage": {"off": 7, "gain": 0.01, "unit": "V", "desc": "锂电池放电电压限值"},
            "charging_current_limiting": {"off": 8, "gain": 0.01, "unit": "A", "desc": "锂电池充电限流"},
            "discharge_current_limiting": {"off": 9, "gain": 0.01, "unit": "A", "desc": "锂电池放电限流"},
            "lithium_battery_alarm_position": {"off": 10, "gain": 1, "unit": "--", "desc": "锂电池告警位"},
            "lithium_battery_fault_location": {"off": 11, "gain": 1, "unit": "--", "desc": "锂电池故障位"},
            "lithium_battery_symbol_2": {"off": 12, "gain": 1, "unit": "--", "desc": "锂电池标志2"},
            "module_numbers": {"off": 13, "gain": 1, "unit": "--", "desc": "模块数量"},
        }
    },
    "Fault code table": {
        "BIT0": "F001: EPSOverLoadAlarm (EPS过载告警)",
        "BIT2": "F003: Gfci Fault(CheckMode) (漏电流故障检查模式)",
        "BIT4": "F005: Eeprom Read Fault (存储器读失败)",
        "BIT5": "F006: Eeprom Write Fault (存储器写失败)",
        "BIT6": "F007: Low BUS or NO BATT (母线电压低或无电池)",
        "BIT7": "F008: InvGrid Volt Delta (逆变电网电压差)",
        "BIT8": "F009: Batt Aux OFF (电池辅助断开)",
        "BIT9": "F010: Aux Power Fault (辅助电源故障)",
        "BIT10": "F011: LiBattAlarm (锂电池告警)",
        "BIT11": "F012: LiBattError (锂电池故障)",
        "BIT12": "F013: Batt setting Fault (电池设置故障)",
        "BIT13": "F014: PV OCP (光伏过流保护)",
        "BIT14": "F015: Inv Ocp (逆变器过流保护)"
    },
    
    # 下发系统基础设置参数 (功能码 10H) - 起始地址 16001
    "basic setting parameters": {
        "start_addr": 16001,
        "registers": {
            # 16001
            "power_factor_regulation": {"off": 0, "gain": 0.001, "unit": "--", "desc": "功率因数调节 [0,2000]"},
            # 16002
            "active_power_regulation": {"off": 1, "gain": 0.1, "unit": "%", "desc": "有功功率调节 [0,1200]"},
            # 16003
            "reactive_power_regulation": {"off": 2, "gain": 0.1, "unit": "%", "desc": "无功功率调节 [0,1200]"},
            # 16004
            "apparent_power_regulation": {"off": 3, "gain": 0.1, "unit": "%", "desc": "视在功率调节 [0,1200]"},
            # 16005
            "switch_on_off_enable": {"off": 4, "gain": 1, "unit": "--", "desc": "开关机使能 [0:ON 1:OFF]"},
            # 16006
            "factory_reset_enable": {"off": 5, "gain": 1, "unit": "--", "desc": "恢复出厂设置 [0:disable 1:enable]"},
            # 16007
            "self_checking_time": {"off": 6, "gain": 1, "unit": "s", "desc": "自检时间 [0,600]"},
            # 16008
            "pv_shadow_scanning_function": {"off": 7, "gain": 1, "unit": "--", "desc": "PV阴影扫描功能"},
            # 16009
            "scan_period": {"off": 8, "gain": 1, "unit": "h", "desc": "扫描周期"},
            # 16010
            "mppt_numbers": {"off": 9, "gain": 1, "unit": "--", "desc": "MPPT数量"},
            # 16011
            "meter_enable": {"off": 10, "gain": 1, "unit": "--", "desc": "电表使能"},
            # 16012
            "rcd_enable": {"off": 11, "gain": 1, "unit": "--", "desc": "RCD使能"},
            # 16013
            "riso_enable": {"off": 12, "gain": 1, "unit": "--", "desc": "RISO使能"},
            # 16014
            "open_loop_instruction": {"off": 13, "gain": 1, "unit": "--", "desc": "开环指令"},
            # 16015
            "manual_removal_permanent_fault": {"off": 14, "gain": 1, "unit": "--", "desc": "手动清除永久故障"},
        }
    },
    # 系统工作模式参数1 (功能码 10H) - 起始地址 16067, 8个寄存器
    "system work mode1 parameters": {
        "start_addr": 16067,
        "registers": {
            # 16067
            "battery_type": {"off": 0, "gain": 1, "unit": "--", "desc": "电池类型"},
            # 16068
            "battery_mode": {"off": 1, "gain": 1, "unit": "--", "desc": "电池模式"},
            # 16069
            "system_work_mode": {"off": 2, "gain": 1, "unit": "--", "desc": "系统工作模式"},
            # 16070
            "solar_sell": {"off": 3, "gain": 1, "unit": "--", "desc": "光伏卖电使能"},
            # 16071
            "max_solar_power": {"off": 4, "gain": 1, "unit": "W", "desc": "最大光伏功率"},
            # 16072
            "max_sell_power": {"off": 5, "gain": 1, "unit": "W", "desc": "最大卖电功率"},
            # 16073
            "zero_export_power": {"off": 6, "gain": 1, "unit": "W", "desc": "防逆流功率"},
            # 16074
            "energy_pattern": {"off": 7, "gain": 1, "unit": "--", "desc": "能量模式"},
        }
    },
    # 系统工作模式参数2 (功能码 10H) - 起始地址 16075, 39个寄存器
    "system work mode2 parameters": {
        "start_addr": 16077,
        "registers": {
            # # 16075
            # "grid_output_power_limit_enable": {"off": 0, "gain": 1, "unit": "--", "desc": "电网输出功率限制使能"},
            # # 16076
            # "grid_output_power_limit": {"off": 1, "gain": 1, "unit": "W", "desc": "电网输出功率限制"},
            # 16077
            "time_of_use_enable": {"off": 0, "gain": 1, "unit": "--", "desc": "分时电价使能"},
            # 16078-16083 电网充电时段1-6
            "grid_charge1": {"off": 1, "gain": 1, "unit": "--", "desc": "电网充电时段1"},
            "grid_charge2": {"off": 2, "gain": 1, "unit": "--", "desc": "电网充电时段2"},
            "grid_charge3": {"off": 3, "gain": 1, "unit": "--", "desc": "电网充电时段3"},
            "grid_charge4": {"off": 4, "gain": 1, "unit": "--", "desc": "电网充电时段4"},
            "grid_charge5": {"off": 5, "gain": 1, "unit": "--", "desc": "电网充电时段5"},
            "grid_charge6": {"off": 6, "gain": 1, "unit": "--", "desc": "电网充电时段6"},
            # 16084-16089 发电机充电时段1-6
            "gen_charge1": {"off": 7, "gain": 1, "unit": "--", "desc": "发电机充电时段1"},
            "gen_charge2": {"off": 8, "gain": 1, "unit": "--", "desc": "发电机充电时段2"},
            "gen_charge3": {"off": 9, "gain": 1, "unit": "--", "desc": "发电机充电时段3"},
            "gen_charge4": {"off": 10, "gain": 1, "unit": "--", "desc": "发电机充电时段4"},
            "gen_charge5": {"off": 11, "gain": 1, "unit": "--", "desc": "发电机充电时段5"},
            "gen_charge6": {"off": 12, "gain": 1, "unit": "--", "desc": "发电机充电时段6"},
            # 16090-16095 时间段1-6
            "time1": {"off": 15, "gain": 1, "unit": "--", "desc": "时间段1"},
            "time2": {"off": 16, "gain": 1, "unit": "--", "desc": "时间段2"},
            "time3": {"off": 17, "gain": 1, "unit": "--", "desc": "时间段3"},
            "time4": {"off": 18, "gain": 1, "unit": "--", "desc": "时间段4"},
            "time5": {"off": 19, "gain": 1, "unit": "--", "desc": "时间段5"},
            "time6": {"off": 20, "gain": 1, "unit": "--", "desc": "时间段6"},
            # 16096-16101 功率段1-6
            "power1": {"off": 21, "gain": 1, "unit": "W", "desc": "功率段1 [0,15000]"},
            "power2": {"off": 22, "gain": 1, "unit": "W", "desc": "功率段2 [0,15000]"},
            "power3": {"off": 23, "gain": 1, "unit": "W", "desc": "功率段3 [0,15000]"},
            "power4": {"off": 24, "gain": 1, "unit": "W", "desc": "功率段4 [0,15000]"},
            "power5": {"off": 25, "gain": 1, "unit": "W", "desc": "功率段5 [0,15000]"},
            "power6": {"off": 26, "gain": 1, "unit": "W", "desc": "功率段6 [0,15000]"},
            # 16102-16107 电池电压限制1-6
            "batt_v1": {"off": 27, "gain": 0.01, "unit": "V", "desc": "电池电压限制1 [180-800]"},
            "batt_v2": {"off": 28, "gain": 0.01, "unit": "V", "desc": "电池电压限制2 [180-800]"},
            "batt_v3": {"off": 29, "gain": 0.01, "unit": "V", "desc": "电池电压限制3 [180-800]"},
            "batt_v4": {"off": 30, "gain": 0.01, "unit": "V", "desc": "电池电压限制4 [180-800]"},
            "batt_v5": {"off": 31, "gain": 0.01, "unit": "V", "desc": "电池电压限制5 [180-800]"},
            "batt_v6": {"off": 32, "gain": 0.01, "unit": "V", "desc": "电池电压限制6 [180-800]"},
            # 16108-16113 电池电量限制1-6
            "batt_percent1": {"off": 33, "gain": 1, "unit": "%", "desc": "电池电量限制1 [0,100]"},
            "batt_percent2": {"off": 34, "gain": 1, "unit": "%", "desc": "电池电量限制2 [0,100]"},
            "batt_percent3": {"off": 35, "gain": 1, "unit": "%", "desc": "电池电量限制3 [0,100]"},
            "batt_percent4": {"off": 36, "gain": 1, "unit": "%", "desc": "电池电量限制4 [0,100]"},
            "batt_percent5": {"off": 37, "gain": 1, "unit": "%", "desc": "电池电量限制5 [0,100]"},
            "batt_percent6": {"off": 38, "gain": 1, "unit": "%", "desc": "电池电量限制6 [0,100]"},
        }
    },
    # 系统高级设置参数 (功能码 10H) - 起始地址 16164
    "advanced setting parameters": {
        "start_addr": 16164,
        "registers": {
            "parallel_enable": {"off": 0, "gain": 1, "unit": "--", "desc": "并机使能 [0:Disable 1:Enable]"},
            "parallel_serial_number": {"off": 1, "gain": 1, "unit": "--", "desc": "并机序列号 [0,63]"},
            "master_slave": {"off": 2, "gain": 1, "unit": "--", "desc": "主从设置 [0:Slave 1:Master]"},
            "phase_select": {"off": 3, "gain": 1, "unit": "--", "desc": "相位选择 [A/B/C Phase]"},
            # 16168
            # "three_phase_parallel_enable": {"off": 4, "gain": 1, "unit": "--", "desc": "三相并机使能 [0:Disable 1:Enable]"},
            # # 16169
            # "a_phase_enable": {"off": 5, "gain": 1, "unit": "--", "desc": "A相位使能 [0:Disable 1:Enable]"},
            # # 16170
            # "b_phase_enable": {"off": 6, "gain": 1, "unit": "--", "desc": "B相位使能 [0:Disable 1:Enable]"},
            # # 16171
            # "c_phase_enable": {"off": 7, "gain": 1, "unit": "--", "desc": "C相位使能 [0:Disable 1:Enable]"},
        }
    },
    # 电网设置参数 (功能码 10H)
    "grid setting parameters": {
        "start_addr": 26001,
        "registers": {
            "grid_mode": {"off": 0, "gain": 1, "unit": "--", "desc": "Grid Mode 并网标准"},
            "grid_type": {"off": 1, "gain": 1, "unit": "--", "desc": "Grid Type 电网类型 [0:单相240/230/220V 1:单相120/240V 2:三相380/400V]"},
            "off_grid_voltage": {"off": 2, "gain": 1, "unit": "--", "desc": "Off-grid Voltage 离网模式电压档位 [0-4]"},
            # "grid_frequency": {"off": 3, "gain": 1, "unit": "--", "desc": "Grid Frequency 电网频率 [0:50Hz 1:60Hz]"},
            # "restore_connection_time": {"off": 4, "gain": 1, "unit": "s", "desc": "Restore connection time 恢复并网时间 [10-300]"},
            # "over_frequency_protection": {"off": 5, "gain": 0.01, "unit": "Hz", "desc": "Grid Frequency Over 电网过频保护点"},
            # "under_frequency_protection": {"off": 6, "gain": 0.01, "unit": "Hz", "desc": "Grid Frequency Under 电网欠频保护点"},
            # "over_voltage_protection": {"off": 7, "gain": 0.1, "unit": "V", "desc": "Grid Voltage Over 电网过压保护点"},
            # "under_voltage_protection": {"off": 8, "gain": 0.1, "unit": "V", "desc": "Grid Voltage Under 电网欠压保护点"},
        }
    },
    # 电池设置参数 (功能码 10H)
    "battery setting parameters": {
        "start_addr": 36001,
        "registers": {
            "battery_type": {"off": 0, "gain": 1, "unit": "--", "desc": "Battery_Type 电池类型"},
            "battery_mode": {"off": 1, "gain": 1, "unit": "--", "desc": "Battery_Mode 电池模式"},
            "activate_battery": {"off": 2, "gain": 1, "unit": "--", "desc": "Activate Battery 电池使能"},
            "battery_capacity": {"off": 3, "gain": 1, "unit": "Ah", "desc": "Batt Capacity 电池容量"},
            "max_a_charge": {"off": 4, "gain": 1, "unit": "A", "desc": "Max A Charge 最大充电电流"},
            "max_a_discharge": {"off": 5, "gain": 1, "unit": "A", "desc": "Max A discharge 最大放电电流"},
            "gen_charge": {"off": 6, "gain": 1, "unit": "--", "desc": "Gen Charge 发电机充电使能"},
            "gen_signal": {"off": 7, "gain": 1, "unit": "--", "desc": "Gen Signal 发电机信号"},
            "generator_charging_start_capacity_point": {"off": 8, "gain": 0.01, "unit": "%", "desc": "Generator charging start capacity point 发电机充电起始容量点"},
            "generator_to_battery_charging_current": {"off": 9, "gain": 0.01, "unit": "A", "desc": "Generator to battery charging current 发电机到电池充电电流"},
            "max_run_time": {"off": 10, "gain": 0.1, "unit": "h", "desc": "Max Run Time 最大运行时间"},
            "cooling_time": {"off": 11, "gain": 0.1, "unit": "h", "desc": "Cooling Time 冷却时间"},
            "grid_charge": {"off": 12, "gain": 1, "unit": "--", "desc": "Grid Charge 电网充电使能"},
            "grid_signal": {"off": 13, "gain": 1, "unit": "--", "desc": "Grid Signal 电网信号"},
            "utility_charging_start_capacity_point": {"off": 14, "gain": 1, "unit": "%", "desc": "Utility charging start capacity point 市电充电起始容量点"},
            "utility_to_battery_charging_current": {"off": 15, "gain": 1, "unit": "A", "desc": "Utility to battery charging current 市电到电池充电电流"},
            "generator_charging_start_voltage_point": {"off": 16, "gain": 0.01, "unit": "V", "desc": "Generator charging start voltage point 发电机充电起始电压点"},
            "utility_charging_start_voltage_point": {"off": 17, "gain": 0.01, "unit": "V", "desc": "Utility charging start voltage point 市电充电起始电压点"},
            "lithium_protocol": {"off": 18, "gain": 1, "unit": "--", "desc": "Lithium Protocol 锂电协议"},
            "shutdown_percent": {"off": 19, "gain": 1, "unit": "%", "desc": "ShutDown % 关机百分比"},
            "restart_percent": {"off": 20, "gain": 1, "unit": "%", "desc": "Restart % 重启百分比"},
            "low_batt_percent": {"off": 21, "gain": 1, "unit": "%", "desc": "LowBatt % 低电百分比"},
            "shutdown_voltage": {"off": 22, "gain": 1, "unit": "V", "desc": "ShutDown V 关机电压"},
            "restart_voltage": {"off": 23, "gain": 1, "unit": "V", "desc": "Restart V 重启电压"},
            "low_batt_voltage": {"off": 24, "gain": 1, "unit": "V", "desc": "LowBatt V 低电电压"},
            "float_voltage": {"off": 25, "gain": 0.01, "unit": "V", "desc": "Float Voltage 浮充电压"},
            "absorption_voltage": {"off": 26, "gain": 0.01, "unit": "V", "desc": "Absorption Voltage 吸收电压"},
            "equalization_voltage": {"off": 27, "gain": 0.01, "unit": "V", "desc": "Equalization Voltage 均衡电压"},
            "equalization_days": {"off": 28, "gain": 1, "unit": "d", "desc": "Equalization days 均衡天数"},
            "equalization_hours": {"off": 29, "gain": 1, "unit": "h", "desc": "Equalization hours 均衡小时"},
            "tempco": {"off": 30, "gain": 1, "unit": "mV/℃", "desc": "TEMPCO 温度补偿"},
            "battery_resistance_value": {"off": 31, "gain": 1, "unit": "mΩ", "desc": "Battery Resistance Value 电池内阻值"},
        }
    },
    # 保护参数设置 (功能码 10H) - 起始地址 46001
    "protection setting parameters": {
        "start_addr": 46001,
        "registers": {
            # ── 单/多级选择 ────────────────────────────────────────────
            "single_multiple_level_selection": {"off": 0, "gain": 1, "unit": "--", "desc": "单级/多级选择 [0:单级 1:二级 2:三级 3:四级 4:五级]"},

            # ── 保护恢复点 ─────────────────────────────────────────────
            "uvp_recovery":  {"off": 1, "gain": 0.1, "unit": "V",  "desc": "欠压保护恢复点 [前高后低]"},
            "ovp_recovery":  {"off": 2, "gain": 0.1, "unit": "V",  "desc": "过压保护恢复点"},
            "ufp_recovery":  {"off": 3, "gain": 0.01, "unit": "Hz", "desc": "欠频保护恢复点"},
            "ofp_recovery":  {"off": 4, "gain": 0.01, "unit": "Hz", "desc": "过频保护恢复点"},

            # ── 一级保护值（单级时同效） ────────────────────────────────
            "uvp_l1_value":  {"off": 5, "gain": 0.1,  "unit": "V",  "desc": "欠压一级保护值（单级）"},
            "ovp_l1_value":  {"off": 6, "gain": 0.1,  "unit": "V",  "desc": "过压一级保护值（单级）"},
            "ufp_l1_value":  {"off": 7, "gain": 0.01, "unit": "Hz", "desc": "欠频一级保护值（单级）"},
            "ofp_l1_value":  {"off": 8, "gain": 0.01, "unit": "Hz", "desc": "过频一级保护值（单级）"},

            # ── 一级保护时间（U32） ─────────────────────────────────────
            "uvp_l1_time":   {"off": 9,  "gain": 0.01, "unit": "s", "desc": "欠压一级保护时间"},
            "ovp_l1_time":   {"off": 11, "gain": 0.01, "unit": "s", "desc": "过压一级保护时间"},
            "ufp_l1_time":   {"off": 13, "gain": 0.01, "unit": "s", "desc": "欠频一级保护时间"},
            "ofp_l1_time":   {"off": 15, "gain": 0.01, "unit": "s", "desc": "过频一级保护时间"},

            # ── 二级保护值 ─────────────────────────────────────────────
            "uvp_l2_value":  {"off": 17, "gain": 0.1,  "unit": "V",  "desc": "欠压二级保护值"},
            "ovp_l2_value":  {"off": 18, "gain": 0.1,  "unit": "V",  "desc": "过压二级保护值"},
            "ufp_l2_value":  {"off": 19, "gain": 0.01, "unit": "Hz", "desc": "欠频二级保护值"},
            "ofp_l2_value":  {"off": 20, "gain": 0.01, "unit": "Hz", "desc": "过频二级保护值"},

            # ── 二级保护时间（U32） ─────────────────────────────────────
            "uvp_l2_time":   {"off": 21, "gain": 0.01, "unit": "s", "desc": "欠压二级保护时间"},
            "ovp_l2_time":   {"off": 23, "gain": 0.01, "unit": "s", "desc": "过压二级保护时间"},
            "ufp_l2_time":   {"off": 25, "gain": 0.01, "unit": "s", "desc": "欠频二级保护时间"},
            "ofp_l2_time":   {"off": 27, "gain": 0.01, "unit": "s", "desc": "过频二级保护时间"},

            # ── 三级保护值 ─────────────────────────────────────────────
            "uvp_l3_value":  {"off": 29, "gain": 0.1,  "unit": "V",  "desc": "欠压三级保护值"},
            "ovp_l3_value":  {"off": 30, "gain": 0.1,  "unit": "V",  "desc": "过压三级保护值"},
            "ufp_l3_value":  {"off": 31, "gain": 0.01, "unit": "Hz", "desc": "欠频三级保护值"},
            "ofp_l3_value":  {"off": 32, "gain": 0.01, "unit": "Hz", "desc": "过频三级保护值"},

            # ── 三级保护时间（U32） ─────────────────────────────────────
            "uvp_l3_time":   {"off": 33, "gain": 0.01, "unit": "s", "desc": "欠压三级保护时间"},
            "ovp_l3_time":   {"off": 35, "gain": 0.01, "unit": "s", "desc": "过压三级保护时间"},
            "ufp_l3_time":   {"off": 37, "gain": 0.01, "unit": "s", "desc": "欠频三级保护时间"},
            "ofp_l3_time":   {"off": 39, "gain": 0.01, "unit": "s", "desc": "过频三级保护时间"},

            # ── 四级保护值 ─────────────────────────────────────────────
            "uvp_l4_value":  {"off": 41, "gain": 0.1,  "unit": "V",  "desc": "欠压四级保护值"},
            "ovp_l4_value":  {"off": 42, "gain": 0.1,  "unit": "V",  "desc": "过压四级保护值"},
            "ufp_l4_value":  {"off": 43, "gain": 0.01, "unit": "Hz", "desc": "欠频四级保护值"},
            "ofp_l4_value":  {"off": 44, "gain": 0.01, "unit": "Hz", "desc": "过频四级保护值"},

            # ── 四级保护时间（U32） ─────────────────────────────────────
            "uvp_l4_time":   {"off": 45, "gain": 0.01, "unit": "s", "desc": "欠压四级保护时间"},
            "ovp_l4_time":   {"off": 47, "gain": 0.01, "unit": "s", "desc": "过压四级保护时间"},
            "ufp_l4_time":   {"off": 49, "gain": 0.01, "unit": "s", "desc": "欠频四级保护时间"},
            "ofp_l4_time":   {"off": 51, "gain": 0.01, "unit": "s", "desc": "过频四级保护时间"},

            # ── 五级保护值 ─────────────────────────────────────────────
            "uvp_l5_value":  {"off": 53, "gain": 0.1,  "unit": "V",  "desc": "欠压五级保护值"},
            "ovp_l5_value":  {"off": 54, "gain": 0.1,  "unit": "V",  "desc": "过压五级保护值"},
            "ufp_l5_value":  {"off": 55, "gain": 0.01, "unit": "Hz", "desc": "欠频五级保护值"},
            "ofp_l5_value":  {"off": 56, "gain": 0.01, "unit": "Hz", "desc": "过频五级保护值"},

            # ── 五级保护时间（U32） ─────────────────────────────────────
            "uvp_l5_time":   {"off": 57, "gain": 0.01, "unit": "s", "desc": "欠压五级保护时间"},
            "ovp_l5_time":   {"off": 59, "gain": 0.01, "unit": "s", "desc": "过压五级保护时间"},
            "ufp_l5_time":   {"off": 61, "gain": 0.01, "unit": "s", "desc": "欠频五级保护时间"},
            "ofp_l5_time":   {"off": 63, "gain": 0.01, "unit": "s", "desc": "过频五级保护时间"},

            # ── 10 分钟过压保护 ─────────────────────────────────────────
            "ovp_10min_enable":   {"off": 65, "gain": 1,   "unit": "--", "desc": "10分钟过压保护使能 [0:Disable 1:Enable]"},
            "ovp_10min_value":    {"off": 66, "gain": 0.1, "unit": "V",  "desc": "10分钟过压保护值"},
            "ovp_10min_recovery": {"off": 67, "gain": 0.1, "unit": "V",  "desc": "10分钟过压保护恢复值"},
        }
    },
}

# CAN 总线配置（用于 BMS 详细数据采集）
CAN_CONFIG = {
    "channel": "canb0",
    "bustype": "socketcan",
    "bitrate": 500000,
}

# BMS CAN 协议配置
BMS_PROTOCOL_CONFIG = {
    # BMS CAN ID 配置（29位扩展帧）
    "tx_can_id": None,
    "rx_can_ids": [
        0x1820C0B0,  # 总体信息
        0x1821C0B0,  # 单体电压1-4
        0x1822C0B0,  # 单体电压5-8
        0x1823C0B0,  # 单体电压9-12
        0x1824C0B0,  # 单体电压13-16
        0x1825C0B0,  # 最高最低序号与电流
        0x1826C0B0,  # 总体状态信息
        0x1827C0B0,  # 温度1-4
        0x1828C0B0,  # 报警信息
    ],
    # 字节序：小端（Intel）
    "byte_order": "little",

    # BMS_Overall_Info 总体信息 0x1820C0B0
    "BMS_Overall_Info": {
        "can_id": 0x1820C0B0,
        "data_length": 8,
        "cycle": 100,
        "map": {
            "cell_total_count": {"byte": 0, "length": 1, "resolution": 1, "offset": 0},
            "total_voltage":    {"byte": 1, "length": 2, "resolution": 10, "offset": 0},  # mV
            "cell_voltage_max": {"byte": 3, "length": 2, "resolution": 1, "offset": 0},   # mV
            "cell_voltage_min": {"byte": 5, "length": 2, "resolution": 1, "offset": 0},   # mV
            "reserved":         {"byte": 7, "length": 1},
        }
    },

    # BMS_SingleVoltage1_4 1-4号单体电压 0x1821C0B0
    "BMS_SingleVoltage1_4": {
        "can_id": 0x1821C0B0,
        "data_length": 8,
        "cycle": 100,
        "map": {
            "cell_voltage_1": {"byte": 0, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_2": {"byte": 2, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_3": {"byte": 4, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_4": {"byte": 6, "length": 2, "resolution": 1, "offset": 0},
        }
    },

    # BMS_SingleVoltage5_8 5-8号单体电压 0x1822C0B0
    "BMS_SingleVoltage5_8": {
        "can_id": 0x1822C0B0,
        "data_length": 8,
        "cycle": 100,
        "map": {
            "cell_voltage_5": {"byte": 0, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_6": {"byte": 2, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_7": {"byte": 4, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_8": {"byte": 6, "length": 2, "resolution": 1, "offset": 0},
        }
    },

    # BMS_SingleVoltage9_12 9-12号单体电压 0x1823C0B0
    "BMS_SingleVoltage9_12": {
        "can_id": 0x1823C0B0,
        "data_length": 8,
        "cycle": 100,
        "map": {
            "cell_voltage_9":  {"byte": 0, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_10": {"byte": 2, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_11": {"byte": 4, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_12": {"byte": 6, "length": 2, "resolution": 1, "offset": 0},
        }
    },

    # BMS_SingleVoltage13_16 13-16号单体电压 0x1824C0B0
    "BMS_SingleVoltage13_16": {
        "can_id": 0x1824C0B0,
        "data_length": 8,
        "cycle": 100,
        "map": {
            "cell_voltage_13": {"byte": 0, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_14": {"byte": 2, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_15": {"byte": 4, "length": 2, "resolution": 1, "offset": 0},
            "cell_voltage_16": {"byte": 6, "length": 2, "resolution": 1, "offset": 0},
        }
    },

    # BMS_MaxMinIndexAndCurrent 最高最低序号与电流 0x1825C0B0
    "BMS_MaxMinIndexAndCurrent": {
        "can_id": 0x1825C0B0,
        "data_length": 8,
        "cycle": 100,
        "map": {
            "cell_max_index": {"byte": 0, "length": 1, "resolution": 1,    "offset": 0},
            "cell_min_index": {"byte": 1, "length": 1, "resolution": 1,    "offset": 0},
            "total_current":  {"byte": 2, "length": 2, "resolution": 0.01, "offset": -200},  # A
            "reserved":       {"byte": 4, "length": 4},
        }
    },

    # BMS_OverallState 总体状态 0x1826C0B0
    "BMS_OverallState": {
        "can_id": 0x1826C0B0,
        "data_length": 8,
        "cycle": 500,
        "map": {
            "SOH":              {"byte": 0, "length": 1, "resolution": 0.4, "offset": 0},
            "SOC":              {"byte": 1, "length": 1, "resolution": 0.4, "offset": 0},
            "SOP":              {"byte": 2, "length": 2, "resolution": 1,   "offset": 0},
            "SOE":              {"byte": 4, "length": 1, "resolution": 0.4, "offset": 0},
            "SOT":              {"byte": 5, "length": 1, "resolution": 1,   "offset": 0},  # ℃
            "battery_capacity": {"byte": 6, "length": 1, "resolution": 1,   "offset": 0},
            "reserved":         {"byte": 7, "length": 1},
        }
    },

    # BMS_SingleTemp1_4 温度1-4 0x1827C0B0
    "BMS_SingleTemp1_4": {
        "can_id": 0x1827C0B0,
        "data_length": 8,
        "cycle": 500,
        "map": {
            "temp_1": {"byte": 0, "length": 2, "resolution": 1, "offset": 0},  # ℃
            "temp_2": {"byte": 2, "length": 2, "resolution": 1, "offset": 0},
            "temp_3": {"byte": 4, "length": 2, "resolution": 1, "offset": 0},
            "temp_4": {"byte": 6, "length": 2, "resolution": 1, "offset": 0},
        }
    },

    # BMS_AlarmInfo 报警信息 0x1828C0B0
    "BMS_AlarmInfo": {
        "can_id": 0x1828C0B0,
        "data_length": 8,
        "cycle": 500,
        "map": {
            # BYTE0 按位定义
            "alarm_cell_under_1":  {"byte": 0, "bit": 0, "length": 1},
            "alarm_cell_under_2":  {"byte": 0, "bit": 1, "length": 1},
            "alarm_cell_under_3":  {"byte": 0, "bit": 2, "length": 1},
            "alarm_cell_over_1":   {"byte": 0, "bit": 3, "length": 1},
            "alarm_cell_over_2":   {"byte": 0, "bit": 4, "length": 1},
            "alarm_cell_over_3":   {"byte": 0, "bit": 5, "length": 1},
            "alarm_total_under_1": {"byte": 0, "bit": 6, "length": 1},
            "alarm_total_under_2": {"byte": 0, "bit": 7, "length": 1},
            "reserved":            {"byte": 1, "length": 7},
        }
    },
}