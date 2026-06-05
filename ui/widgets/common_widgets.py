"""Shared UI widgets used across multiple tabs."""

from __future__ import annotations

import os
import multiprocessing as mp
from queue import Empty
import time

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def wrap_scroll_area(
    inner: QWidget,
    spacing: int = 8,
    margins: tuple[int, int, int, int] = (6, 6, 6, 6),
) -> QScrollArea:
    """Wrap a widget inside a scroll area."""
    del spacing, margins
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    scroll.setWidget(inner)
    return scroll


def create_card(parent, title, obj_name, color, font_size=14):
    """Create a metric card and expose its value label on the parent."""
    group = QGroupBox(title)
    layout = QVBoxLayout(group)
    layout.setContentsMargins(8, 8, 8, 8)

    label = QLabel("--")
    label.setObjectName(obj_name)
    label.setStyleSheet(f"font: {font_size}pt 'Microsoft YaHei UI'; color: {color}; font-weight: bold;")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    layout.addWidget(label)
    setattr(parent, obj_name, label)
    return group


def create_status_item(parent, name, obj_name, color, font_size=11):
    """Create a compact status widget and expose its value label on the parent."""
    widget = QFrame()
    widget.setStyleSheet("background-color: #F5F7FA; border-radius: 4px;")

    v_layout = QVBoxLayout(widget)
    v_layout.setContentsMargins(4, 4, 4, 4)
    v_layout.setSpacing(2)

    lbl_name = QLabel(name)
    lbl_name.setStyleSheet("color: #666666; font-size: 8pt;")
    lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)

    lbl_val = QLabel("--")
    lbl_val.setObjectName(obj_name)
    lbl_val.setStyleSheet(f"font: {font_size}pt 'Microsoft YaHei UI'; color: {color}; font-weight: bold;")
    lbl_val.setAlignment(Qt.AlignmentFlag.AlignCenter)

    v_layout.addWidget(lbl_name)
    v_layout.addWidget(lbl_val)
    setattr(parent, obj_name, lbl_val)
    return widget


def add_combo(parent, grid_layout, row, col, label_text, attr_name, items, item_data=None):
    """Add a combo box to a form-style grid."""
    widget = QComboBox()
    if item_data is None:
        widget.addItems(items)
    else:
        if len(item_data) != len(items):
            raise ValueError("items and item_data must have the same length")
        for text, data in zip(items, item_data):
            widget.addItem(text, data)
    setattr(parent, attr_name, widget)
    label = QLabel(label_text)
    label.setMinimumWidth(120)
    grid_layout.addWidget(label, row, col * 2)
    grid_layout.addWidget(widget, row, col * 2 + 1)
    return widget


def add_spin(parent, grid_layout, row, col, label_text, attr_name, minimum, maximum, decimals=0, suffix=""):
    """Add a spin box to a form-style grid."""
    widget = QDoubleSpinBox() if decimals else QSpinBox()
    widget.setRange(minimum, maximum)
    if decimals:
        widget.setDecimals(decimals)
    if suffix:
        widget.setSuffix(suffix)
    setattr(parent, attr_name, widget)
    label = QLabel(label_text)
    label.setMinimumWidth(120)
    grid_layout.addWidget(label, row, col * 2)
    grid_layout.addWidget(widget, row, col * 2 + 1)
    return widget


class _PlotImageLoader(QObject):
    """Load and scale plot images in a background thread."""

    images_ready = Signal(int, object)

    @Slot(int, object)
    def load_images(self, request_id: int, jobs):
        results = []
        for job in jobs or []:
            payload = {
                "index": int(job.get("index", -1)),
                "image": None,
                "text": str(job.get("placeholder", "")),
            }
            path = str(job.get("path", "") or "")
            if not path:
                results.append(payload)
                continue
            if not os.path.exists(path):
                payload["text"] = "图像未生成"
                results.append(payload)
                continue

            try:
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                image = reader.read()
                if image.isNull():
                    payload["text"] = "图像加载失败"
                else:
                    image = image.scaled(
                        QSize(max(1, int(job.get("width", 1))), max(1, int(job.get("height", 1)))),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    payload["image"] = image
                    payload["text"] = ""
            except Exception:
                payload["text"] = "图像加载失败"

            results.append(payload)

        try:
            self.images_ready.emit(request_id, results)
        except RuntimeError:
            # The owning gallery can disappear while an image decode is finishing.
            pass


class ProcessSimulationWorkerBase(QObject):
    """Bridge child-process messages back into Qt signals on the GUI thread."""

    progress = Signal(str)
    rolling_updated = Signal(dict)
    finished = Signal(dict)
    failed = Signal(str)
    terminated = Signal()

    _THREAD_ENV_NAMES = (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    )

    def __init__(self, process_target, payload: dict):
        super().__init__()
        self._process_target = process_target
        self._payload = dict(payload)
        self._process = None
        self._queue = None
        self._stop_event = None
        self._pause_event = None
        self._terminal_received = False
        self._stop_deadline = None
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll_messages)

    def start(self):
        if self._process is not None:
            return
        context = mp.get_context("spawn")
        self._queue = context.Queue()
        self._stop_event = context.Event()
        self._pause_event = context.Event()
        self._process = context.Process(
            target=self._process_target,
            args=(self._queue, self._stop_event, self._pause_event, self._payload),
            name=f"simulation-{self._process_target.__name__}",
            daemon=True,
        )

        previous = {name: os.environ.get(name) for name in self._THREAD_ENV_NAMES}
        try:
            for name in self._THREAD_ENV_NAMES:
                os.environ[name] = "1"
            self._process.start()
        except Exception as exc:
            self._cleanup_process()
            self.failed.emit(str(exc))
            self.terminated.emit()
            return
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self._timer.start()

    def request_stop(self):
        if self._stop_event is not None:
            self._stop_event.set()
            self._stop_deadline = time.monotonic() + 3.0
        if self._pause_event is not None:
            self._pause_event.clear()

    def request_pause(self):
        if self._pause_event is not None:
            self._pause_event.set()

    def request_resume(self):
        if self._pause_event is not None:
            self._pause_event.clear()

    def is_paused(self) -> bool:
        return bool(self._pause_event is not None and self._pause_event.is_set())

    def shutdown(self):
        """Stop immediately while a page or the application is closing."""
        self._timer.stop()
        if self._stop_event is not None:
            self._stop_event.set()
        if self._pause_event is not None:
            self._pause_event.clear()
        process = self._process
        if process is not None:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
        self._cleanup_process()

    def _poll_messages(self):
        process = self._process
        if process is None:
            return

        while self._queue is not None:
            try:
                kind, payload = self._queue.get_nowait()
            except Empty:
                break
            if kind == "progress":
                self.progress.emit(str(payload))
            elif kind == "rolling":
                self.rolling_updated.emit(dict(payload or {}))
            elif kind == "finished":
                self._terminal_received = True
                self.finished.emit(dict(payload or {}))
            elif kind == "failed":
                self._terminal_received = True
                self.failed.emit(str(payload))

        if self._terminal_received:
            self._finish_process()
            return
        if self._stop_deadline is not None and time.monotonic() >= self._stop_deadline and process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
            self._terminal_received = True
            self.finished.emit({"stopped": True})
            self._finish_process()
            return
        if not process.is_alive():
            exitcode = process.exitcode
            self.failed.emit(f"Simulation subprocess exited unexpectedly (exit code {exitcode}).")
            self._finish_process()

    def _finish_process(self):
        process = self._process
        if process is not None:
            process.join(timeout=0.5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
        self._cleanup_process()
        self.terminated.emit()

    def _cleanup_process(self):
        self._timer.stop()
        if self._queue is not None:
            self._queue.close()
            self._queue = None
        if self._process is not None:
            try:
                self._process.close()
            except ValueError:
                # close() is rejected only while a process is still active.
                pass
        self._process = None
        self._stop_event = None
        self._pause_event = None
        self._stop_deadline = None


def start_simulation_process(
    owner,
    worker,
    *,
    on_finished,
    on_failed,
    on_thread_finished,
    on_progress=None,
    extra_signals=(),
):
    """Start a subprocess worker while preserving the pages' running handle."""
    owner.thread = worker
    owner.worker = worker
    if on_progress is not None:
        worker.progress.connect(on_progress)
    for signal, callback in extra_signals:
        signal.connect(callback)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    worker.terminated.connect(on_thread_finished)
    worker.terminated.connect(worker.deleteLater)
    worker.start()
    return worker


class PlotImageGallery(QWidget):
    """Display a fixed set of plot placeholders and refresh them asynchronously."""

    request_images = Signal(int, object)

    def __init__(
        self,
        titles: list[str],
        placeholder: str,
        parent=None,
        image_size: QSize | None = None,
        min_image_height: int = 250,
        columns: int = 2,
    ):
        super().__init__(parent)
        self.titles = list(titles)
        self.placeholder = placeholder
        self.image_size = image_size
        self.min_image_height = int(min_image_height)
        self.columns = max(1, int(columns))
        self.plot_paths: list[str] = []
        self.image_labels: list[QLabel] = []
        self._request_serial = 0
        self._loaded_signature = None
        self._pending_signature = None
        self._request_signatures: dict[int, tuple] = {}

        self._loader_thread = QThread(self)
        self._loader_worker = _PlotImageLoader()
        self._loader_worker.moveToThread(self._loader_thread)
        self.request_images.connect(self._loader_worker.load_images)
        self._loader_worker.images_ready.connect(self._apply_loaded_images)
        self._loader_thread.finished.connect(self._loader_worker.deleteLater)
        self._loader_thread.start()
        self.destroyed.connect(lambda *_args: self._shutdown_loader())

        layout = QGridLayout(self)
        layout.setSpacing(12)
        for index, title in enumerate(self.titles):
            group = QGroupBox(title)
            group_layout = QVBoxLayout(group)
            label = QLabel(self.placeholder)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setScaledContents(False)
            label.setStyleSheet("background: #FFFFFF; color: #909399; border: 1px solid #D7DCE3;")
            if self.image_size is not None:
                label.setFixedSize(self.image_size)
            else:
                label.setMinimumHeight(self.min_image_height)
            group_layout.addWidget(label)
            self.image_labels.append(label)
            layout.addWidget(group, index // self.columns, index % self.columns)

    def set_plot_paths(self, plot_paths):
        self.plot_paths = list(plot_paths or [])
        self._request_async_refresh()

    def clear(self):
        self.plot_paths = []
        self._request_serial += 1
        self._loaded_signature = None
        self._pending_signature = None
        self._request_signatures.clear()
        for label in self.image_labels:
            label.setPixmap(QPixmap())
            label.setText(self.placeholder)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.plot_paths:
            self._request_async_refresh()

    def _request_async_refresh(self):
        jobs = []
        signature = []
        for index, label in enumerate(self.image_labels):
            path = self.plot_paths[index] if index < len(self.plot_paths) else ""
            target_size = label.size() if self.image_size is None else self.image_size
            width = max(1, target_size.width())
            height = max(1, target_size.height())
            mtime_ns = None
            if path and os.path.exists(path):
                try:
                    mtime_ns = os.stat(path).st_mtime_ns
                except OSError:
                    mtime_ns = None
            jobs.append(
                {
                    "index": index,
                    "path": path,
                    "width": width,
                    "height": height,
                    "placeholder": self.placeholder,
                }
            )
            signature.append((path, mtime_ns, width, height))

        signature_key = tuple(signature)
        if signature_key == self._loaded_signature or signature_key == self._pending_signature:
            return

        self._request_serial += 1
        self._pending_signature = signature_key
        self._request_signatures[self._request_serial] = signature_key
        self.request_images.emit(self._request_serial, jobs)

    def _apply_loaded_images(self, request_id: int, payloads):
        signature = self._request_signatures.pop(request_id, None)
        if request_id != self._request_serial:
            return

        self._loaded_signature = signature
        self._pending_signature = None
        for payload in payloads or []:
            index = int(payload.get("index", -1))
            if index < 0 or index >= len(self.image_labels):
                continue

            label = self.image_labels[index]
            image = payload.get("image")
            if image is not None:
                label.setText("")
                label.setPixmap(QPixmap.fromImage(image))
                continue

            current = label.pixmap()
            if current is not None and not current.isNull():
                continue
            label.setPixmap(QPixmap())
            label.setText(str(payload.get("text", self.placeholder)))

    def _shutdown_loader(self):
        if self._loader_thread.isRunning():
            self._loader_thread.quit()
            self._loader_thread.wait(1000)


class CollapsibleSection(QWidget):
    """Simple expandable/collapsible section wrapper."""

    def __init__(self, title, content, note="", expanded=False, parent=None):
        super().__init__(parent)
        self.content = content

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.toggle_btn = QToolButton()
        self.toggle_btn.setText(title)
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(expanded)
        self.toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle_btn.clicked.connect(self._toggle)
        layout.addWidget(self.toggle_btn)

        if note:
            note_label = QLabel(note)
            note_label.setStyleSheet("color: #909399;")
            layout.addWidget(note_label)

        self.content.setVisible(expanded)
        layout.addWidget(self.content)

    def _toggle(self):
        expanded = self.toggle_btn.isChecked()
        self.toggle_btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)
