"""
交易服务 - 管理机器人生命周期。

实现了分离原则，将线程管理和引擎协调与 HTTP 接口隔离开来。
"""

import logging
import time
from threading import Thread
from typing import Optional, Dict

from app import create_app  # Import create_app
from app.bot.engine import TradingEngine
from config import get_config

logger = logging.getLogger(__name__)


class TradingService:
    """
    交易机器人的服务层。
    
    管理：
    - TradingEngine 实例
    - 后台交易循环线程
    - 机器人状态
    """
    
    def __init__(self, engine: TradingEngine):
        """
        初始化服务。
        
        Args:
            engine: 已配置的 TradingEngine 实例
        """
        self.engine = engine
        self._is_running = False
        self._thread: Optional[Thread] = None
        self._last_cycle_started_at: Optional[float] = None
        self._last_cycle_finished_at: Optional[float] = None
        self._next_run_at: Optional[float] = None
        self._cycle_in_progress = False
    
    @property
    def is_running(self) -> bool:
        """检查交易循环是否在运行。"""
        return self._is_running
    
    @property
    def live_trading(self) -> bool:
        """检查是否启用了实盘交易。"""
        return self.engine.live_trading
    
    def start(self):
        """启动后台交易循环。"""
        if self._is_running:
            raise RuntimeError("机器人已在运行")
        
        # Explicitly sync time before starting
        logger.info("正在与 OKX 同步时间...")
        self.engine.data_engine.exchange.synchronize_time()
        
        self._is_running = True
        self._next_run_at = time.time()
        self._thread = Thread(target=self._trading_loop, daemon=True)
        self._thread.start()
        logger.info("交易服务已启动")
    
    def stop(self):
        """停止后台交易循环。"""
        if not self._is_running:
            raise RuntimeError("机器人未运行")
        
        self._is_running = False
        self._next_run_at = None
        # 线程将在循环检查或睡眠后自然退出
        logger.info("正在停止交易服务...")
    
    def run_once(self) -> dict:
        """立即运行一个交易循环。"""
        self._cycle_in_progress = True
        self._last_cycle_started_at = time.time()
        try:
            return self.engine.run_cycle()
        finally:
            self._last_cycle_finished_at = time.time()
            self._cycle_in_progress = False
    
    def enable_live_trading(self, enable: bool):
        """启用或禁用实盘交易。"""
        self.engine.enable_live_trading(enable)
    
    def set_custom_instructions(self, instructions: str):
        """更新自定义指令。"""
        self.engine.set_custom_instructions(instructions)
    
    def get_status(self) -> Dict:
        """获取全面的机器人状态。"""
        status = {
            'running': self._is_running,
            'live_trading': self.engine.live_trading,
            'timestamp': time.time(),
            'cycle_in_progress': self._cycle_in_progress,
            'last_cycle_started_at': self._last_cycle_started_at,
            'last_cycle_finished_at': self._last_cycle_finished_at,
            'next_run_at': self._next_run_at,
            'interval_seconds': get_config().TRADING_INTERVAL_MINUTES * 60
        }
        status.update(self.engine.get_status())
        return status
    
    def _trading_loop(self):
        """内部后台循环。"""
        config = get_config()
        interval = config.TRADING_INTERVAL_MINUTES * 60
        
        logger.info("交易循环已激活 (间隔: %d 秒)", interval)
        
        # 在线程中创建应用上下文以访问数据库
        app = create_app(config)
        
        while self._is_running:
            with app.app_context():
                try:
                    self._cycle_in_progress = True
                    self._last_cycle_started_at = time.time()
                    self._next_run_at = None
                    result = self.engine.run_cycle()
                    logger.info("循环成功: %s", result.get('success'))
                except Exception as e:
                    logger.error("循环错误: %s", e)
                finally:
                    self._last_cycle_finished_at = time.time()
                    self._cycle_in_progress = False
            
            if self._is_running:
                self._next_run_at = time.time() + interval

            # 可中断的睡眠
            for _ in range(interval):
                if not self._is_running:
                    break
                time.sleep(1)
        
        self._next_run_at = None
        self._cycle_in_progress = False
        
        logger.info("交易循环已结束")
