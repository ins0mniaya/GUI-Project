"""Child-process entry points used by the simulation GUI.

This module deliberately imports model implementations inside each entry point.
That keeps expensive numerical imports out of a freshly spawned child until its
thread limits have been configured.
"""

from __future__ import annotations

import os
import time
import traceback
from pathlib import Path


_NUMERIC_THREAD_ENV = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)


def limit_numeric_threads() -> None:
    """Restrict a model child process to one numerical-library worker thread."""
    for name in _NUMERIC_THREAD_ENV:
        os.environ[name] = "1"

    try:
        import torch

        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    except (ImportError, RuntimeError):
        pass


def _progress(message_queue, text: str) -> None:
    message_queue.put(("progress", str(text)))


def _rolling(message_queue, snapshot: dict) -> None:
    message_queue.put(("rolling", dict(snapshot or {})))


def _finish(message_queue, result: dict) -> None:
    message_queue.put(("finished", dict(result or {})))


def _fail(message_queue) -> None:
    message_queue.put(("failed", traceback.format_exc()))


def run_pv_process(message_queue, stop_event, pause_event, payload: dict) -> None:
    """Run continuously updated PV simulation cycles in one child process."""
    limit_numeric_threads()
    try:
        from packages.pv_mppt.gui_runner import run_gui_pv_simulation

        runtime_params = dict(payload["runtime_params"])
        rolling_start_offset = int(runtime_params.get("rolling_start_offset", 0))
        rolling_cycle = 0
        last_result: dict = {}
        _progress(message_queue, "PV simulation process started.")

        while not stop_event.is_set():
            cycle_number = rolling_cycle + 1

            def emit_rolling_update(snapshot, cycle=cycle_number):
                update = dict(snapshot or {})
                update["rolling_cycle"] = cycle
                _rolling(message_queue, update)

            result = run_gui_pv_simulation(
                data_path=payload["data_path"],
                output_csv=payload["output_path"],
                max_weather_steps=1,
                forecast_days=0.0,
                controller_dt=runtime_params["controller_dt"],
                pv_params=payload["pv_params"],
                dc_bus_voltage=runtime_params["dc_bus_voltage"],
                mppt_enable_irradiance=runtime_params["mppt_enable_irradiance"],
                progress_callback=emit_rolling_update,
                plot_update_interval_seconds=runtime_params.get("plot_update_interval_seconds", 1.0),
                pause_callback=pause_event.is_set,
                stop_callback=stop_event.is_set,
                rolling_start_offset=rolling_start_offset,
                real_time_playback=runtime_params.get("real_time_playback", True),
                playback_speed=runtime_params.get("playback_speed", 1.0),
            )
            last_result = result
            step_count = max(1, int(result.get("weather_points", 1) or 1))
            rolling_cycle += step_count
            rolling_start_offset += step_count

        last_result["stopped"] = True
        last_result["rolling_cycles"] = rolling_cycle
        _finish(message_queue, last_result)
    except Exception:
        _fail(message_queue)


def run_wind_process(message_queue, stop_event, pause_event, payload: dict) -> None:
    """Run continuously updated wind simulation cycles in one child process."""
    limit_numeric_threads()
    try:
        from packages.wind_mppt.gui_runner import run_gui_wind_simulation

        runtime_params = dict(payload["runtime_params"])
        rolling_start_offset = int(runtime_params.get("rolling_start_offset", 0))
        rolling_cycle = 0
        last_result: dict = {}
        _progress(message_queue, "Wind simulation process started.")

        while not stop_event.is_set():
            cycle_number = rolling_cycle + 1

            def emit_rolling_update(snapshot, cycle=cycle_number):
                update = dict(snapshot or {})
                update["rolling_cycle"] = cycle
                _rolling(message_queue, update)

            result = run_gui_wind_simulation(
                data_path=payload["data_path"],
                max_weather_steps=1,
                forecast_days=0.0,
                controller_dt=runtime_params["controller_dt"],
                dc_bus_voltage=runtime_params["dc_bus_voltage"],
                wind_params=payload["wind_params"],
                progress_callback=emit_rolling_update,
                plot_update_interval_seconds=runtime_params.get("plot_update_interval_seconds", 1.0),
                rolling_start_offset=rolling_start_offset,
                pause_callback=pause_event.is_set,
                stop_callback=stop_event.is_set,
                real_time_playback=runtime_params.get("real_time_playback", True),
                playback_speed=runtime_params.get("playback_speed", 1.0),
            )
            last_result = result
            step_count = max(1, int(result.get("weather_points", 1) or 1))
            rolling_cycle += step_count
            rolling_start_offset += step_count

        last_result["stopped"] = True
        last_result["rolling_cycles"] = rolling_cycle
        _finish(message_queue, last_result)
    except Exception:
        _fail(message_queue)


def run_flywheel_process(message_queue, stop_event, pause_event, payload: dict) -> None:
    """Run one flywheel simulation in a dedicated child process."""
    limit_numeric_threads()
    try:
        from packages.flywheel.gui_runner import run_gui_flywheel_simulation

        runtime_params = dict(payload["runtime_params"])
        _progress(message_queue, "Flywheel simulation process started.")
        result = run_gui_flywheel_simulation(
            scenario=runtime_params["scenario"],
            duration=runtime_params["duration"],
            controller_dt=runtime_params["controller_dt"],
            response_tau_s=runtime_params["response_tau_s"],
            flywheel_params=payload["flywheel_params"],
            progress_callback=lambda snapshot: _rolling(message_queue, snapshot),
            plot_update_interval_seconds=1.0,
            pause_callback=pause_event.is_set,
            stop_callback=stop_event.is_set,
            real_time_playback=True,
            playback_speed=1.0,
        )
        _finish(message_queue, result)
    except Exception:
        _fail(message_queue)


def run_load_process(message_queue, stop_event, pause_event, payload: dict) -> None:
    """Run continuously updated load prediction cycles in one child process."""
    limit_numeric_threads()
    try:
        from packages.load_forecast.gui_runner import (
            DEFAULT_INPUT_LEN,
            LONG_FORECAST_HOURS,
            LONG_INPUT_LEN,
            LONG_FORECAST_MODE,
            SHORT_FORECAST_HOURS,
            SHORT_FORECAST_MODE,
            run_gui_load_prediction,
            write_combined_prediction_plot,
        )

        runtime_params = dict(payload["runtime_params"])
        rolling_enabled = bool(runtime_params.get("rolling_enabled", True))
        rolling_start_offset = int(runtime_params.get("rolling_start_offset", runtime_params.get("max_windows", 0)))
        plot_interval = max(float(runtime_params.get("plot_update_interval_seconds", 1.0)), 0.0)
        rolling_cycle = 0
        last_result: dict = {}
        prediction_modes = runtime_params.get("prediction_modes")
        if not prediction_modes:
            prediction_modes = [runtime_params["prediction_mode"]] if runtime_params.get("prediction_mode") else [
                SHORT_FORECAST_MODE,
                LONG_FORECAST_MODE,
            ]
        aligned_dual_horizon = SHORT_FORECAST_MODE in prediction_modes and LONG_FORECAST_MODE in prediction_modes

        def start_offset_for_mode(mode: str, base_offset: int) -> int:
            if aligned_dual_horizon and mode == SHORT_FORECAST_MODE:
                return max(0, int(base_offset) + LONG_INPUT_LEN - DEFAULT_INPUT_LEN)
            return int(base_offset)

        _progress(message_queue, "Load prediction process started.")

        def output_for_mode(mode: str) -> str:
            base = Path(payload["output_path"])
            suffix = "short" if mode == SHORT_FORECAST_MODE else "long"
            return str(base.with_name(f"{base.stem}_{suffix}{base.suffix}"))

        def combined_plot_path() -> str:
            base = Path(payload["output_path"])
            return str(base.with_name(f"{base.stem}_combined.png"))

        while not stop_event.is_set():
            while pause_event.is_set() and not stop_event.is_set():
                time.sleep(0.05)
            if stop_event.is_set():
                break

            mode_results: dict = {}
            for mode in prediction_modes:
                mode = str(mode)
                result = run_gui_load_prediction(
                    data_path=payload["data_path"],
                    model_path=payload["model_path"],
                    output_csv=output_for_mode(mode),
                    predict_every_hours=SHORT_FORECAST_HOURS if mode == SHORT_FORECAST_MODE else LONG_FORECAST_HOURS,
                    prediction_mode=mode,
                    max_windows=start_offset_for_mode(mode, rolling_start_offset),
                    progress_callback=None,
                )
                mode_results[mode] = result

            result = dict(mode_results.get(LONG_FORECAST_MODE) or next(iter(mode_results.values())))
            result["mode_results"] = mode_results
            result["plot_paths"] = [
                mode_results[mode]["plot_paths"][0]
                for mode in (SHORT_FORECAST_MODE, LONG_FORECAST_MODE)
                if mode in mode_results and mode_results[mode].get("plot_paths")
            ]
            result["component_plot_paths"] = list(result["plot_paths"])
            if result["plot_paths"]:
                result["plot_paths"] = [write_combined_prediction_plot(mode_results, combined_plot_path())]
            result["output_csv"] = "; ".join(
                mode_results[mode]["output_csv"]
                for mode in (SHORT_FORECAST_MODE, LONG_FORECAST_MODE)
                if mode in mode_results and mode_results[mode].get("output_csv")
            )
            if SHORT_FORECAST_MODE in mode_results and LONG_FORECAST_MODE in mode_results:
                result["forecast_type"] = "短期+长期预测"
                result["algorithm"] = "短期4h多输出GRU / 长期Seq2Seq Attention LSTM"
                result["forecast_horizon"] = LONG_FORECAST_HOURS
                result["max_start"] = min(int(item.get("max_start", 0)) for item in mode_results.values())
            rolling_cycle += 1
            result["rolling_cycle"] = rolling_cycle
            _rolling(message_queue, result)
            last_result = result

            if not rolling_enabled or int(result.get("start_index", 0)) >= int(result.get("max_start", 1)) - 1:
                break

            rolling_start_offset = int(result.get("start_index", rolling_start_offset)) + 1
            deadline = time.monotonic() + plot_interval
            while time.monotonic() < deadline and not stop_event.is_set():
                while pause_event.is_set() and not stop_event.is_set():
                    time.sleep(0.05)
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

        last_result["stopped"] = bool(stop_event.is_set())
        last_result["rolling_cycles"] = rolling_cycle
        _finish(message_queue, last_result)
    except Exception:
        _fail(message_queue)
