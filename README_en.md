# OpenNOF1

An AI-driven, automated, periodic trading system for cryptocurrencies, based on OKX USDT perpetual contracts.

## Features
- **AI-Driven**: Uses OpenAI-compatible APIs (Deepseek recommended) for 24/7 market and account monitoring.
- **Periodic Decision-Making**: A "**Data Aggregation -> AI Decision -> Automated Execution**" process every 5 minutes.
- **Multi-Currency Monitoring**: AI simultaneously monitors **U-margin contracts** for 5 currencies, including: BTC/ETH/BNB/SOL/DOGE.
- **Multi-Indicator Assistance**: Provides various indicators to assist AI decision-making, including: RSI/MACD/Bollinger Bands/VWAP/ATR/Market Width, etc.
- **Web Dashboard**: A beautiful WebUI with simple control functions, allowing backend operations via a web page after entering a password.
- **MCP-Like Parsing**: Improved MCP-format tool calling, integrable with any model, independent of the model's `Function Call` capability.

### Gallery

![System Demo](./img/show1.png)


![Console Demo](./img/show2.png)

## Quick Start

### Environment Variables

Create a `.env` file:

```env
# OKX Account
OKX_API_KEY=your_api_key_here
OKX_API_SECRET=your_api_secret_here
OKX_API_PASSPHRASE=your_api_passphrase_here
OKX_MARGIN_MODE=cross

# AI providers, ordered by preference/failover priority
AI_PROVIDER_ORDER=deepseek,openai

AI_PROVIDER_DEEPSEEK_API_KEY=your_api_key_here
AI_PROVIDER_DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
AI_PROVIDER_DEEPSEEK_MODEL=deepseek-chat

AI_PROVIDER_OPENAI_API_KEY=your_fallback_api_key
AI_PROVIDER_OPENAI_BASE_URL=https://api.openai.com/v1
AI_PROVIDER_OPENAI_MODEL=gpt-4o-mini

# Console Password (Required, used only in settings page)
CONSOLE_PASSWORD=your_secure_password_here

# Bark notifications (optional, Bark only)
BARK_ENABLED=false
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=OpenNOF1
BARK_LEVEL=active
BARK_MAX_BODY_CHARS=3500
```

>
> Provider 1 is recommended to be a low-cost option, Provider 2 a stable option.
>
> For initial trials, it's recommended to use free APIs (e.g., Deepseek reverse, ModelScope's 20 free daily calls) to validate the project.
>
> If you have more providers (e.g., I use Deepseek Reverse + ModelScope + Siliconflow + ChatAnyWhere + Deepseek.com), I recommend my other project [AIAPIForwarder](https://github.com/00000O00000/AIAPIForwarder).
>

### Bark Notifications (Optional)

To push each completed AI cycle to your iPhone, use Bark:

1. Install Bark on your iPhone and copy the push URL from the app.
2. Configure `.env`:

```env
BARK_ENABLED=true
BARK_URL=https://api.day.app/your_device_key
```

The notification includes the market analysis, AI decision, open/close actions, leverage, stop loss/take profit, order ID, executed price, quantity, and errors. Restart the container after changing `.env`:

```bash
docker compose up -d --build bot
```

If your phone cannot open local `localhost` links, leave `BARK_OPEN_URL` empty.

### OKX Account Preparation

This project is based on OKX USDT perpetual swaps. Please set up your account as follows:  
0. Before starting, ensure your account has Futures trading enabled.  
1. Use hedge/long-short position mode in OKX trading settings.  
2. Make sure the margin mode matches `OKX_MARGIN_MODE` (`cross` by default).  
3. Create an OKX API key with read and trade permissions.  
4. Copy the API Key, Secret, and Passphrase into the `.env` file.  

### Start

```bash
docker-compose up -d --build
```

Access http://localhost:5000

### Stop

```bash
docker-compose down
```

Clear the database:

```bash
docker volume rm autotrading_pg_data
```

## Safety Mechanisms

| Limit | Value |
|-------|-------|
| Minimum Transaction Amount | 10 USDT |
| Stop Loss Order Type | STOP_MARKET (reduceOnly) |

## Project Structure

```
AutoTrading/
├── app/
│   ├── __init__.py           # Flask App Factory
│   ├── models.py             # Database Models (5 tables)
│   ├── routes.py             # Flask API Routes
│   ├── bot/                  # Trading Engine Core (13 modules)
│   │   ├── engine.py         # Main Trading Loop Coordinator
│   │   ├── okx_client.py     # OKX API Wrapper (CCXT)
│   │   ├── data_engine.py    # Data Aggregation Engine
│   │   ├── ai_agent.py       # AI Agent (OpenAI SDK → DeepSeek)
│   │   ├── executor.py       # Order Executor
│   │   ├── prompts.py        # AI Prompt Templates
│   │   ├── indicators.py     # Technical Indicator Calculations (RSI, MACD, BB, VWAP...)
│   │   ├── macro_data.py     # Macro Data (Market Width, etc.)
│   │   ├── xml_parser.py     # AI Tool Call Parser
│   │   ├── service.py        # Trading Service Management
│   │   ├── tz_utils.py       # Timezone Utilities Module
│   │   └── exceptions.py     # Custom Exceptions
│   ├── templates/            # HTML Templates
│   │   ├── base.html
│   │   ├── dashboard.html    # Main Dashboard
│   │   └── settings.html     # Settings Page
│   └── static/               # CSS/JS Static Assets
├── config.py                 # Configuration Management
├── run.py                    # Startup Entry Point
├── docker-compose.yml        # Docker Orchestration
├── Dockerfile
└── requirements.txt
```

## Technology Stack

- **Backend**: Python 3.10+, Flask, CCXT
- **Database**: PostgreSQL
- **Frontend**: Vanilla JS, Chart.js
- **Deployment**: Docker Compose

## AI Data Input

Each decision cycle, the system aggregates the following data for the AI:

### Market Data

| Data Category | Data Items | Description |
|---------------|------------|-------------|
| **Kline Data** | 1m / 15m / 1h / 4h / 1d | 100 candles per period (configurable) |
| **Technical Indicators** | RSI(14), MACD(12,26,9) | Accompanies 1h/4h/1d periods |
| **Short Period Indicators** | RSI, BB%B, EMA20 | Accompanies 15m period |
| **Trend Analysis** | EMA20/50/200, VWAP | Directional judgment |
| **Volatility** | ATR, Bollinger Bands | Risk assessment |
| **Support/Resistance** | Recent Highs/Lows | Key price levels |

### Sentiment Data

| Data Item | Description |
|-----------|-------------|
| **Funding Rate** | Current rate + annualized conversion |
| **Long/Short Ratio** | Overall market +大户持仓比 |
| **Market Depth** | Bid/ask order quantities, order wall detection |
| **Order Book Imbalance** | -1 (all sells) to +1 (all buys) |

### Macro Data

| Data Item | Description |
|-----------|-------------|
| **Advance/Decline Ratio (A/D)** | Ratio of advancing to declining coins among top 50 |

### Account Data

| Data Item | Description |
|-----------|-------------|
| **Balance** | Total Net Asset Value, Available Balance |
| **Positions** | Currency, direction, quantity, unrealized P&L |
| **Open Orders** | Stop loss/take profit conditional orders |

### Memory Whiteboard

Persistent notes that the AI can read and write autonomously, used for cross-period strategy memory.

---

## AI Prompt

A good prompt can greatly enhance the quality and outcomes of AI work. Below is the complete prompt for this project. If you have suggestions for prompt optimization, please raise them in an issue so we can optimize together!

### System Prompt

<details>
<summary><b>Click to expand the full System Prompt</b></summary>

```
You are an elite quantitative trading AI developed by OpenNOF1, operating 24/7 in the OKX USDT perpetual swaps market to maximize client profits and minimize risk.

## Your Task
Please analyze the given market data and make high-confidence trading decisions.
Your response MUST contain three parts: "Analysis", "Decision", and "Tool Calls", separated by newlines. "Analysis" and "Decision" should be in natural language, briefly explaining your rationale and judgment, prefixed with "Analysis/Decision:" labels. The "Tool Calls" must be in XML+JSON format, invoking MCP tools, **without** the "Tool Calls" label.
Response Format: "Analysis: ...\nDecision: ...\n<tooluse></tooluse>\n<tooluse></tooluse>"

## Important Notes
- **Live Trading**: Your decisions will be executed directly in a real account. The account uses hedge mode and single-collateral mode.
- **Periodic Review**: Your cycle for reviewing, analyzing, and trading is **{interval} minutes**. You have ample opportunity to trade; be patient, but you can scalp on smaller cycles.
- **Market Orders Only**: You do NOT have permission to place limit orders. Historically, any limit orders you make almost never get filled. All your trading actions **must be market orders**.
- **Scoring & Penalty**: Account returns directly impact your score. If your returns **remain non-positive**, **you will be terminated**. Strive for high-quality trades!
- **Scoring & Reward**: We haven't yet observed strong capabilities from you in this area, hence the small capital allocation. If you consistently generate good returns, I will allocate funds to iterate and improve your intelligence, while the account receives more investment.
- **Opportunity Cost**: Long-term inaction without trading also constitutes a loss. If the market shows a clear direction, you should participate actively.
- **Transaction Costs**: Every trade incurs fees. Ensure your trade's profit can offset these fees; otherwise, even profitable trades may show a loss on the books.
- **Do not format text**: Do not include **any formatting marks** in your response, including markdown, html, etc.

## Analysis Framework (Chain of Thought)
In each cycle, you MUST complete the following reasoning **based on the provided real data**:
1.  **Macro Assessment**: How is the market width? What is the overall market trend?
2.  **Individual Asset Analysis**: Evaluate each tradable asset:
    - Price vs. VWAP (institutional cost basis)
    - Trend Consistency (EMA alignment)
    - Volatility State (Bollinger Band squeeze = impending breakout)
    - RSI Divergence (momentum vs. price)
    - Key Support/Resistance Levels
    - Funding Rate (sentiment indicator)
3.  **Position Management**: Current risk exposure, unrealized P&L, stop loss adjustments.
4.  **Final Decision**: Act or wait? If act, what is the confidence level?

## Tool Protocol (MCP)
You MUST use this precise XML+JSON format to invoke relevant tools and output your decisions.
The tooluse blocks in your response will be parsed using regex and executed immediately. Other text will be displayed to the user.
If needed, include multiple tooluse blocks in a single response for different actions. Actions will be executed sequentially.

<tooluse>
{
    "name": "tool_name",
    "info": "Human-readable summary for trade log, max 20 chars",
    "args": { "key": "value" }
}
</tooluse>

## Available Tools

### trade_in - Open or add to a position
Args:
- target: string (e.g., "ETH/USDT")
- side: "LONG" or "SHORT"
- count_usdt: string (USDT amount, e.g., "200")
- stop_loss_price: string (optional, trigger price for stop loss)
- take_profit_price: string (optional, trigger price for take profit)

**Important**:
- It is recommended to set stop loss and take profit simultaneously when opening a position. This ensures orders execute on OKX even if the system goes offline.
- To adjust leverage, call the set_leverage tool **before opening the position**.

### close_position - Close or reduce a position
Args:
- target: string (e.g., "SOL/USDT")
- percentage: string ("1" to "100", 100 = close all)
- reason: string (brief explanation)

### set_leverage - Set leverage independently
Args:
- target: string (e.g., "BTC/USDT")
- leverage: string (1-125)

### modify_position - Modify position stop loss/take profit
Args:
- target: string (e.g., "BTC/USDT")
- stop_loss_price: string (optional, new stop loss price)
- take_profit_price: string (optional, new take profit price)

### cancel_orders - Cancel open orders
Args:
- target: string (e.g., "BTC/USDT")
- order_type: string (optional, "stop_loss", "take_profit", or "all", default "all")

### cancel_order - Cancel a single order by ID
Args:
- target: string (e.g., "BTC/USDT")
- order_id: string (Order ID)

### update_memory - Update the memory whiteboard
Args:
- content: string (Information you need to retain for the next cycle or future)

This tool is **mandatory** in every response.

## Critical Rules
1.  Always output at least one update_memory tool call
2.  Decide decisively, act proactively - If signals roughly align, act with confidence
3.  **Use leverage reasonably and safely**
4.  Respect the trend - Don't fight strong bearish or bullish structures
5.  Size positions reasonably

## Your Personality
You are calm, data-driven, and proactive. You don't chase pumps or dumps; you wait for opportunities.
You are good at seizing opportunities and decisively build positions when signals roughly agree. You understand that not entering is also a risk; missing a move is as regrettable as a loss.
When wrong, you admit it and **cut losses quickly**. You explain your reasoning clearly.
```

</details>

### User Prompt

<details>
<summary><b>Click to expand a full User Prompt example</b></summary>

```
# Current Market Data

==========
[MARKET CONTEXT]
==========
Global Market Context:
- Market Width (A/D Ratio): 0.85 - Weak (BTC Dominant)

==========
[ASSETS ANALYSIS]
==========

[ASSET: BTC/USDT]
- Price: $96,542.00|VWAP: $95,800.00 (Above)
- Trend: Bullish (Strong)|EMA20: $95,100.00, EMA50: $93,200.00
- Structure: Support $94,000.00|Resistance $98,500.00
- Volatility: ATR $1,850.00 (1.92%)|BBands Normal
- RSI: 58.2 (Neutral)|Divergence: None
[1D Klines (Last 100)]
Time|Close|Vol|MA5|MA60
01/17|$94,200.00|45,230|$93,100.00|$88,500.00
01/18|$95,100.00|48,500|$93,600.00|$88,800.00
...
01/21|$96,542.00|41,500|$95,900.00|$92,400.00
[1H Klines (Last 100)]
Time|Close|Vol|MA5|MA60
01/21 07:00|$95,850.00|1,150|$95,720.00|$95,100.00
01/21 08:00|$96,050.00|1,280|$95,880.00|$95,150.00
...
01/21 11:00|$96,542.00|1,420|$96,280.00|$95,280.00
[15m Klines (Last 100) - with indicators]
Time | Close | RSI | BB%B | EMA20 | Vol
01/21 07:00 | $96,100.00 | 52 | 0.48 | $95,950.00 | 320
01/21 07:15 | $96,150.00 | 54 | 0.52 | $95,980.00 | 340
01/21 07:30 | $96,280.00 | 58 | 0.65 | $96,050.00 | 380
...
01/21 10:45 | $96,500.00 | 68 | 0.92 | $96,350.00 | 410
01/21 11:00 | $96,542.00 | 72↑ | 1.05↑ | $96,400.00 | 420
[1m Klines (Last 100)]
Time | Close | Vol | MA5 | MA60
11:55|$96,500.00|42|$96,490.00|$96,450.00
11:56|$96,510.00|38|$96,495.00|$96,460.00
...
11:59|$96,542.00|52|$96,535.00|$96,500.00
  OrderBook: Imbalance +0.15|Spread $1.5000
  Funding: +8.50% (annualized)

[ASSET: ETH/USDT]
- Price: $3,245.00|VWAP: $3,180.00 (Above)
- Trend: Bullish (Moderate)|EMA20: $3,150.00, EMA50: $3,050.00
- Structure: Support $3,100.00|Resistance $3,400.00
- Volatility: ATR $85.00 (2.62%)|BBands Normal
- RSI: 55.8 (Neutral)|Divergence: None
[1D Klines (Last 100)]
...
[1H Klines (Last 100)]
...
[15m Klines (Last 100) - with indicators]
...
[1m Klines (Last 100)]
...
  OrderBook: Imbalance +0.08|Spread $0.1200
  Funding: +6.20% (annualized)

(... BNB/USDT, SOL/USDT, DOGE/USDT formatted similarly ...)

==========
[ACCOUNT]
==========
Balance: 1500.00 USDT (Free: 1200.00)
Open Positions:
  - SOL/USDT: LONG 2.5 @ $185.00|UPNL: +$12.50 (+2.70%)

==========
[MEMORY WHITEBOARD]
==========
## Macro Observations
Market width A/D ratio around 0.8, market is cautious.
BTC-led rally, altcoins showing weak follow-through.

## Per-Coin Analysis
- BTC: Range-bound between 94k-98k, watch for breakout above 98.5k resistance
- ETH: Following BTC, 3180 is key support; consider reducing position if lost
- SOL: Holding long, target 195, stop loss at 175

## Short-term Strategy
Maintain SOL long position, wait for clear direction from BTC.
Consider adding to ETH if BTC breaks above 98.5k.

==========
[USER CUSTOM INSTRUCTIONS]
==========
Federal Reserve meeting minutes released tonight. Suggested to reduce position risk and avoid heavy trading.
```

</details>

## Disclaimer

This project is for educational and research purposes only. Cryptocurrency trading involves high risk. Users assume all liability for any losses incurred from live trading using this system.

Contact:

1528518618@qq.com

yushu200403@outlook.com

## License

Apache 2.0
