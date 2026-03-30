# EMS 图形化监控与预测系统（PySide6）

> 出品单位：合肥工业大学 | 交通能源协同控制实验室  
> 产品定位：面向储能与能源场景的 EMS 监控与预测软件产品

---

## 1. 项目简介
本项目是一个基于 **PySide6** 的 EMS（Energy Management System）桌面应用，面向储能/能源场景，提供以下能力：

- EMS 运行状态监控
- EMS 参数配置与下发
- 预测模型调用与结果展示
- 统一日志管理与故障排查支持

本产品由**合肥工业大学 | 交通能源协同控制实验室**研发，适用于本地联调、现场调试与演示环境。

---

## 2. 功能概览

### 2.1 登录与主界面
- 登录窗口入口
- 主窗口多 Tab 页面切换
- 统一状态栏与提示信息

### 2.2 EMS 监控（EMS Monitor）
- 展示 EMS 实时关键数据
- 支持定时刷新与状态展示

### 2.3 EMS 参数（EMS Param）
- 参数读取、编辑、下发
- 配置项状态反馈与异常提示

### 2.4 预测模块（Prediction）
- 调用 `model_prediction` 下模型进行预测
- 展示预测结果和历史数据文件（CSV）

### 2.5 About
- 项目说明/版本信息展示

---

## 3. 项目结构

```text
.
├─ config.py                    # 全局配置（日志/CAN/协议映射）
├─ driver.py                    # 设备驱动与数据交互逻辑
├─ logger.py                    # 日志初始化与封装
├─ main.py                      # 程序主入口
├─ README.md
├─ logs/                        # 日志输出目录
│  └─ system_run.log
├─ model_prediction/            # 预测模型与数据
│  ├─ data1.xlsx
│  ├─ predict_v3.py
│  ├─ predictions_v3.csv
│  └─ solar_power_lstm_model_v3.pth
└─ ui/
   ├─ __init__.py
   ├─ login_window.py           # 登录窗口
   ├─ main_window.py            # 主窗口与槽函数组织核心
   └─ widgets/                  # 各 Tab 页面组件
      ├─ __init__.py
      ├─ about_tab.py
      ├─ ems_monitor_tab.py
      ├─ ems_param_tab.py
      └─ prediction_tab.py
```

---

## 4. 环境要求

- Python 3.9+（建议 3.10）
- Windows（当前开发环境）
- 依赖库（至少包含）：
  - PySide6
  - torch（用于预测模型推理）
  - pandas（用于 CSV 处理）
  - 以及项目实际使用到的通信/工具依赖

---

## 5. 安装与运行

### 5.1 安装依赖
若你已有 `requirements.txt`，推荐：

```bash
pip install -r requirements.txt
```

如果暂未维护依赖文件，可按实际缺失逐步安装：

```bash
pip install PySide6 torch pandas
```

### 5.2 启动项目

```bash
python main.py
```

当前版本统一由 `main.py` 作为启动入口。

---

## 6. 配置说明（config.py）

- `LOG_CONFIG`：日志级别、格式、输出目录、文件大小与轮转数量等配置
- `CAN_CONFIG`：CAN 通讯通道、总线类型、波特率、超时等配置
- `EMS_PROTOCOL_CONFIG`：EMS 协议映射、请求参数与字段解析配置

> 建议：配置变更前先备份，避免现场参数误改导致连接失败。

---

## 7. 日志与排障

### 7.1 日志位置
- 默认输出目录：`logs/`

### 7.2 常见问题
1. 启动报模块缺失  
   - 执行依赖安装，确认 Python 环境与解释器一致。

2. 预测失败/模型加载失败  
   - 检查 `model_prediction/solar_power_lstm_model_v3.pth` 是否存在；
   - 确认 `torch` 版本与模型兼容。

3. 通讯无数据  
   - 检查 CAN 配置、设备连接状态、协议映射配置是否一致。

4. UI 有值但不刷新  
   - 检查主窗口定时器、槽函数连接是否有效；
   - 关注 `ui/main_window.py` 中对应 tab 的信号绑定逻辑。

---

## 8. 开发约定

- UI 相关逻辑集中在 `ui/`
- 设备驱动与数据交互逻辑集中在 `driver.py`
- 配置与协议映射集中在 `config.py`
- 建议将 `main_window.py` 的槽函数按 Tab 分组管理，降低维护成本

---

## 9. 后续优化建议

1. 增加 `requirements.txt` / `pyproject.toml`，统一依赖管理  
2. 为核心服务与预测流程补充单元测试  
3. 将主窗口槽函数进一步模块化（按 Tab 拆分 mixin/controller）  
4. 增加异常场景下的用户可视化提示与重试机制  

---

## 10. 免责声明
本项目用于开发调试与业务验证，部署到生产环境前请进行充分测试与安全评估。

---

## 11. 出品与版权声明
- 出品单位：合肥工业大学 | 交通能源协同控制实验室
- 产品名称：EMS 图形化监控与预测系统（PySide6）
- 本仓库中的代码、文档与界面方案用于教学科研及项目开发，请在授权范围内使用。
