"""
Bark 通知器。

将每轮 AI 分析和工具执行结果汇总后推送到 iOS Bark。
通知失败不影响交易循环。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from config import get_config
from app.bot.tz_utils import get_timezone
from app.bot.ai_agent import AIResponse
from app.bot.executor import ExecutionResult
from app.bot.xml_parser import ToolCall

logger = logging.getLogger(__name__)


class BarkNotifier:
    """通过 Bark 推送交易循环摘要。"""

    def __init__(self):
        self.config = get_config()

    @property
    def enabled(self) -> bool:
        return bool(self.config.BARK_ENABLED and self._resolve_endpoint())

    def _resolve_endpoint(self) -> Optional[str]:
        if self.config.BARK_URL:
            return self.config.BARK_URL.rstrip('/')
        if self.config.BARK_DEVICE_KEY:
            return f"{self.config.BARK_SERVER_URL.rstrip('/')}/{self.config.BARK_DEVICE_KEY}"
        return None

    def send_cycle_summary(self, cycle_result: Dict[str, Any]) -> bool:
        """发送单轮交易循环汇总。"""
        if not self.enabled:
            return False

        title = self._build_title(cycle_result)
        body = self._truncate(self._build_body(cycle_result), self.config.BARK_MAX_BODY_CHARS)
        return self._send(title, body)

    def _send(self, title: str, body: str) -> bool:
        endpoint = self._resolve_endpoint()
        if not endpoint:
            return False

        payload = {
            'title': title,
            'body': body,
            'group': self.config.BARK_GROUP or 'OpenNOF1',
            'level': self.config.BARK_LEVEL or 'active'
        }
        if self.config.BARK_SOUND:
            payload['sound'] = self.config.BARK_SOUND
        if self.config.BARK_ICON:
            payload['icon'] = self.config.BARK_ICON
        if self.config.BARK_OPEN_URL:
            payload['url'] = self.config.BARK_OPEN_URL

        try:
            response = requests.post(
                endpoint,
                json=payload,
                timeout=max(1, self.config.BARK_TIMEOUT_SECONDS)
            )
            response.raise_for_status()
            logger.info("Bark 通知已发送: %s", self._redact_endpoint(endpoint))
            return True
        except Exception as e:
            logger.warning("Bark 通知发送失败: %s", e)
            return False

    def _build_title(self, cycle_result: Dict[str, Any]) -> str:
        if not cycle_result.get('success'):
            return "OpenNOF1 循环异常"

        actions = cycle_result.get('actions') or []
        failed_actions = [item for item in cycle_result.get('tool_results', []) if not item.get('success')]
        trade_actions = [item for item in actions if item.get('tool') in ('trade_in', 'close_position')]

        if failed_actions:
            return f"OpenNOF1 有 {len(failed_actions)} 个工具失败"
        if trade_actions:
            return f"OpenNOF1 执行 {len(trade_actions)} 个交易动作"
        return "OpenNOF1 本轮分析完成"

    def _build_body(self, cycle_result: Dict[str, Any]) -> str:
        lines = [
            f"时间: {self._format_time(cycle_result.get('timestamp'))}",
            f"模式: {'实盘' if cycle_result.get('live_trading') else '模拟'}",
            f"状态: {'成功' if cycle_result.get('success') else '失败'}",
        ]

        if cycle_result.get('error'):
            lines.extend(["", "错误:", str(cycle_result['error'])])

        ai_rounds = cycle_result.get('ai_rounds') or []
        if ai_rounds:
            last_round = ai_rounds[-1]
            if len(ai_rounds) > 1:
                lines.append(f"重试: {len(ai_rounds) - 1} 次")
            if last_round.get('model'):
                lines.append(f"模型: {last_round['model']}")
            if last_round.get('tokens'):
                lines.append(f"Tokens: {last_round['tokens']}")

            reasoning = self._clean_reasoning(last_round.get('reasoning') or '')
            if reasoning:
                lines.extend(["", "市场分析/决策:", reasoning])

        tool_results = cycle_result.get('tool_results') or []
        if tool_results:
            lines.extend(["", "工具执行:"])
            for item in tool_results:
                lines.extend(self._format_tool_result(item))

        if not tool_results and not cycle_result.get('error'):
            lines.extend(["", "动作: 本轮未执行交易工具"])

        return "\n".join(lines).strip()

    def _format_tool_result(self, item: Dict[str, Any]) -> List[str]:
        tool_call: ToolCall = item['tool_call']
        execution_result: Optional[ExecutionResult] = item.get('execution_result')
        args = tool_call.args or {}
        status = "成功" if item.get('success') else "失败"
        prefix = f"- {self._tool_label(tool_call.name)} [{status}]"
        if tool_call.info:
            prefix += f" {tool_call.info}"

        lines = [prefix]

        if tool_call.name == 'trade_in':
            lines.append(
                "  "
                f"{args.get('side', '-')} {args.get('target', '-')} "
                f"{args.get('count_usdt', '-')} USDT名义 "
                f"{args.get('order_type', 'market')}"
            )
            if args.get('limit_price'):
                lines.append(f"  限价: {args.get('limit_price')}")
            lines.append(f"  止损: {args.get('stop_loss_price', '-')}")
            lines.append(f"  止盈: {args.get('take_profit_price', '未设置')}")
        elif tool_call.name == 'close_position':
            lines.append(
                f"  {args.get('target', '-')} 平仓 {args.get('percentage', '-')}% "
                f"原因: {args.get('reason', '-')}"
            )
        elif tool_call.name == 'set_leverage':
            lines.append(f"  {args.get('target', '-')} -> {args.get('leverage', '-')}x")
        elif tool_call.name == 'modify_position':
            lines.append(f"  {args.get('target', '-')}")
            if args.get('stop_loss_price'):
                lines.append(f"  新止损: {args.get('stop_loss_price')}")
            if args.get('take_profit_price'):
                lines.append(f"  新止盈: {args.get('take_profit_price')}")
        elif tool_call.name in ('cancel_orders', 'cancel_order'):
            lines.append(f"  {args.get('target', '-')} {args.get('order_type') or args.get('order_id') or ''}".rstrip())
        elif tool_call.name == 'update_memory':
            content = str(args.get('content') or '')
            lines.append(f"  记忆更新: {self._truncate(content, 180)}")
        else:
            lines.append(f"  参数: {args}")

        if execution_result:
            if execution_result.order_id:
                lines.append(f"  订单ID: {execution_result.order_id}")
            if execution_result.executed_price:
                lines.append(f"  成交价: {execution_result.executed_price}")
            if execution_result.quantity:
                lines.append(f"  数量: {execution_result.quantity}")
            if execution_result.error:
                lines.append(f"  错误: {execution_result.error}")

        return lines

    def _tool_label(self, name: str) -> str:
        labels = {
            'trade_in': '开仓/加仓',
            'close_position': '平仓/减仓',
            'set_leverage': '设置杠杆',
            'modify_position': '修改止盈止损',
            'cancel_orders': '取消挂单',
            'cancel_order': '取消指定订单',
            'update_memory': '更新记忆'
        }
        return labels.get(name, name)

    def _clean_reasoning(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ''
        return self._truncate(text, 1200)

    def _truncate(self, text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return text[:max_chars - 20].rstrip() + "\n...(已截断)"

    def _format_time(self, value: Any) -> str:
        """格式化为 YYYY-MM-DD HH:mm:ss。"""
        if not value:
            return '-'

        try:
            if isinstance(value, datetime):
                dt = value
            else:
                text = str(value).strip()
                if text.endswith('Z'):
                    text = text[:-1] + '+00:00'
                dt = datetime.fromisoformat(text)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(get_timezone()).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return str(value)

    def _redact_endpoint(self, endpoint: str) -> str:
        parsed = urlparse(endpoint)
        if not parsed.netloc:
            return '<configured>'
        return f"{parsed.scheme}://{parsed.netloc}/***"
