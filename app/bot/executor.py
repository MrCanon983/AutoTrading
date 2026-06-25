"""
交易执行器 - 处理交易执行逻辑。

将 AI 工具调用转换为实际的 OKX 订单，
包含精度处理、安全检查和错误处理。
"""

import logging
from typing import Optional, Dict
from dataclasses import dataclass

from app.bot.okx_client import OKXClient
from app.bot.exceptions import (
    OrderExecutionError,
    InsufficientBalanceError,
    PositionNotFoundError
)

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """交易执行结果。"""
    success: bool
    order_id: Optional[str] = None
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    executed_price: float = 0.0
    error: Optional[str] = None
    sl_failed: bool = False  # 止损设置失败标志
    tp_failed: bool = False  # 止盈设置失败标志
    # 订单 ID 追踪 (算法订单使用 algoId)
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None


class TradeExecutor:
    """
    交易执行处理器。
    
    将 AI 决策转换为 OKX 订单，包含：
    - USDT 转数量
    - 精度处理
    - 余额检查
    - 随开仓单附带止损止盈
    """
    
    # 安全限制
    MIN_TRADE_USDT = 10.0  # 最小名义交易金额
    MAX_NOTIONAL_FREE_BALANCE_MULTIPLE = 125.0  # OKX 合约杠杆上限保护
    
    def __init__(self, exchange_client: OKXClient):
        """
        初始化交易执行器。
        
        Args:
            exchange_client: 已配置的 OKXClient 实例
        """
        self.client = exchange_client
    
    def open_position(
        self,
        symbol: str,
        side: str,
        amount_usdt: float,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        order_type: str = "market",
        limit_price: Optional[float] = None
    ) -> ExecutionResult:
        """
        开仓或加仓，必须随开仓单附带止损。
        
        Args:
            symbol: 交易对 (例如 'BTC/USDT')
            side: 'LONG' (做多) 或 'SHORT' (做空) - 持仓方向
            amount_usdt: 合约名义金额 (USDT)，不是保证金。保证金约等于名义金额 / 杠杆。
            stop_loss_price: 必需止损触发价格
            take_profit_price: 可选止盈触发价格
            order_type: 'market' 或 'limit'
            limit_price: 限价单价格
            
        Returns:
            ExecutionResult 包含订单详情
            
        Note:
            如需设置杠杆，请在调用此方法前使用 set_leverage 方法。
        """
        side = side.upper()
        
        # 兼容性处理：支持 BUY/SELL 和 LONG/SHORT 两种格式
        # 统一转换为 position_side (LONG/SHORT) 和 order_side (BUY/SELL)
        if side in ('LONG', 'BUY'):
            position_side = 'LONG'
            order_side = 'BUY'
        elif side in ('SHORT', 'SELL'):
            position_side = 'SHORT'
            order_side = 'SELL'
        else:
            raise ValueError(f"无效方向: {side}，应为 LONG/SHORT 或 BUY/SELL")
        
        try:
            order_type = (order_type or 'market').lower()
            if order_type not in ('market', 'limit'):
                raise ValueError(f"无效订单类型: {order_type}")
            if order_type == 'limit' and (not limit_price or limit_price <= 0):
                raise ValueError("限价单必须提供有效 limit_price")
            if not stop_loss_price or stop_loss_price <= 0:
                raise ValueError("开仓必须提供有效 stop_loss_price，系统不会创建无止损仓位")

            entry_reference_price = limit_price if order_type == 'limit' else self.client.fetch_ticker(symbol).last_price
            self._validate_protective_prices(
                position_side,
                entry_reference_price,
                stop_loss_price,
                take_profit_price
            )

            logger.info(
                "正在开仓: %s %s %.2f USDT (%s, 入场参考: %.8f, 限价: %s, 随单止损: %s, 随单止盈: %s)",
                position_side, symbol, amount_usdt,
                order_type,
                entry_reference_price,
                limit_price or 'none',
                stop_loss_price,
                take_profit_price or 'none'
            )
            
            # 检查余额
            balance = self.client.fetch_balance()
            free_balance = balance.get('free', 0)
            
            # 安全检查
            if amount_usdt < self.MIN_TRADE_USDT:
                raise ValueError(
                    f"交易金额 {amount_usdt} 低于最小限制 {self.MIN_TRADE_USDT}"
                )
            
            if free_balance <= 0:
                raise InsufficientBalanceError(amount_usdt, free_balance)

            # count_usdt 是合约名义金额，不是保证金。
            # 例如 50x 下 25 USDT 名义金额约占用 0.5 USDT 保证金。
            # OKX 会基于实际杠杆、风控和手续费做最终校验；这里仅阻挡明显离谱的名义金额。
            max_estimated_notional = free_balance * self.MAX_NOTIONAL_FREE_BALANCE_MULTIPLE
            if amount_usdt > max_estimated_notional:
                raise ValueError(
                    f"名义金额 {amount_usdt:.2f} USDT 超过当前可用余额的保护上限 "
                    f"{max_estimated_notional:.2f} USDT"
                )
            
            # 检查调整后的最小名义价值
            min_notional = self.client.get_min_notional(symbol)
            if amount_usdt < min_notional:
                raise ValueError(
                    f"金额 {amount_usdt:.2f} 低于最小名义价值 {min_notional}"
                )
            
            # 计算数量
            quantity = self.client.calculate_quantity(
                symbol,
                amount_usdt,
                current_price=limit_price if order_type == 'limit' else None
            )
            
            if quantity <= 0:
                raise ValueError(f"{amount_usdt} USDT 计算出的数量为 0")
            
            # 执行开仓单
            if order_type == 'limit':
                order = self.client.create_limit_order(
                    symbol,
                    order_side,
                    quantity,
                    limit_price,
                    position_side,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price
                )
            else:
                order = self.client.create_market_order(
                    symbol,
                    order_side,
                    quantity,
                    position_side,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price
                )
            
            # 获取成交价
            executed_price = float(order.get('average', 0) or order.get('price', 0))
            
            logger.info(
                "仓位开仓单已提交且附带止损: %s %s %.8f @ %.4f (SL=%s, TP=%s)",
                side, symbol, quantity, executed_price,
                stop_loss_price,
                take_profit_price or 'none'
            )
            
            return ExecutionResult(
                success=True,
                order_id=order.get('id'),
                symbol=symbol,
                side=side,
                quantity=quantity,
                executed_price=executed_price,
                sl_failed=False,
                tp_failed=False
            )
            
        except (InsufficientBalanceError, ValueError) as e:
            logger.error("交易验证失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                side=side,
                error=str(e)
            )
        except Exception as e:
            logger.error("订单执行失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                side=side,
                error=str(e)
            )

    def _validate_protective_prices(
        self,
        position_side: str,
        entry_reference_price: float,
        stop_loss_price: float,
        take_profit_price: Optional[float] = None
    ) -> None:
        """确保保护性价格在正确方向，避免无效或危险的开仓单。"""
        if entry_reference_price <= 0:
            raise ValueError("无法获取有效入场参考价，拒绝开仓")

        if position_side == 'LONG':
            if stop_loss_price >= entry_reference_price:
                raise ValueError(
                    f"做多止损价 {stop_loss_price} 必须低于入场参考价 {entry_reference_price}"
                )
            if take_profit_price and take_profit_price <= entry_reference_price:
                raise ValueError(
                    f"做多止盈价 {take_profit_price} 必须高于入场参考价 {entry_reference_price}"
                )
        elif position_side == 'SHORT':
            if stop_loss_price <= entry_reference_price:
                raise ValueError(
                    f"做空止损价 {stop_loss_price} 必须高于入场参考价 {entry_reference_price}"
                )
            if take_profit_price and take_profit_price >= entry_reference_price:
                raise ValueError(
                    f"做空止盈价 {take_profit_price} 必须低于入场参考价 {entry_reference_price}"
                )
    
    def close_position(
        self,
        symbol: str,
        percentage: int,
        reason: str = ""
    ) -> ExecutionResult:
        """
        平仓或减仓。
        
        Args:
            symbol: 交易对
            percentage: 平仓百分比 (1-100)
            reason: 平仓原因 (用于日志)
            
        Returns:
            ExecutionResult 包含订单详情
        """
        logger.info(
            "正在平仓: %s %d%% (原因: %s)",
            symbol, percentage, reason or 'none'
        )
        
        try:
            # 验证百分比
            if not 1 <= percentage <= 100:
                raise ValueError(f"无效百分比: {percentage}")
            
            # 获取当前仓位
            position = self.client.get_position_size(symbol)
            
            if position is None:
                raise PositionNotFoundError(symbol)
            
            current_contracts = position['contracts']
            position_side = position['side']  # 'LONG' or 'SHORT'
            
            if current_contracts <= 0:
                raise PositionNotFoundError(symbol)
            
            # 计算平仓数量
            close_quantity = current_contracts * (percentage / 100.0)
            
            # 截断到精度
            precision = self.client.get_precision(symbol)
            close_quantity = self.client.truncate_to_precision(
                close_quantity, precision['amount']
            )
            
            if close_quantity <= 0:
                raise ValueError("计算出的平仓数量为 0")
            
            # 确定平仓方向 (与持仓相反)
            close_side = 'SELL' if position_side == 'LONG' else 'BUY'
            
            # 如果是全平仓，先取消所有挂单，防止残留或干扰
            # 这是一个更安全的操作顺序：
            # 1. 撤单 -> 确保没有由旧策略产生的挂单
            # 2. 平仓 -> 安全退出市场
            if percentage == 100:
                try:
                    self.client.cancel_all_orders(symbol)
                    logger.info("全平仓前已撤销 %s 所有挂单", symbol)
                except Exception as e:
                    logger.warning("平仓前撤单失败 (继续尝试平仓): %s", e)
            else:
                # 部分平仓提醒
                logger.info("部分平仓 %d%% - 现有止损订单保持有效", percentage)

            # 执行平仓单 (双向持仓模式需要传递 positionSide)
            order = self.client.create_market_order(symbol, close_side, close_quantity, position_side)
            
            executed_price = float(order.get('average', 0) or order.get('price', 0))
            
            logger.info(
                "仓位已平仓: %s %s %.8f @ %.4f",
                close_side, symbol, close_quantity, executed_price
            )
            
            return ExecutionResult(
                success=True,
                order_id=order.get('id'),
                symbol=symbol,
                side=close_side,
                quantity=close_quantity,
                executed_price=executed_price
            )
            
        except PositionNotFoundError as e:
            logger.warning("无仓位可平: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
        except Exception as e:
            logger.error("平仓失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
    
    def set_leverage(self, symbol: str, leverage: int) -> ExecutionResult:
        """
        设置交易对杠杆。
        
        Args:
            symbol: 交易对
            leverage: 杠杆倍数 (1-125)
            
        Returns:
            ExecutionResult
        """
        logger.info("正在设置杠杆: %s -> %dx", symbol, leverage)
        
        try:
            self.client.set_leverage(symbol, leverage)
            return ExecutionResult(
                success=True,
                symbol=symbol,
                side=f"LEVERAGE_{leverage}x"
            )
        except Exception as e:
            logger.error("设置杠杆失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
    
    def set_margin_mode(self, symbol: str, mode: str) -> ExecutionResult:
        """
        设置交易对保证金模式。
        
        Args:
            symbol: 交易对
            mode: 'cross' (全仓) 或 'isolated' (逐仓)
            
        Returns:
            ExecutionResult
        """
        logger.info("正在设置保证金模式: %s -> %s", symbol, mode)
        
        try:
            self.client.set_margin_mode(symbol, mode)
            return ExecutionResult(
                success=True,
                symbol=symbol,
                side=f"MARGIN_{mode.upper()}"
            )
        except Exception as e:
            logger.error("设置保证金模式失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
    
    def modify_position_tpsl(
        self, 
        symbol: str,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None
    ) -> ExecutionResult:
        """
        修改现有仓位的止盈止损。
        
        取消现有止盈止损单并创建新单。
        
        Args:
            symbol: 交易对
            stop_loss_price: 新止损价 (None 表示不修改)
            take_profit_price: 新止盈价 (None 表示不修改)
            
        Returns:
            ExecutionResult
        """
        logger.info(
            "正在修改仓位止盈止损: %s (止损: %s, 止盈: %s)",
            symbol, stop_loss_price or 'unchanged', take_profit_price or 'unchanged'
        )
        
        try:
            # 获取当前仓位
            position = self.client.get_position_size(symbol)
            
            if position is None:
                raise PositionNotFoundError(symbol)
            
            quantity = position['contracts']
            position_side = position['side']  # 'LONG' or 'SHORT'
            opposite_side = 'SELL' if position_side == 'LONG' else 'BUY'
            
            # 取消现有止盈止损单
            if stop_loss_price:
                self.client.cancel_orders_by_type(symbol, 'stop_loss')
            if take_profit_price:
                self.client.cancel_orders_by_type(symbol, 'take_profit')
            
            # 创建新止损 (传递 positionSide)
            if stop_loss_price and stop_loss_price > 0:
                try:
                    self.client.create_stop_loss_order(
                        symbol, opposite_side, quantity, stop_loss_price, position_side
                    )
                    logger.info("新止损已设置 @ %.2f", stop_loss_price)
                except Exception as e:
                    logger.warning("设置止损失败: %s", e)
            
            # 创建新止盈 (传递 positionSide)
            if take_profit_price and take_profit_price > 0:
                try:
                    self.client.create_take_profit_order(
                        symbol, opposite_side, quantity, take_profit_price, position_side
                    )
                    logger.info("新止盈已设置 @ %.2f", take_profit_price)
                except Exception as e:
                    logger.warning("设置止盈失败: %s", e)
            
            return ExecutionResult(
                success=True,
                symbol=symbol,
                side="MODIFY_TPSL"
            )
            
        except PositionNotFoundError as e:
            logger.warning("无仓位可修改: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
        except Exception as e:
            logger.error("修改止盈止损失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
    
    def cancel_orders(
        self,
        symbol: str,
        order_type: str = "all"
    ) -> ExecutionResult:
        """
        取消指定交易对的挂单。
        
        Args:
            symbol: 交易对
            order_type: 'stop_loss', 'take_profit', 或 'all'
            
        Returns:
            ExecutionResult
        """
        logger.info(
            "正在取消挂单: %s (类型: %s)",
            symbol, order_type
        )
        
        try:
            cancelled = self.client.cancel_orders_by_type(symbol, order_type)
            cancelled_count = len(cancelled) if cancelled else 0
            
            logger.info("已取消 %d 个 %s 挂单", cancelled_count, symbol)
            
            return ExecutionResult(
                success=True,
                symbol=symbol,
                side=f"CANCEL_{order_type.upper()}",
                quantity=float(cancelled_count)
            )
            
        except Exception as e:
            logger.error("取消挂单失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
    
    def cancel_order_by_id(
        self,
        symbol: str,
        order_id: str
    ) -> ExecutionResult:
        """
        根据订单 ID 取消单个订单。
        
        Args:
            symbol: 交易对
            order_id: 订单 ID
            
        Returns:
            ExecutionResult
        """
        logger.info(
            "正在取消订单: %s (ID: %s)",
            symbol, order_id
        )
        
        try:
            result = self.client.cancel_order_by_id(symbol, order_id)
            
            if result.get('success'):
                logger.info("已取消订单: %s", order_id)
                return ExecutionResult(
                    success=True,
                    symbol=symbol,
                    side=f"CANCEL_ORDER_{result.get('type', 'unknown').upper()}",
                    order_id=order_id
                )
            else:
                return ExecutionResult(
                    success=False,
                    symbol=symbol,
                    error=result.get('error', '未知错误')
                )
            
        except Exception as e:
            logger.error("取消订单失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
