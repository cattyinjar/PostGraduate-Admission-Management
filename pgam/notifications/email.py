from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from html import escape
from typing import Protocol

from ..core.models import MonitorTask, SummaryResult
from ..core.ports import NotifierProtocol


class NotificationError(RuntimeError):
    pass


@dataclass(slots=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    use_tls: bool = True
    subject_prefix: str = "【招生监视】"


RELEVANCE_LABEL = {"high": "高相关", "medium": "中相关", "low": "低相关"}


class EmailNotifier(NotifierProtocol):
    def __init__(self, config: SmtpConfig):
        self.config = config

    async def send_event(
        self, task: MonitorTask, title: str, url: str | None, summary: SummaryResult
    ) -> None:
        subject = (
            f"{self.config.subject_prefix}{RELEVANCE_LABEL.get(summary.relevance, '中相关')} | "
            f"{task.name} | {title}"
        )
        html = self._event_html(task.name, title, url, summary)
        text = self._event_text(task.name, title, url, summary)
        await self._send(subject, html, text)

    async def send_task_failure(self, task: MonitorTask, error: str) -> None:
        subject = f"{self.config.subject_prefix}任务异常 | {task.name}"
        html = (
            f"<h2>监视任务连续失败</h2><p><strong>任务：</strong>{escape(task.name)}</p>"
            f"<p><strong>地址：</strong>{escape(task.url)}</p><p><strong>错误：</strong>{escape(error)}</p>"
        )
        text = f"监视任务连续失败\n任务：{task.name}\n地址：{task.url}\n错误：{error}"
        await self._send(subject, html, text)

    async def send_test(self) -> None:
        subject = f"{self.config.subject_prefix}测试邮件"
        html = "<h2>配置成功</h2><p>研究生招生信息监视系统可以正常发送邮件。</p>"
        text = "配置成功：研究生招生信息监视系统可以正常发送邮件。"
        await self._send(subject, html, text)

    async def _send(self, subject: str, html: str, text: str) -> None:
        if not self.config.host or not self.config.recipient:
            raise NotificationError("SMTP is not configured")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.sender or self.config.username
        message["To"] = self.config.recipient
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        try:
            if self.config.port == 465:
                with smtplib.SMTP_SSL(
                    self.config.host, self.config.port, timeout=20, context=ssl.create_default_context()
                ) as client:
                    self._auth_and_send(client, message)
            else:
                with smtplib.SMTP(self.config.host, self.config.port, timeout=20) as client:
                    client.ehlo()
                    if self.config.use_tls:
                        client.starttls(context=ssl.create_default_context())
                        client.ehlo()
                    self._auth_and_send(client, message)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotificationError(f"SMTP send failed: {type(exc).__name__}") from exc

    def _auth_and_send(self, client: smtplib.SMTP, message: EmailMessage) -> None:
        if self.config.username and self.config.password:
            client.login(self.config.username, self.config.password)
        client.send_message(message)

    @staticmethod
    def _event_text(task_name: str, title: str, url: str | None, summary: SummaryResult) -> str:
        dates = "\n".join(f"- {item['name']}：{item['time']}" for item in summary.key_dates)
        attachments = "\n".join(f"- {item}" for item in summary.attachments)
        return f"""任务：{task_name}
标题：{title}
分类：{summary.category}
相关性：{RELEVANCE_LABEL.get(summary.relevance, summary.relevance)}
首次发现：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

AI 摘要：
{summary.summary or 'AI 摘要暂不可用。'}

关键时间节点：
{dates or '无'}

需要执行的动作：
{summary.action_required or '无'}

原文链接：{url or '未提供'}

附件：
{attachments or '无'}"""

    @staticmethod
    def _event_html(task_name: str, title: str, url: str | None, summary: SummaryResult) -> str:
        dates = "".join(
            f"<li>{escape(item['name'])}：{escape(item['time'])}</li>" for item in summary.key_dates
        )
        attachments = "".join(f"<li>{escape(item)}</li>" for item in summary.attachments)
        links = "".join(f'<li><a href="{escape(item)}">{escape(item)}</a></li>' for item in summary.important_links)
        source = f'<p><a href="{escape(url or "")}">查看原文</a></p>' if url else "<p>原文链接未提供</p>"
        return f"""<div style="font-family: system-ui, -apple-system, sans-serif; max-width: 760px;">
<h2>{escape(title)}</h2>
<p>任务：{escape(task_name)} | 分类：{escape(summary.category)} | 相关性：{escape(RELEVANCE_LABEL.get(summary.relevance, summary.relevance))}</p>
<p><strong>AI 摘要：</strong>{escape(summary.summary or 'AI 摘要暂不可用。')}</p>
<h3>关键时间节点</h3><ul>{dates or '<li>无</li>'}</ul>
<h3>需要执行的动作</h3><p>{escape(summary.action_required or '无')}</p>
<h3>适用考生</h3><p>{escape(summary.target_audience or '未说明')}</p>
<h3>附件</h3><ul>{attachments or '<li>无</li>'}</ul>
<h3>重要链接</h3><ul>{links or '<li>无</li>'}</ul>
{source}
<p style="color:#666">首次发现时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
</div>"""


class NullNotifier(NotifierProtocol):
    async def send_event(
        self, task: MonitorTask, title: str, url: str | None, summary: SummaryResult
    ) -> None:
        return None

    async def send_task_failure(self, task: MonitorTask, error: str) -> None:
        return None


class NotifierFactory(Protocol):
    def create(self) -> NotifierProtocol: ...
