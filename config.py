"""
OpenNOF1 的配置管理。

从环境变量加载设置，并提供合理的默认值。
遵循 12-Factor App 方法论。
"""

import os
import re
from dotenv import load_dotenv

# 加载 .env 文件 (如果存在)
load_dotenv()


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _normalize_ai_provider(item: dict, index: int) -> dict:
    """规范化 AI 提供商配置，避免在业务代码里处理多种字段名。"""
    return {
        'name': str(item.get('name') or f'provider{index}').strip(),
        'api_key': str(item.get('api_key') or item.get('apiKey') or '').strip(),
        'base_url': str(item.get('base_url') or item.get('baseUrl') or item.get('url') or '').strip(),
        'model': str(item.get('model') or '').strip()
    }


def _provider_env_prefix(name: str) -> str:
    """将供应商名转换为环境变量后缀。"""
    return re.sub(r'[^A-Z0-9]+', '_', name.upper()).strip('_')


def _parse_ai_providers_from_named_env() -> list:
    """
    从命名块读取 AI 供应商配置。

    示例：
    AI_PROVIDER_ORDER=deepseek,openai
    AI_PROVIDER_DEEPSEEK_API_KEY=...
    AI_PROVIDER_DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
    AI_PROVIDER_DEEPSEEK_MODEL=deepseek-chat
    """
    order = [
        item.strip()
        for item in os.getenv('AI_PROVIDER_ORDER', '').split(',')
        if item.strip()
    ]
    if not order:
        return []

    providers = []
    for idx, name in enumerate(order, start=1):
        suffix = _provider_env_prefix(name)
        providers.append(_normalize_ai_provider({
            'name': name,
            'api_key': os.getenv(f'AI_PROVIDER_{suffix}_API_KEY', ''),
            'base_url': os.getenv(f'AI_PROVIDER_{suffix}_BASE_URL', ''),
            'model': os.getenv(f'AI_PROVIDER_{suffix}_MODEL', '')
        }, idx))
    return providers


def _load_ai_provider_configs() -> list:
    """读取 AI_PROVIDER_ORDER + AI_PROVIDER_<NAME>_*，顺序就是首选/备选顺序。"""
    return _parse_ai_providers_from_named_env()


class Config:
    """基础配置。"""
    
    # Flask 配置
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-prod')
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    
    # 数据库 - 开发环境使用 SQLite 回退
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        # SQLite 回退
        SQLALCHEMY_DATABASE_URI = 'sqlite:///opennof1.db'
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # OKX API
    OKX_API_KEY = os.getenv('OKX_API_KEY', '')
    OKX_API_SECRET = os.getenv('OKX_API_SECRET', '')
    OKX_API_PASSPHRASE = os.getenv('OKX_API_PASSPHRASE', '')
    OKX_MARGIN_MODE = os.getenv('OKX_MARGIN_MODE', 'cross').lower()
    
    # AI 提供商列表。使用 AI_PROVIDER_ORDER + AI_PROVIDER_<NAME>_*，按顺序故障转移。
    AI_PROVIDER_CONFIGS = _load_ai_provider_configs()

    # Bark 通知配置。BARK_URL 可直接填 https://api.day.app/<device_key>；
    # 也可使用 BARK_SERVER_URL + BARK_DEVICE_KEY 组合。
    BARK_ENABLED = _env_bool('BARK_ENABLED', False)
    BARK_URL = os.getenv('BARK_URL', '').strip()
    BARK_SERVER_URL = os.getenv('BARK_SERVER_URL', 'https://api.day.app').strip()
    BARK_DEVICE_KEY = os.getenv('BARK_DEVICE_KEY', '').strip()
    BARK_GROUP = os.getenv('BARK_GROUP', 'OpenNOF1').strip()
    BARK_LEVEL = os.getenv('BARK_LEVEL', 'active').strip()
    BARK_SOUND = os.getenv('BARK_SOUND', '').strip()
    BARK_ICON = os.getenv('BARK_ICON', '').strip()
    BARK_OPEN_URL = os.getenv('BARK_OPEN_URL', '').strip()
    BARK_TIMEOUT_SECONDS = _env_int('BARK_TIMEOUT_SECONDS', 8)
    BARK_MAX_BODY_CHARS = _env_int('BARK_MAX_BODY_CHARS', 3500)
    
    # 交易配置
    TRADING_SYMBOLS = os.getenv(
        'TRADING_SYMBOLS', 
        'BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,DOGE/USDT'
    ).split(',')
    
    TRADING_INTERVAL_MINUTES = int(os.getenv('TRADING_INTERVAL_MINUTES', '3'))
    
    # 要获取的 OHLCV 时间周期
    TIMEFRAMES = ['1m', '15m', '1h', '4h', '1d']
    
    # 每个时间周期获取的 K 线数量
    CANDLE_LIMIT = 300
    
    # AI Prompt 中显示的 K 线数量 (每个时间周期)
    KLINE_DISPLAY_LIMIT = int(os.getenv('KLINE_DISPLAY_LIMIT', '100'))
    
    # 控制台密码 (用于设置页)
    CONSOLE_PASSWORD = os.getenv('CONSOLE_PASSWORD', 'admin')
    
    # 时区设置 (格式: "+8", "-5" 等，范围 +14 至 -12)
    _tz_str = os.getenv('TIMEZONE', '+8')
    try:
        TIMEZONE_OFFSET = int(_tz_str.replace('+', ''))
        if not -12 <= TIMEZONE_OFFSET <= 14:
            TIMEZONE_OFFSET = 8  # 默认 UTC+8
    except ValueError:
        TIMEZONE_OFFSET = 8  # 解析失败使用默认值


class DevelopmentConfig(Config):
    """开发环境配置。"""
    DEBUG = True


class ProductionConfig(Config):
    """生产环境配置。"""
    DEBUG = False


# 配置选择器
config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
}

def get_config():
    """根据环境获取配置。"""
    env = os.getenv('FLASK_ENV', 'development')
    return config_map.get(env, DevelopmentConfig)
