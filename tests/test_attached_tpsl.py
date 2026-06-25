import unittest
from dataclasses import dataclass

from app.bot.executor import TradeExecutor
from app.bot.okx_client import OKXClient


@dataclass
class FakeTicker:
    last_price: float


class FakeTradingClient:
    def __init__(self):
        self.calls = []

    def fetch_ticker(self, symbol):
        return FakeTicker(last_price=100.0)

    def fetch_balance(self):
        return {'free': 10.0}

    def get_min_notional(self, symbol):
        return 1.0

    def calculate_quantity(self, symbol, amount_usdt, current_price=None):
        return 1.23

    def create_market_order(self, *args, **kwargs):
        self.calls.append(('market', args, kwargs))
        return {'id': 'order-1', 'average': 100.0}

    def create_limit_order(self, *args, **kwargs):
        self.calls.append(('limit', args, kwargs))
        return {'id': 'order-2', 'price': args[3]}


class FakeExchange:
    def __init__(self):
        self.calls = []

    def create_order(self, **kwargs):
        self.calls.append(kwargs)
        return {'id': 'order-1', 'average': 100.0}


class AttachedTpSlTest(unittest.TestCase):
    def test_open_position_rejects_missing_stop_loss(self):
        client = FakeTradingClient()
        result = TradeExecutor(client).open_position(
            'BTC/USDT',
            'LONG',
            20,
            order_type='market'
        )

        self.assertFalse(result.success)
        self.assertIn('stop_loss_price', result.error)
        self.assertEqual(client.calls, [])

    def test_open_position_passes_stop_loss_to_entry_order(self):
        client = FakeTradingClient()
        result = TradeExecutor(client).open_position(
            'BTC/USDT',
            'LONG',
            20,
            stop_loss_price=95,
            take_profit_price=110,
            order_type='market'
        )

        self.assertTrue(result.success)
        self.assertEqual(len(client.calls), 1)
        call_type, args, kwargs = client.calls[0]
        self.assertEqual(call_type, 'market')
        self.assertEqual(args, ('BTC/USDT', 'BUY', 1.23, 'LONG'))
        self.assertEqual(kwargs['stop_loss_price'], 95)
        self.assertEqual(kwargs['take_profit_price'], 110)

    def test_okx_client_builds_attached_stop_loss_params(self):
        client = OKXClient(api_key='x', api_secret='y', passphrase='z')
        client._require_auth = lambda: None
        client._to_okx_symbol = lambda symbol: f'{symbol}:USDT' if ':' not in symbol else symbol
        client._price_to_precision = lambda symbol, price: str(price)
        client.exchange = FakeExchange()

        client.create_market_order(
            'BTC/USDT',
            'BUY',
            1.23,
            'LONG',
            stop_loss_price=95,
            take_profit_price=110
        )

        params = client.exchange.calls[0]['params']
        self.assertEqual(client.exchange.calls[0]['type'], 'market')
        self.assertEqual(params['positionSide'], 'long')
        self.assertEqual(params['stopLoss']['triggerPrice'], '95')
        self.assertEqual(params['stopLoss']['type'], 'market')
        self.assertEqual(params['takeProfit']['triggerPrice'], '110')
        self.assertEqual(params['takeProfit']['type'], 'market')


if __name__ == '__main__':
    unittest.main()
