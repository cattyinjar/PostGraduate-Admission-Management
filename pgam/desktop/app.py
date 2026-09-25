from __future__ import annotations

import asyncio
import queue
import sys
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from PySide6.QtCore import QLockFile, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.models import AdapterConfig, AppSettings, SourceType, TaskCreateInput, TaskUpdateInput
from ..services.services import AppService, default_data_dir
from .startup import WindowsStartupManager


class ServiceWorker(QThread):
    job_finished = Signal(dict)

    def __init__(self, service: AppService, parent=None):
        super().__init__(parent)
        self.service = service
        self.jobs: queue.Queue[tuple[int, Awaitable[Any]] | None] = queue.Queue()
        self._next_id = 1
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(self, coroutine: Awaitable[Any]) -> int:
        job_id = self._next_id
        self._next_id += 1
        self.jobs.put((job_id, coroutine))
        return job_id

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            # Keep this event loop running for the application lifetime. The scheduler
            # consists of long-lived asyncio tasks; briefly calling run_until_complete()
            # only while a GUI job arrives would leave those tasks permanently suspended.
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()
            self._loop = None

    async def _main(self) -> None:
        try:
            await self.service.start()
            while True:
                item = await asyncio.to_thread(self.jobs.get)
                if item is None:
                    break
                job_id, coroutine = item
                await self._run_job(job_id, coroutine)
        finally:
            await self.service.stop()

    async def _run_job(self, job_id: int, coroutine: Awaitable[Any]) -> None:
        try:
            result = await coroutine
            self.job_finished.emit({"id": job_id, "ok": True, "value": result})
        except Exception as exc:
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.job_finished.emit({"id": job_id, "ok": False, "value": str(exc), "detail": detail})

    def stop(self) -> None:
        self.jobs.put(None)


class ServiceBridge:
    def __init__(self, service: AppService):
        self.service = service
        self.worker = ServiceWorker(service)
        self.worker.job_finished.connect(self._dispatch)
        self.callbacks: dict[int, tuple[Callable[[Any], None] | None, Callable[[str], None] | None]] = {}
        self.worker.start()

    def call(
        self,
        coroutine_factory: Callable[[], Awaitable[Any]],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> int:
        job_id = self.worker.submit(coroutine_factory())
        self.callbacks[job_id] = (on_success, on_error)
        return job_id

    def _dispatch(self, payload: dict) -> None:
        job_id = int(payload["id"])
        callbacks = self.callbacks.pop(job_id, (None, None))
        if payload["ok"]:
            if callbacks[0]:
                callbacks[0](payload["value"])
        else:
            if callbacks[1]:
                callbacks[1](str(payload["value"]))
            else:
                QMessageBox.warning(None, "操作失败", str(payload["value"]))

    def shutdown(self) -> None:
        self.worker.stop()
        self.worker.wait(10000)


class SecretField(QWidget):
    """A masked secret editor that normally stores only a length-equal placeholder.

    The real credential is not loaded for the initial mask. Reveal and copy operations
    request it explicitly through the service bridge and only place it in the QLineEdit
    when the user asks to visualize it.
    """

    reveal_requested = Signal()
    copy_requested = Signal()

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self._saved_length = 0
        self._editing = False
        self._revealed = False

        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(False)

        self.display_button = QPushButton("\u663e\u793a", self)
        self.copy_button = QPushButton("\u590d\u5236", self)
        self.replace_button = QPushButton("\u66f4\u6362", self)
        self.display_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.display_button.clicked.connect(self._toggle_display)
        self.copy_button.clicked.connect(self.copy_requested.emit)
        self.replace_button.clicked.connect(self.begin_edit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.display_button)
        layout.addWidget(self.copy_button)
        layout.addWidget(self.replace_button)

    def set_masked_length(self, length: int) -> None:
        self._saved_length = max(0, int(length))
        self._editing = False
        self._revealed = False
        self.edit.setReadOnly(True)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setText("\u2022" * self._saved_length)
        self.edit.setPlaceholderText("\u5c1a\u672a\u4fdd\u5b58" if self._saved_length == 0 else "")
        self.display_button.setText("\u663e\u793a")
        self.display_button.setEnabled(self._saved_length > 0)
        self.copy_button.setEnabled(self._saved_length > 0)
        self.replace_button.setEnabled(True)

    def begin_edit(self) -> None:
        self._editing = True
        self._revealed = False
        self.edit.setReadOnly(False)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.clear()
        self.edit.setPlaceholderText("\u8f93\u5165\u65b0\u7684\u4fdd\u5bc6\u5185\u5bb9")
        self.display_button.setText("\u663e\u793a")
        self.display_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.replace_button.setEnabled(False)
        self.edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_secret_for_display(self, value: str) -> None:
        self._saved_length = len(value)
        self._editing = False
        self._revealed = bool(value)
        self.edit.setReadOnly(True)
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if value else QLineEdit.EchoMode.Password)
        self.edit.setText(value)
        self.edit.setPlaceholderText("" if value else "\u5c1a\u672a\u4fdd\u5b58")
        self.display_button.setText("\u9690\u85cf" if value else "\u663e\u793a")
        self.display_button.setEnabled(bool(value))
        self.copy_button.setEnabled(bool(value))
        self.replace_button.setEnabled(True)

    def hide_secret(self) -> None:
        if self._editing:
            self.edit.setEchoMode(QLineEdit.EchoMode.Password)
            return
        self.set_masked_length(self._saved_length)
        self.replace_button.setEnabled(True)

    def value_for_save(self) -> str | None:
        return self.edit.text() if self._editing else None

    def is_editing(self) -> bool:
        return self._editing

    def _toggle_display(self) -> None:
        if self._revealed:
            self.hide_secret()
        else:
            self.reveal_requested.emit()


class TaskDialog(QDialog):
    def __init__(self, parent=None, task=None, default_interval: int = 30):
        super().__init__(parent)
        self.task = task
        self.setWindowTitle("编辑监视任务" if task else "新建监视任务")
        self.setMinimumWidth(620)
        self.name = QLineEdit(task.name if task else "")
        self.url = QLineEdit(task.url if task else "https://")
        self.source_type = QComboBox()
        for item in SourceType:
            self.source_type.addItem(item.value, item)
        if task:
            self.source_type.setCurrentIndex(list(SourceType).index(task.source_type))
        self.interval = QSpinBox()
        self.interval.setRange(5, 1440)
        self.interval.setValue(task.check_interval_minutes if task else default_interval)
        self.enabled = QCheckBox("启用")
        self.enabled.setChecked(task.enabled if task else True)
        self.keywords = QLineEdit("、".join(task.keywords) if task else "")
        self.keywords.setPlaceholderText("例如：推免、夏令营、复试；留空表示全部通知")
        self.llm = QCheckBox("启用 AI 摘要")
        self.llm.setChecked(task.enable_llm_summary if task else True)

        adapter = task.adapter if task else AdapterConfig()
        self.item_selector = QLineEdit(adapter.item_selector or "")
        self.title_selector = QLineEdit(adapter.title_selector or "")
        self.link_selector = QLineEdit(adapter.link_selector or "")
        self.date_selector = QLineEdit(adapter.date_selector or "")
        self.encoding = QLineEdit(adapter.encoding or "")
        self.encoding.setPlaceholderText("留空自动识别；可填 utf-8 / gbk")
        self.insecure_tls = QCheckBox("允许跳过 HTTPS 证书校验")
        self.insecure_tls.setChecked(adapter.allow_insecure_tls)
        self.detect_updates = QCheckBox("检测详情内容更新")
        self.detect_updates.setChecked(adapter.detect_content_updates)

        form = QFormLayout(self)
        form.addRow("任务名称", self.name)
        form.addRow("公开网址", self.url)
        form.addRow("来源类型", self.source_type)
        form.addRow("检查间隔（分钟）", self.interval)
        form.addRow("关键词", self.keywords)
        form.addRow(self.enabled)
        form.addRow(self.llm)

        advanced = QGroupBox("高级识别配置")
        advanced_form = QFormLayout(advanced)
        advanced_form.addRow("条目选择器", self.item_selector)
        advanced_form.addRow("标题选择器", self.title_selector)
        advanced_form.addRow("链接选择器", self.link_selector)
        advanced_form.addRow("日期选择器", self.date_selector)
        advanced_form.addRow("编码", self.encoding)
        advanced_form.addRow(self.insecure_tls)
        advanced_form.addRow(self.detect_updates)
        form.addRow(advanced)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self):
        keywords = [item.strip() for item in self.keywords.text().replace(",", "、").split("、") if item.strip()]
        adapter = AdapterConfig(
            item_selector=self.item_selector.text().strip() or None,
            title_selector=self.title_selector.text().strip() or None,
            link_selector=self.link_selector.text().strip() or None,
            date_selector=self.date_selector.text().strip() or None,
            encoding=self.encoding.text().strip() or None,
            allow_insecure_tls=self.insecure_tls.isChecked(),
            detect_content_updates=self.detect_updates.isChecked(),
        )
        common = {
            "name": self.name.text().strip(),
            "url": self.url.text().strip(),
            "source_type": SourceType(self.source_type.currentData()),
            "check_interval_minutes": self.interval.value(),
            "enabled": self.enabled.isChecked(),
            "keywords": keywords,
            "enable_llm_summary": self.llm.isChecked(),
            "adapter": adapter,
        }
        if self.task:
            return self.task.id, TaskUpdateInput(**common)
        return None, TaskCreateInput(**common)


class MainWindow(QMainWindow):
    def __init__(self, bridge: ServiceBridge, *, startup_manager=None):
        super().__init__()
        self.bridge = bridge
        self.startup_manager = startup_manager or WindowsStartupManager()
        self.settings: AppSettings | None = None
        self._start_hidden = False
        self.setWindowTitle("研究生招生信息监视系统")
        self.resize(1280, 800)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_task_page()
        self._build_message_page()
        self._build_settings_page()
        self._build_diagnostics_page()
        self.statusBar().showMessage("后台调度器已启动")
        self._tray = self._build_tray()
        self._closing = False
        QTimer.singleShot(100, self.refresh_all)

    def _build_task_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.task_table = QTableWidget(0, 10)
        self.task_table.setHorizontalHeaderLabels(["ID", "\u4efb\u52a1", "\u72b6\u6001", "\u542f\u7528", "\u95f4\u9694", "\u4e0a\u6b21\u68c0\u67e5", "\u4e0a\u6b21\u6210\u529f", "\u4e0b\u6b21\u68c0\u67e5", "\u8fde\u7eed\u5931\u8d25", "\u5f85\u5904\u7406"])
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.task_table)
        buttons = QHBoxLayout()
        for label, handler in [
            ("新建", self.new_task),
            ("编辑", self.edit_task),
            ("删除", self.delete_task),
            ("启用/暂停", self.toggle_task),
            ("立即检查", self.check_task),
            ("识别预览", self.preview_task),
            ("刷新", self.refresh_tasks),
            ("导出", self.export_tasks),
            ("导入", self.import_tasks),
        ]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.tabs.addTab(page, "任务")

    def _build_message_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.event_table = QTableWidget(0, 7)
        self.event_table.setHorizontalHeaderLabels(["ID", "类型", "状态", "任务", "标题", "链接", "更新时间"])
        self.event_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.event_table)
        buttons = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_events)
        resend = QPushButton("重发邮件")
        resend.clicked.connect(self.resend_event)
        buttons.addWidget(refresh)
        buttons.addWidget(resend)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.tabs.addTab(page, "消息")

    def _build_settings_page(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        llm_box = QGroupBox("LLM（OpenAI-compatible /chat/completions）")
        llm_form = QFormLayout(llm_box)
        self.llm_base_url = QLineEdit()
        self.llm_api_key = SecretField("\u5c1a\u672a\u4fdd\u5b58 API Key")
        self.llm_api_key.reveal_requested.connect(
            lambda: self.bridge.call(
                self.bridge.service.settings_service.llm_api_key,
                self.llm_api_key.set_secret_for_display,
                self._secret_action_error,
            )
        )
        self.llm_api_key.copy_requested.connect(
            lambda: self.bridge.call(
                self.bridge.service.settings_service.llm_api_key,
                lambda value: self._copy_secret(value, "LLM API Key"),
                self._secret_action_error,
            )
        )
        self.llm_model = QLineEdit()
        self.llm_timeout = QSpinBox()
        self.llm_timeout.setRange(5, 300)
        self.llm_tokens = QSpinBox()
        self.llm_tokens.setRange(256, 8000)
        llm_form.addRow("Base URL", self.llm_base_url)
        llm_form.addRow("API Key", self.llm_api_key)
        llm_form.addRow("模型", self.llm_model)
        llm_form.addRow("超时（秒）", self.llm_timeout)
        llm_form.addRow("最大输出 Tokens", self.llm_tokens)
        test_llm = QPushButton("测试 LLM 连接")
        test_llm.clicked.connect(self.test_llm)
        llm_form.addRow(test_llm)

        smtp_box = QGroupBox("SMTP 邮件")
        smtp_form = QFormLayout(smtp_box)
        self.smtp_host = QLineEdit()
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(587)
        self.smtp_username = QLineEdit()
        self.smtp_password = SecretField("\u5c1a\u672a\u4fdd\u5b58 SMTP \u6388\u6743\u7801")
        self.smtp_password.reveal_requested.connect(
            lambda: self.bridge.call(
                self.bridge.service.settings_service.smtp_password,
                self.smtp_password.set_secret_for_display,
                self._secret_action_error,
            )
        )
        self.smtp_password.copy_requested.connect(
            lambda: self.bridge.call(
                self.bridge.service.settings_service.smtp_password,
                lambda value: self._copy_secret(value, "SMTP \u6388\u6743\u7801"),
                self._secret_action_error,
            )
        )
        self.smtp_sender = QLineEdit()
        self.smtp_recipient = QLineEdit()
        self.smtp_tls = QCheckBox("使用 STARTTLS / TLS")
        self.smtp_tls.setChecked(True)
        smtp_form.addRow("SMTP 服务器", self.smtp_host)
        smtp_form.addRow("端口", self.smtp_port)
        smtp_form.addRow("用户名", self.smtp_username)
        smtp_form.addRow("密码", self.smtp_password)
        smtp_form.addRow("发件人", self.smtp_sender)
        smtp_form.addRow("收件人", self.smtp_recipient)
        smtp_form.addRow(self.smtp_tls)
        test_mail = QPushButton("发送测试邮件")
        test_mail.clicked.connect(self.test_email)
        smtp_form.addRow(test_mail)

        general_box = QGroupBox("常规")
        general_form = QFormLayout(general_box)
        self.display_timezone = QComboBox()
        self.display_timezone.addItem("\u8ddf\u968f Windows \u7cfb\u7edf\u65f6\u533a", "system")
        for zone in sorted(available_timezones()):
            self.display_timezone.addItem(zone, zone)
        self.display_timezone.setToolTip("\u4ec5\u5f71\u54cd\u754c\u9762\u548c\u90ae\u4ef6\u4e2d\u7684\u65f6\u95f4\u663e\u793a\uff1b\u6570\u636e\u5e93\u5185\u90e8\u4ecd\u4f7f\u7528 UTC \u65f6\u95f4\u5b58\u50a8\u3002")

        self.default_interval = QSpinBox()
        self.default_interval.setRange(5, 1440)
        self.auto_start = QCheckBox("\u5f00\u673a\u81ea\u52a8\u542f\u52a8\uff0c\u5e76\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8")
        general_form.addRow("\u65f6\u95f4\u663e\u793a\u65f6\u533a", self.display_timezone)
        self.auto_start.setChecked(True)
        self.auto_start.setToolTip("\u4ec5\u5199\u5165\u5f53\u524d\u7528\u6237\u7684 Windows \u81ea\u542f\u52a8\u914d\u7f6e\uff0c\u4e0d\u9700\u8981\u7ba1\u7406\u5458\u6743\u9650\u3002")
        self.subject_prefix = QLineEdit("【招生监视】")
        self.immediate_email = QCheckBox("发现新消息立即发送邮件")
        self.immediate_email.setChecked(True)
        general_form.addRow("默认检查间隔（分钟）", self.default_interval)
        general_form.addRow("邮件主题前缀", self.subject_prefix)
        general_form.addRow(self.auto_start)
        general_form.addRow(self.immediate_email)

        save = QPushButton("保存设置")
        save.clicked.connect(self.save_settings)
        open_data = QPushButton("打开数据目录")
        open_data.clicked.connect(self.open_data_dir)
        outer.addWidget(llm_box)
        outer.addWidget(smtp_box)
        outer.addWidget(general_box)
        row = QHBoxLayout()
        row.addWidget(save)
        row.addWidget(open_data)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        self.tabs.addTab(page, "设置")

    def _build_diagnostics_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.run_table = QTableWidget(0, 5)
        self.run_table.setHorizontalHeaderLabels(["ID", "任务", "开始", "结束", "状态/错误"])
        self.run_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.run_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.run_table)
        self.diagnostic_text = QTextEdit()
        self.diagnostic_text.setReadOnly(True)
        layout.addWidget(self.diagnostic_text)
        refresh = QPushButton("刷新诊断")
        refresh.clicked.connect(self.refresh_runs)
        layout.addWidget(refresh)
        self.tabs.addTab(page, "诊断")

    def _build_tray(self):
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(0, 91, 172))
        painter = QPainter(pixmap)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "研")
        painter.end()
        icon = QIcon(pixmap)
        tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.showNormal)
        check_action = QAction("立即检查全部任务", self)
        check_action.triggered.connect(self.check_all)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.exit_app)
        menu.addAction(show_action)
        menu.addAction(check_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.setToolTip("研究生招生信息监视系统")
        tray.activated.connect(lambda reason: self.showNormal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        tray.show()
        return tray

    def refresh_all(self) -> None:
        if self._closing:
            return
        self.refresh_tasks()
        self.refresh_events()
        self.refresh_runs()
        self.bridge.call(self.bridge.service.settings_service.load, self._apply_settings, self._show_error)

    def refresh_tasks(self) -> None:
        self.bridge.call(self.bridge.service.task_service.list_tasks, self._render_tasks, self._show_error)

    def refresh_events(self) -> None:
        self.bridge.call(self.bridge.service.list_events, self._render_events, self._show_error)

    def refresh_runs(self) -> None:
        self.bridge.call(self.bridge.service.recent_runs, self._render_runs, self._show_error)

    def _display_timezone(self):
        name = self.settings.display_timezone if self.settings else "system"
        if name == "system":
            return datetime.now().astimezone().tzinfo or UTC
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return UTC

    def _format_display_time(self, value) -> str:
        if value is None:
            return "-"
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=UTC)
        local_value = value.astimezone(self._display_timezone())
        offset = local_value.strftime("%z")
        offset_text = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
        return f"{local_value.strftime('%Y-%m-%d %H:%M:%S')} {offset_text}"


    def _render_tasks(self, tasks):
        self.task_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = [
                task.id,
                task.name,
                task.current_status,
                "\u542f\u7528" if task.enabled else "\u6682\u505c",
                f"{task.check_interval_minutes} \u5206\u949f",
                self._format_display_time(task.last_checked_at),
                self._format_display_time(task.last_success_at),
                self._format_display_time(task.next_check_at),
                task.failure_count,
                task.pending_event_count,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 2 and task.failure_count >= 3:
                    item.setForeground(QColor(200, 0, 0))
                self.task_table.setItem(row, column, item)
        self.task_table.resizeColumnsToContents()

    def _render_events(self, events):
        self.event_table.setRowCount(len(events))
        type_label = {"new_item": "新消息", "content_updated": "内容更新"}
        for row, event in enumerate(events):
            values = [
                event.id, type_label.get(event.event_type.value, event.event_type.value), event.status.value,
                event.task_name, event.detail_title or event.item_title, event.item_url or "-",
                self._format_display_time(event.updated_at),
            ]
            for column, value in enumerate(values):
                self.event_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.event_table.resizeColumnsToContents()

    def _render_runs(self, runs):
        self.run_table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            normal_label = "\u6b63\u5e38"
            values = [
                run.id,
                run.task_id,
                self._format_display_time(run.started_at),
                self._format_display_time(run.finished_at),
                f"{run.status.value}: {run.error_message or normal_label}",
            ]
            for column, value in enumerate(values):
                self.run_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.run_table.resizeColumnsToContents()

    def _selected_task_id(self) -> int | None:
        rows = self.task_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "请选择任务", "请先在任务列表中选择一行。")
            return None
        return int(self.task_table.item(rows[0].row(), 0).text())

    def new_task(self):
        dialog = TaskDialog(self, default_interval=self.settings.default_interval_minutes if self.settings else 30)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        _, item = dialog.value()
        self.bridge.call(lambda: self.bridge.service.task_service.create_task(item), lambda _: self.after_task_change("任务已创建"), self._show_error)

    def edit_task(self):
        task_id = self._selected_task_id()
        if task_id is None:
            return
        def update(task):
            dialog = TaskDialog(self, task=task, default_interval=self.settings.default_interval_minutes if self.settings else 30)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            update_id, item = dialog.value()
            self.bridge.call(lambda: self.bridge.service.task_service.update_task(update_id, item), lambda _: self.after_task_change("任务已保存"), self._show_error)
        self.bridge.call(lambda: self.bridge.service.task_service.get_task(task_id), update, self._show_error)

    def delete_task(self):
        task_id = self._selected_task_id()
        if task_id is None:
            return
        if QMessageBox.question(self, "删除任务", "删除任务会同时删除该任务的快照、消息和通知记录，确定继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.bridge.call(lambda: self.bridge.service.task_service.delete_task(task_id), lambda _: self.after_task_change("任务已删除"), self._show_error)

    def toggle_task(self):
        task_id = self._selected_task_id()
        if task_id is None:
            return
        def loaded(task):
            enabled = not task.enabled
            self.bridge.call(lambda: self.bridge.service.task_service.set_enabled(task_id, enabled), lambda _: self.refresh_tasks(), self._show_error)
        self.bridge.call(lambda: self.bridge.service.task_service.get_task(task_id), loaded, self._show_error)

    def check_task(self):
        task_id = self._selected_task_id()
        if task_id is None:
            return
        self.statusBar().showMessage("正在检查任务…")
        self.bridge.call(lambda: self.bridge.service.trigger_manual_check(task_id), lambda _: self.after_task_change("检查完成"), self._show_error)

    def check_all(self):
        self.statusBar().showMessage("已触发全部启用任务检查")
        self.bridge.call(self.bridge.service.scheduler.check_all_enabled, lambda _: None, self._show_error)
        QTimer.singleShot(1500, self.refresh_all)

    def preview_task(self):
        task_id = self._selected_task_id()
        if task_id is None:
            return
        def show(preview):
            count, rows = preview
            text = f"识别到 {count} 条通知：\n\n" + "\n".join(
                f"{index}. {title}\n   {url} {date}" for index, (title, url, date) in enumerate(rows, 1)
            )
            QMessageBox.information(self, "识别预览", text)
        self.bridge.call(lambda: self.bridge.service.monitoring.preview_adapter(task_id), show, self._show_error)

    def export_tasks(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出任务", "pgam-tasks.json", "JSON (*.json)")
        if not path:
            return
        self.bridge.call(lambda: self.bridge.service.task_service.export_tasks(path), lambda count: QMessageBox.information(self, "导出完成", f"已导出 {count} 条任务。"), self._show_error)

    def import_tasks(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入任务", "", "JSON (*.json)")
        if not path:
            return
        replace_items = QMessageBox.question(self, "导入模式", "是否清空现有任务后导入？") == QMessageBox.StandardButton.Yes
        self.bridge.call(lambda: self.bridge.service.task_service.import_tasks(path, replace_items), lambda count: self.after_task_change(f"已导入 {count} 条任务"), self._show_error)

    def _refresh_secret_masks(self) -> None:
        self.bridge.call(
            self.bridge.service.settings_service.llm_api_key_length,
            self.llm_api_key.set_masked_length,
            self._secret_action_error,
        )
        self.bridge.call(
            self.bridge.service.settings_service.smtp_password_length,
            self.smtp_password.set_masked_length,
            self._secret_action_error,
        )

    def _secret_action_error(self, message: str) -> None:
        self._show_error("\u4fdd\u5bc6\u5b57\u6bb5\u64cd\u4f5c\u5931\u8d25\uff0c\u8bf7\u67e5\u770b\u8bca\u65ad\u4fe1\u606f\u3002")

    def _copy_secret(self, value: str, label: str) -> None:
        if not value:
            QMessageBox.information(self, "\u590d\u5236\u5931\u8d25", f"{label}\u5c1a\u672a\u4fdd\u5b58\u3002")
            return
        QApplication.clipboard().setText(value)
        self.statusBar().showMessage(f"{label}\u5df2\u590d\u5236", 3000)

    def _apply_settings(self, settings: AppSettings):
        self.settings = settings
        self.llm_base_url.setText(settings.llm_base_url)
        self.llm_model.setText(settings.llm_model)
        self.llm_timeout.setValue(settings.llm_timeout_seconds)
        self.llm_tokens.setValue(settings.llm_max_output_tokens)
        self.smtp_host.setText(settings.smtp_host)
        self.smtp_port.setValue(settings.smtp_port)
        self.smtp_username.setText(settings.smtp_username)
        self.smtp_sender.setText(settings.smtp_sender)
        self.smtp_recipient.setText(settings.smtp_recipient)
        self.smtp_tls.setChecked(settings.smtp_use_tls)
        self.default_interval.setValue(settings.default_interval_minutes)
        timezone_index = self.display_timezone.findData(settings.display_timezone)
        self.display_timezone.setCurrentIndex(max(0, timezone_index))
        self.auto_start.setChecked(settings.auto_start_enabled and self.startup_manager.is_enabled())
        self.subject_prefix.setText(settings.email_subject_prefix)
        self.immediate_email.setChecked(settings.immediate_email)
        self._refresh_secret_masks()

    def save_settings(self, after_save=None):
        if self.settings is None:
            return
        settings = replace(
            self.settings,
            default_interval_minutes=self.default_interval.value(),
            display_timezone=self.display_timezone.currentData(),
            auto_start_enabled=self.auto_start.isChecked(),
            email_subject_prefix=self.subject_prefix.text().strip() or "【招生监视】",
            immediate_email=self.immediate_email.isChecked(),
            llm_base_url=self.llm_base_url.text().strip().rstrip("/"),
            llm_model=self.llm_model.text().strip(),
            llm_timeout_seconds=self.llm_timeout.value(),
            llm_max_output_tokens=self.llm_tokens.value(),
            smtp_host=self.smtp_host.text().strip(),
            smtp_port=self.smtp_port.value(),
            smtp_username=self.smtp_username.text().strip(),
            smtp_sender=self.smtp_sender.text().strip(),
            smtp_recipient=self.smtp_recipient.text().strip(),
            smtp_use_tls=self.smtp_tls.isChecked(),
        )
        try:
            self.startup_manager.set_enabled(self.auto_start.isChecked())
        except OSError as exc:
            self._show_error(f"\u5f00\u673a\u81ea\u542f\u52a8\u8bbe\u7f6e\u5931\u8d25\uff1a{exc}")
            return
        api_key = self.llm_api_key.value_for_save()
        password = self.smtp_password.value_for_save()
        self.bridge.call(
            lambda: self.bridge.service.settings_service.save(
                settings, llm_api_key=api_key if api_key else None, smtp_password=password if password else None
            ),
            lambda result: self._settings_saved(settings, result, after_save),
            self._show_error,
        )

    def _settings_saved(self, settings, _result=None, after_save=None):
        self.settings = settings
        self._refresh_secret_masks()
        self.statusBar().showMessage("设置已保存", 5000)

    def test_llm(self):
        self.save_settings()
        self.bridge.call(self.bridge.service.test_llm, lambda text: QMessageBox.information(self, "LLM 连接成功", text), self._show_error)

    def test_email(self):
        self.save_settings()
        self.bridge.call(self.bridge.service.send_test_email, lambda: QMessageBox.information(self, "邮件已发送", "测试邮件发送成功。"), self._show_error)

    def open_data_dir(self):
        path = self.bridge.service.data_dir
        if sys.platform == "win32":
            import os
            os.startfile(path)  # noqa: S606
        else:
            QMessageBox.information(self, "数据目录", str(path))

    def resend_event(self):
        rows = self.event_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "请选择消息", "请先选择一条消息。")
            return
        event_id = int(self.event_table.item(rows[0].row(), 0).text())
        self.bridge.call(lambda: self.bridge.service.monitoring.resend_event(event_id), lambda _: self.refresh_events(), self._show_error)

    def after_task_change(self, message):
        self.statusBar().showMessage(message, 5000)
        self.refresh_all()

    def _show_error(self, message: str):
        self.statusBar().showMessage(message, 10000)
        self.diagnostic_text.append(message)

    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        selection = QMessageBox.question(
            self,
            "关闭窗口",
            "是否最小化到系统托盘并继续监视？选择“No”将退出程序。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if selection in {QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.YesToAll}:
            event.ignore()
            self.hide()
            self._tray.showMessage("仍在后台监视", "程序已最小化到系统托盘。", QSystemTrayIcon.MessageIcon.Information, 3000)
            return
        if selection == QMessageBox.StandardButton.No:
            self.exit_app()
            event.accept()
            return
        event.ignore()

    def exit_app(self):
        self._closing = True
        self._tray.hide()
        self.hide()
        QApplication.quit()


def _tray_icon_path() -> Path:
    return Path(__file__).with_name("app.ico")


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("PostGraduateAdmissionMonitor")
    start_hidden = "--hidden" in sys.argv
    lock_path = default_data_dir() / ".pgam.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_path))
    if not lock.tryLock(100):
        QMessageBox.warning(None, "程序已在运行", "研究生招生信息监视系统已经启动，请从系统托盘打开。")
        return 0
    try:
        service = AppService()
        startup_manager = WindowsStartupManager()
        settings = service.settings_repo.load()
        auto_start_setting_exists = bool(
            service.db.query(
                "SELECT 1 FROM app_config WHERE key='auto_start_enabled'"
            )
        )
        if not auto_start_setting_exists:
            settings.auto_start_enabled = True
            service.settings_repo.save(settings)
        try:
            startup_manager.set_enabled(settings.auto_start_enabled)
        except OSError as exc:
            QMessageBox.warning(None, "\u5f00\u673a\u81ea\u542f\u52a8", f"\u65e0\u6cd5\u8bbe\u7f6e\u5f00\u673a\u81ea\u542f\u52a8\uff1a{exc}")
        bridge = ServiceBridge(service)
        window = MainWindow(bridge, startup_manager=startup_manager)
        window._start_hidden = start_hidden
        if start_hidden:
            window.hide()
            window._tray.show()
        else:
            window.show()
        result = app.exec()
        bridge.shutdown()
        return result
    finally:
        lock.unlock()
