#!/usr/bin/env python3
"""
Market display script for pushing small PNG charts to Zectrix device.
Reads API key and device id from environment variables:
  - ZECTRIX_API_KEY
  - DEVICE_ID
Optional:
  - ZECTRIX_URL  (overrides default URL format)
"""
import io
import os
import sys
import logging
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import requests
import yfinance as yf
import argparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ==================== 配置参数 ====================
API_KEY = os.environ.get("ZECTRIX_API_KEY")
DEVICE_ID = os.environ.get("DEVICE_ID")
ZECTRIX_URL = os.environ.get("ZECTRIX_URL") or ("https://cloud.zectrix.com/open/v1/devices/{}/display/image".format(DEVICE_ID) if DEVICE_ID else None)

TIME_SEGMENTS = [
    {
        "name": "Premarket",
        "ticker": "NQ=F",
        "display_name": "NASDAQ FUTURES",
        "start_time": "04:00",
        "end_time": "13:30",
        "xticks": [0, 150, 285],
        "xticklabels": ["00:00", "05:00", "09:30"],
        "output_file": "nq_trend.png"
    },
    {
        "name": "Regular",
        "ticker": "^NDX",
        "display_name": "NASDAQ 100",
        "start_time": "13:30",
        "end_time": "20:11",
        "xticks": [0, 105, 195],
        "xticklabels": ["09:30", "13:00", "16:00"],
        "output_file": "ndx_trend.png"
    }
]


def fetch_market_data(ticker):
    """获取市场数据（2 分钟分辨率，当日）"""
    try:
        ticker_obj = yf.Ticker(ticker)
        df = ticker_obj.history(period="1d", interval="2m")

        if df.empty:
            logging.warning("No data returned for %s", ticker)
            return None, None, None

        previous_close = (
            ticker_obj.info.get('previousClose') or
            ticker_obj.info.get('regularMarketPreviousClose') or
            float(df['Open'].iloc[0])
        )

        return df['Close'].values, ticker_obj, previous_close
    except Exception as e:
        logging.exception("Failed to fetch data for %s: %s", ticker, e)
        return None, None, None


def create_chart(config, prices, previous_close, current_price, pct_change, price_change):
    """创建图表并返回 matplotlib Figure"""
    fig, (ax_text, ax_chart) = plt.subplots(
        2, 1, figsize=(4, 3), dpi=150,
        gridspec_kw={'height_ratios': [1, 2.2]}
    )
    fig.patch.set_facecolor('white')

    # 文本区域
    ax_text.axis('off')
    ax_text.text(0.02, 0.6, config["display_name"], fontsize=11, fontweight='bold')
    ax_text.text(0.02, 0.05, f"{current_price:,.1f}", fontsize=20, fontweight='bold')

    # 涨跌幅框
    sign = "+" if price_change >= 0 else ""
    ax_text.text(
        0.98, 0.15, f" {sign}{pct_change:.2f}% ", fontsize=11, fontweight='bold',
        color='white', ha='right',
        bbox=dict(
            facecolor="black" if price_change >= 0 else "#666666",
            edgecolor='none', boxstyle='round,pad=0.3'
        )
    )

    # 图表区域
    x_idx = np.arange(len(prices))

    ax_chart.axhline(y=previous_close, color='#777777', linestyle=':', linewidth=1, zorder=1)
    ax_chart.plot(x_idx, prices, color='black', linewidth=2.0, zorder=3)
    ax_chart.fill_between(x_idx, prices, previous_close, color='#e0e0e0', alpha=0.8, zorder=2)

    ax_chart.set_xlim(0, max(len(prices) - 1, 1))

    y_min, y_max = min(float(np.min(prices)), float(previous_close)), max(float(np.max(prices)), float(previous_close))
    y_range = y_max - y_min
    margin = (y_range * 0.1) if y_range > 0 else 10
    ax_chart.set_ylim(y_min - margin, y_max + margin)

    ax_chart.set_xticks(config["xticks"])
    ax_chart.set_xticklabels(config["xticklabels"], fontsize=7, fontweight='bold', color='black')
    ax_chart.set_yticks([])
    ax_chart.tick_params(axis='both', which='both', length=0)

    for spine in ax_chart.spines.values():
        spine.set_visible(False)

    plt.tight_layout(pad=0.2)
    return fig


def push_to_device(img_io, filename):
    """推送图片到设备，依赖环境变量 ZECTRIX_API_KEY 和 DEVICE_ID / ZECTRIX_URL"""
    if not API_KEY or not ZECTRIX_URL:
        logging.error("Missing ZECTRIX_API_KEY or DEVICE_ID/ZECTRIX_URL; cannot push to device.")
        return False

    headers = {"X-API-Key": API_KEY}
    files = {'images': (filename, img_io, 'image/png')}
    data = {'dither': 'true', 'pageId': '1'}

    try:
        resp = requests.post(ZECTRIX_URL, headers=headers, files=files, data=data, timeout=30)
        resp.raise_for_status()
        logging.info("Pushed %s to device successfully (status=%s)", filename, resp.status_code)
        return True
    except requests.exceptions.RequestException as e:
        logging.exception("Failed to push to device: %s", e)
        return False


def process_segment(config, do_push=True):
    """处理单个时间段并可选推送"""
    prices, ticker_obj, previous_close = fetch_market_data(config["ticker"])

    if prices is None or len(prices) == 0:
        logging.warning("%s: 无可用数据", config['name'])
        return False

    current_price = float(prices[-1])
    price_change = current_price - float(previous_close)
    pct_change = (price_change / float(previous_close)) * 100 if previous_close else 0.0

    logging.info("%s: %0.1f (%+.2f%%)", config['name'], current_price, pct_change)

    fig = create_chart(config, np.array(prices), previous_close, current_price, pct_change, price_change)

    img_io = io.BytesIO()
    fig.savefig(img_io, format='png', facecolor='white', edgecolor='none')
    img_io.seek(0)
    plt.close(fig)

    if do_push:
        return push_to_device(img_io, config["output_file"])
    return True


def is_in_trading_hours(config):
    """判断是否在交易时间（UTC 时区）"""
    now = datetime.utcnow().time()
    start = datetime.strptime(config["start_time"], "%H:%M").time()
    end = datetime.strptime(config["end_time"], "%H:%M").time()
    return (start <= now < end) if start < end else (now >= start or now < end)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true", help="仅生成图片，不推送到设备（测试模式）")
    parser.add_argument("--all", action="store_true", help="忽略时间段判断，处理所有配置")
    args = parser.parse_args()

    do_push = not args.no_push

    logging.info("Market display run at %s", datetime.utcnow().isoformat())
    for config in TIME_SEGMENTS:
        if args.all or is_in_trading_hours(config):
            process_segment(config, do_push=do_push)

if __name__ == "__main__":
    main()
