"""
OKX USDT 永续合约客户端封装。

通过 CCXT 提供稳定的 OKX 合约接口，供数据引擎和执行器复用。
外部仍使用 BTC/USDT 这类符号，客户端内部映射到 OKX swap 市场。
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import ccxt

from config import get_config
from app.bot.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


@dataclass
class OrderBookData:
    """包含不平衡计算的结构化订单簿数据。"""
    bids: List[List[float]]
    asks: List[List[float]]
    bid_ask_imbalance: float
    spread: float
    mid_price: float
    cumulative_bid_volume: float = 0.0
    cumulative_ask_volume: float = 0.0
    bid_wall_price: Optional[float] = None
    bid_wall_volume: float = 0.0
    ask_wall_price: Optional[float] = None
    ask_wall_volume: float = 0.0


@dataclass
class TickerData:
    """结构化行情数据。"""
    symbol: str
    last_price: float
    high_24h: float
    low_24h: float
    volume_24h: float
    change_24h_percent: float
    timestamp: int


@dataclass
class FundingRateData:
    """资金费率数据。"""
    symbol: str
    funding_rate: float
    funding_rate_annualized: float
    next_funding_time: int


@dataclass
class LongShortRatioData:
    """多空持仓比率数据。OKX 无同等公共端点时使用默认中性值。"""
    symbol: str
    long_account_ratio: float
    short_account_ratio: float
    long_short_ratio: float
    top_trader_long_ratio: float
    top_trader_short_ratio: float
    timestamp: int


class OKXClient:
    """
    OKX USDT 永续合约 CCXT 封装。

    设计目标是保持原执行器所需接口稳定：
    - 外部交易对使用 BTC/USDT
    - 内部市场使用 OKX swap 符号 BTC/USDT:USDT
    - 双向持仓使用 positionSide/posSide: long 或 short
    """

    def __init__(self, api_key: str = '', api_secret: str = '', passphrase: str = ''):
        config = get_config()

        self.api_key = api_key or config.OKX_API_KEY
        self.api_secret = api_secret or config.OKX_API_SECRET
        self.passphrase = passphrase or config.OKX_API_PASSPHRASE
        self.margin_mode = config.OKX_MARGIN_MODE

        self.exchange = ccxt.okx({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'password': self.passphrase,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',
                'defaultMarginMode': self.margin_mode,
                'createOrder': 'privatePostTradeOrder',
            }
        })

        self._markets_cache: Optional[Dict] = None

    # =========================================================================
    # 符号与市场工具
    # =========================================================================

    def _to_okx_symbol(self, symbol: str) -> str:
        """将 BTC/USDT 转为 OKX/CCXT swap 符号 BTC/USDT:USDT。"""
        if ':' in symbol:
            return symbol
        if symbol.endswith('/USDT'):
            return f"{symbol}:USDT"
        return symbol

    def _to_external_symbol(self, symbol: str) -> str:
        """将 BTC/USDT:USDT 转回项目内使用的 BTC/USDT。"""
        if not symbol:
            return symbol
        if '-' in symbol:
            parts = symbol.split('-')
            if len(parts) >= 2:
                return f"{parts[0]}/{parts[1]}"
        return symbol.split(':')[0]

    def _market(self, symbol: str) -> Dict:
        self.load_markets()
        okx_symbol = self._to_okx_symbol(symbol)
        return self.exchange.market(okx_symbol)

    def load_markets(self) -> Dict:
        """加载并缓存市场信息。"""
        if self._markets_cache is None:
            self._markets_cache = self.exchange.load_markets()
        return self._markets_cache

    def synchronize_time(self):
        """OKX 通过 CCXT 时间戳处理即可，这里保留兼容入口。"""
        try:
            self.exchange.load_time_difference()
        except Exception as e:
            logger.debug("OKX 时间同步失败，继续使用本地时间: %s", e)

    def _require_auth(self):
        """检查 API 凭证是否已配置。"""
        if not self.api_key or not self.api_secret or not self.passphrase:
            raise AuthenticationError("OKX private endpoints")

    # =========================================================================
    # 公共端点
    # =========================================================================

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1h', limit: int = 300) -> List[List]:
        return self.exchange.fetch_ohlcv(self._to_okx_symbol(symbol), timeframe, limit=limit)

    def fetch_ohlcv_multi_timeframe(
        self,
        symbol: str,
        timeframes: List[str] = None,
        limit: int = 300
    ) -> Dict[str, List[List]]:
        if timeframes is None:
            config = get_config()
            timeframes = config.TIMEFRAMES
        return {tf: self.fetch_ohlcv(symbol, tf, limit) for tf in timeframes}

    def fetch_ticker(self, symbol: str) -> TickerData:
        ticker = self.exchange.fetch_ticker(self._to_okx_symbol(symbol))
        last_price = ticker.get('last')
        if last_price is None or last_price <= 0:
            raise ValueError(f"Invalid ticker price for {symbol}: {last_price}")

        return TickerData(
            symbol=symbol,
            last_price=last_price,
            high_24h=ticker.get('high') or last_price,
            low_24h=ticker.get('low') or last_price,
            volume_24h=ticker.get('quoteVolume') or ticker.get('baseVolume') or 0,
            change_24h_percent=ticker.get('percentage') or 0,
            timestamp=ticker.get('timestamp') or 0
        )

    def fetch_tickers(self, symbols: List[str]) -> Dict[str, TickerData]:
        return {symbol: self.fetch_ticker(symbol) for symbol in symbols}

    def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBookData:
        order_book = self.exchange.fetch_order_book(self._to_okx_symbol(symbol), limit=depth)

        bids = order_book['bids'][:depth]
        asks = order_book['asks'][:depth]
        bid_volume = sum(bid[1] for bid in bids) if bids else 0
        ask_volume = sum(ask[1] for ask in asks) if asks else 0
        total_volume = bid_volume + ask_volume
        imbalance = (bid_volume - ask_volume) / total_volume if total_volume > 0 else 0.0

        best_bid = bids[0][0] if bids else 0
        best_ask = asks[0][0] if asks else 0
        spread = best_ask - best_bid if best_bid and best_ask else 0
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0

        bid_wall_price, bid_wall_volume = self._detect_order_wall(bids)
        ask_wall_price, ask_wall_volume = self._detect_order_wall(asks)

        return OrderBookData(
            bids=bids,
            asks=asks,
            bid_ask_imbalance=imbalance,
            spread=spread,
            mid_price=mid_price,
            cumulative_bid_volume=bid_volume,
            cumulative_ask_volume=ask_volume,
            bid_wall_price=bid_wall_price,
            bid_wall_volume=bid_wall_volume,
            ask_wall_price=ask_wall_price,
            ask_wall_volume=ask_wall_volume
        )

    def _detect_order_wall(self, orders: List[List[float]], threshold: float = 3.0) -> tuple:
        if not orders or len(orders) < 3:
            return None, 0.0
        avg_volume = sum(o[1] for o in orders) / len(orders)
        for price, volume in orders:
            if volume >= avg_volume * threshold:
                return price, volume
        return None, 0.0

    def fetch_funding_rate(self, symbol: str) -> FundingRateData:
        funding_info = self.exchange.fetch_funding_rate(self._to_okx_symbol(symbol))
        rate = funding_info.get('fundingRate', 0) or 0
        next_time = funding_info.get('fundingTimestamp', 0) or 0
        annualized = rate * 3 * 365 * 100
        return FundingRateData(
            symbol=symbol,
            funding_rate=rate,
            funding_rate_annualized=annualized,
            next_funding_time=next_time
        )

    def fetch_long_short_ratio(self, symbol: str) -> LongShortRatioData:
        """OKX 无同等公共端点时返回中性多空比。"""
        return LongShortRatioData(
            symbol=symbol,
            long_account_ratio=0.5,
            short_account_ratio=0.5,
            long_short_ratio=1.0,
            top_trader_long_ratio=0.5,
            top_trader_short_ratio=0.5,
            timestamp=int(time.time() * 1000)
        )

    def fetch_top_gainers_losers(self, limit: int = 50) -> Dict[str, Any]:
        tickers = self.exchange.fetch_tickers()
        usdt_pairs = []
        for symbol, data in tickers.items():
            external = self._to_external_symbol(symbol)
            if not external.endswith('/USDT'):
                continue
            percentage = data.get('percentage')
            if percentage is not None:
                usdt_pairs.append((external, percentage))

        sorted_by_gain = sorted(usdt_pairs, key=lambda x: x[1], reverse=True)
        top_pairs = sorted_by_gain[:limit]
        gainers_in_top = [(s, p) for s, p in top_pairs if p > 0]
        losers_in_top = [(s, p) for s, p in top_pairs if p < 0]
        advance_count = len(gainers_in_top)
        decline_count = len(losers_in_top)
        ad_ratio = advance_count / decline_count if decline_count > 0 else (9999.0 if advance_count > 0 else 1.0)

        return {
            'gainers': sorted_by_gain[:10],
            'losers': sorted(usdt_pairs, key=lambda x: x[1])[:10],
            'advance_count': advance_count,
            'decline_count': decline_count,
            'advance_decline_ratio': ad_ratio
        }

    def fetch_top_contracts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """按 24h 成交额获取 OKX USDT 永续主流合约。"""
        self.load_markets()
        tickers = self.exchange.fetch_tickers()
        rows = []
        for symbol, data in tickers.items():
            external = self._to_external_symbol(symbol)
            market = self._markets_cache.get(symbol) if self._markets_cache else None
            if market is None:
                try:
                    market = self.exchange.market(symbol)
                except Exception:
                    market = {}
            if not external.endswith('/USDT') or not market.get('swap'):
                continue
            last = data.get('last') or 0
            contract_size = float(market.get('contractSize') or 1)
            base_volume = data.get('baseVolume') or 0
            quote_volume = data.get('quoteVolume')
            if not quote_volume:
                quote_volume = float(base_volume or 0) * contract_size * float(last or 0)
            rows.append({
                'symbol': external,
                'base': external.split('/')[0],
                'price': last,
                'change_24h': data.get('percentage') or 0,
                'volume_24h': quote_volume,
                'high_24h': data.get('high') or last,
                'low_24h': data.get('low') or last
            })

        rows.sort(key=lambda item: item.get('volume_24h') or 0, reverse=True)
        return rows[:limit]

    # =========================================================================
    # 私有端点
    # =========================================================================

    def fetch_balance(self) -> Dict[str, float]:
        self._require_auth()
        balance = self.exchange.fetch_balance({'type': 'swap'})
        usdt = balance.get('USDT', {})
        return {
            'total': usdt.get('total', 0) or 0,
            'free': usdt.get('free', 0) or 0,
            'used': usdt.get('used', 0) or 0
        }

    def fetch_positions(self, symbols: List[str] = None) -> List[Dict]:
        self._require_auth()
        okx_symbols = [self._to_okx_symbol(s) for s in symbols] if symbols else None
        positions = self.exchange.fetch_positions(okx_symbols)
        active = []
        for pos in positions:
            contracts = abs(float(pos.get('contracts') or 0))
            if contracts != 0:
                active.append(self._format_position(pos))
        return active

    def _format_position(self, pos: Dict) -> Dict:
        raw_symbol = pos.get('symbol') or pos.get('info', {}).get('instId', '')
        symbol = self._to_external_symbol(raw_symbol)
        info = pos.get('info') or {}

        contracts = abs(float(pos.get('contracts') or pos.get('contractSize') or 0))
        raw_side = (pos.get('side') or pos.get('info', {}).get('posSide') or '').lower()
        if raw_side in ('long', 'buy'):
            position_side = 'LONG'
        elif raw_side in ('short', 'sell'):
            position_side = 'SHORT'
        else:
            position_side = 'LONG' if float(pos.get('contracts') or 0) > 0 else 'SHORT'

        notional = float(pos.get('notional') or 0)
        leverage = int(float(pos.get('leverage') or info.get('lever') or 1))
        initial_margin = self._safe_float(
            pos.get('initialMargin')
            or pos.get('collateral')
            or info.get('imr')
            or info.get('margin')
        )
        if initial_margin == 0 and leverage > 0:
            initial_margin = abs(notional) / leverage

        timestamp = pos.get('timestamp') or self._safe_float(info.get('uTime'))
        updated_at = None
        if timestamp:
            try:
                updated_at = int(float(timestamp))
            except (TypeError, ValueError):
                updated_at = None

        return {
            'symbol': symbol,
            'side': position_side,
            'contracts': contracts,
            'notional': notional,
            'entry_price': float(pos.get('entryPrice') or 0),
            'mark_price': float(pos.get('markPrice') or 0),
            'liquidation_price': self._safe_float(pos.get('liquidationPrice') or info.get('liqPx')),
            'unrealized_pnl': float(pos.get('unrealizedPnl') or 0),
            'realized_pnl': self._safe_float(info.get('realizedPnl')),
            'percentage': float(pos.get('percentage') or 0),
            'leverage': leverage,
            'margin_mode': info.get('mgnMode') or pos.get('marginMode') or 'cross',
            'initial_margin': initial_margin,
            'maintenance_margin': self._safe_float(pos.get('maintenanceMargin') or info.get('mmr')),
            'margin_ratio': self._safe_float(info.get('mgnRatio')),
            'updated_at': updated_at
        }

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value in (None, ''):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    # =========================================================================
    # 工具方法
    # =========================================================================

    def get_precision(self, symbol: str) -> Dict[str, Any]:
        market = self._market(symbol)
        precision = market.get('precision', {})
        return {
            'price': precision.get('price', 0.01),
            'amount': precision.get('amount', 1)
        }

    def get_fees(self, symbol: str) -> Dict[str, float]:
        market = self._market(symbol)
        taker = market.get('taker')
        maker = market.get('maker')
        return {
            'taker': float(taker if taker is not None else 0.0005),
            'maker': float(maker if maker is not None else 0.0002)
        }

    def truncate_to_precision(self, value: float, precision: Any) -> float:
        """兼容小数位精度和 OKX tick-size 精度。"""
        if isinstance(precision, int):
            multiplier = 10 ** precision
            return int(value * multiplier) / multiplier
        tick = float(precision or 1)
        if tick <= 0:
            return value
        return int(value / tick) * tick

    def get_min_notional(self, symbol: str) -> float:
        market = self._market(symbol)
        limits = market.get('limits', {})
        cost_limits = limits.get('cost', {}) or {}
        min_cost = cost_limits.get('min')
        if min_cost:
            return float(min_cost)

        amount_min = (limits.get('amount', {}) or {}).get('min') or 1
        contract_size = float(market.get('contractSize') or 1)
        ticker = self.fetch_ticker(symbol)
        return float(amount_min) * contract_size * ticker.last_price

    def calculate_quantity(self, symbol: str, usdt_amount: float, current_price: float = None) -> float:
        """按 OKX 合约张数计算下单数量。"""
        if current_price is None:
            ticker = self.fetch_ticker(symbol)
            current_price = ticker.last_price
        if current_price <= 0:
            raise ValueError(f"Invalid price for {symbol}: {current_price}")

        market = self._market(symbol)
        contract_size = float(market.get('contractSize') or 1)
        raw_contracts = usdt_amount / current_price / contract_size
        okx_symbol = self._to_okx_symbol(symbol)
        amount_text = self.exchange.amount_to_precision(okx_symbol, raw_contracts)
        return float(amount_text)

    def _price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.exchange.price_to_precision(self._to_okx_symbol(symbol), price))

    def get_position_size(self, symbol: str) -> Optional[Dict]:
        self._require_auth()
        for i in range(3):
            try:
                positions = self.fetch_positions([symbol])
                for pos in positions:
                    if pos['symbol'] == symbol:
                        return pos
            except Exception as e:
                logger.warning("尝试获取 OKX 持仓失败 (%d/3): %s", i + 1, e)
            if i < 2:
                time.sleep(1)
        return None

    # =========================================================================
    # 交易执行
    # =========================================================================

    def create_market_order(self, symbol: str, side: str, quantity: float, position_side: str) -> Dict:
        self._require_auth()
        okx_symbol = self._to_okx_symbol(symbol)
        pos_side = position_side.lower()
        params = {
            'marginMode': self.margin_mode,
            'tdMode': self.margin_mode,
            'positionSide': pos_side,
        }

        logger.info("正在创建 OKX 市价单: %s %s %.8f (posSide=%s)", side.upper(), symbol, quantity, pos_side)
        order = self.exchange.create_order(
            symbol=okx_symbol,
            type='market',
            side=side.lower(),
            amount=quantity,
            params=params
        )
        logger.info("OKX 订单已创建: %s", order.get('id'))
        return order

    def create_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        position_side: str
    ) -> Dict:
        self._require_auth()
        okx_symbol = self._to_okx_symbol(symbol)
        pos_side = position_side.lower()
        price = self._price_to_precision(symbol, price)
        params = {
            'marginMode': self.margin_mode,
            'tdMode': self.margin_mode,
            'positionSide': pos_side,
        }

        logger.info(
            "正在创建 OKX 限价单: %s %s %.8f @ %.8f (posSide=%s)",
            side.upper(), symbol, quantity, price, pos_side
        )
        order = self.exchange.create_order(
            symbol=okx_symbol,
            type='limit',
            side=side.lower(),
            amount=quantity,
            price=price,
            params=params
        )
        logger.info("OKX 限价单已创建: %s", order.get('id'))
        return order

    def create_stop_loss_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        position_side: str
    ) -> Dict:
        self._require_auth()
        stop_price = self._price_to_precision(symbol, stop_price)
        params = {
            'marginMode': self.margin_mode,
            'tdMode': self.margin_mode,
            'positionSide': position_side.lower(),
            'stopLossPrice': stop_price,
            'slOrdPx': '-1',
            'reduceOnly': True,
        }
        order = self.exchange.create_order(
            symbol=self._to_okx_symbol(symbol),
            type='conditional',
            side=side.lower(),
            amount=quantity,
            params=params
        )
        logger.info("OKX 止损单已创建: %s", order.get('id'))
        return order

    def create_take_profit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        take_profit_price: float,
        position_side: str
    ) -> Dict:
        self._require_auth()
        take_profit_price = self._price_to_precision(symbol, take_profit_price)
        params = {
            'marginMode': self.margin_mode,
            'tdMode': self.margin_mode,
            'positionSide': position_side.lower(),
            'takeProfitPrice': take_profit_price,
            'tpOrdPx': '-1',
            'reduceOnly': True,
        }
        order = self.exchange.create_order(
            symbol=self._to_okx_symbol(symbol),
            type='conditional',
            side=side.lower(),
            amount=quantity,
            params=params
        )
        logger.info("OKX 止盈单已创建: %s", order.get('id'))
        return order

    def set_leverage(self, symbol: str, leverage: int) -> Dict:
        self._require_auth()
        leverage = max(1, min(125, leverage))
        okx_symbol = self._to_okx_symbol(symbol)
        results = []
        for pos_side in ('long', 'short'):
            try:
                result = self.exchange.set_leverage(
                    leverage,
                    okx_symbol,
                    {'marginMode': self.margin_mode, 'posSide': pos_side}
                )
                results.append(result)
            except Exception as e:
                logger.warning("设置 OKX %s %s 杠杆失败: %s", symbol, pos_side, e)
                raise
        return {'results': results}

    def set_margin_mode(self, symbol: str, mode: str) -> Dict:
        self._require_auth()
        mode = mode.lower()
        if mode not in ('cross', 'isolated'):
            raise ValueError(f"无效的保证金模式: {mode}")
        self.margin_mode = mode
        try:
            return self.exchange.set_margin_mode(mode, self._to_okx_symbol(symbol))
        except Exception as e:
            if 'not modified' in str(e).lower() or 'no need' in str(e).lower():
                return {'info': 'already_set'}
            raise

    # =========================================================================
    # 挂单与算法单
    # =========================================================================

    def _fetch_algo_orders(self, symbol: str = None) -> List[Dict]:
        self._require_auth()
        params = {'ordType': 'conditional'}
        if symbol:
            params['instId'] = self._market(symbol)['id']
        response = self.exchange.privateGetTradeOrdersAlgoPending(params)
        return response.get('data', []) if isinstance(response, dict) else []

    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        self._require_auth()
        all_orders = []

        try:
            orders = self.exchange.fetch_open_orders(self._to_okx_symbol(symbol)) if symbol else self.exchange.fetch_open_orders()
            for order in orders:
                order['symbol'] = self._to_external_symbol(order.get('symbol', ''))
            all_orders.extend(orders)
        except Exception as e:
            logger.warning("获取 OKX 普通挂单失败: %s", e)

        try:
            for order in self._fetch_algo_orders(symbol):
                inst_id = order.get('instId', '')
                external_symbol = symbol or self._to_external_symbol(inst_id)
                all_orders.append({
                    'id': str(order.get('algoId')),
                    'symbol': external_symbol,
                    'type': order.get('ordType') or 'conditional',
                    'side': order.get('side'),
                    'amount': float(order.get('sz') or 0),
                    'stopPrice': float(order.get('slTriggerPx') or order.get('tpTriggerPx') or order.get('triggerPx') or 0),
                    'status': order.get('state'),
                    'is_algo': True,
                    'info': order
                })
        except Exception as e:
            logger.debug("获取 OKX 条件委托失败: %s", e)

        return all_orders

    def cancel_all_orders(self, symbol: str) -> List[Dict]:
        self._require_auth()
        cancelled = []
        okx_symbol = self._to_okx_symbol(symbol)

        try:
            for order in self.exchange.fetch_open_orders(okx_symbol):
                self.exchange.cancel_order(order['id'], okx_symbol)
                cancelled.append(order)
        except Exception as e:
            logger.warning("取消 OKX 普通订单失败: %s", e)

        try:
            algo_orders = self._fetch_algo_orders(symbol)
            if algo_orders:
                payload = [{
                    'instId': order.get('instId'),
                    'algoId': order.get('algoId')
                } for order in algo_orders if order.get('algoId')]
                result = self.exchange.privatePostTradeCancelAlgos(payload)
                cancelled.extend(algo_orders)
                logger.info("已取消 OKX 条件委托: %s", result)
        except Exception as e:
            logger.warning("取消 OKX 条件委托失败: %s", e)

        return cancelled

    def cancel_order_by_id(self, symbol: str, order_id: str) -> Dict:
        self._require_auth()
        okx_symbol = self._to_okx_symbol(symbol)
        try:
            result = self.exchange.cancel_order(order_id, okx_symbol)
            return {'success': True, 'order_id': order_id, 'type': 'normal', 'result': result}
        except Exception as e:
            logger.debug("OKX 普通订单取消失败，尝试条件委托: %s", e)

        try:
            inst_id = self._market(symbol)['id']
            result = self.exchange.privatePostTradeCancelAlgos([{
                'instId': inst_id,
                'algoId': order_id
            }])
            return {'success': True, 'order_id': order_id, 'type': 'algo', 'result': result}
        except Exception as e:
            return {'success': False, 'order_id': order_id, 'error': str(e)}

    def cancel_orders_by_type(self, symbol: str, order_type: str) -> List[Dict]:
        self._require_auth()
        if order_type.lower() == 'all':
            return self.cancel_all_orders(symbol)

        orders = self.get_open_orders(symbol)
        cancelled = []
        for order in orders:
            info = order.get('info') or {}
            has_sl = bool(info.get('slTriggerPx')) or 'STOP' in str(order.get('type', '')).upper()
            has_tp = bool(info.get('tpTriggerPx')) or 'TAKE_PROFIT' in str(order.get('type', '')).upper()
            matched = (order_type == 'stop_loss' and has_sl) or (order_type == 'take_profit' and has_tp)
            if not matched:
                continue
            result = self.cancel_order_by_id(symbol, str(order.get('id')))
            if result.get('success'):
                cancelled.append(order)
        return cancelled
