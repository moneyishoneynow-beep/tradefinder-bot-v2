import os
import requests
import pandas as pd
from datetime import datetime, time
import schedule
import time as time_module
from telegram import Bot
from telegram.error import TelegramError
import logging
from typing import List, Dict
import pytz

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your_bot_token_here")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID", "your_user_id_here")
BOT = Bot(token=TELEGRAM_BOT_TOKEN)

# F&O Eligible Stocks (NIFTY 50, BANKNIFTY, FINNIFTY)
FO_STOCKS = {
    'NIFTY': ['RELIANCE', 'TCS', 'INFY', 'HINDUNILVR', 'ICICIBANK', 'SBIN', 'BAJAJFINSV', 
              'LT', 'MARUTI', 'ADANIPOWER', 'TATASTEEL', 'WIPRO', 'NTPC', 'ASIANPAINT', 
              'SUNPHARMA', 'ULTRACEMCO', 'JSWSTEEL', 'AXISBANK', 'EICHERMOT', 'HDFCBANK',
              'BAJAJ-AUTO', 'BHARATIARTL', 'BRITANNIA', 'CIPLA', 'COALINDIA', 'DIVISLAB',
              'DRREDDY', 'GRASIM', 'HDFC', 'HEROMOTOCO', 'HINDALCO', 'HINDPETRO', 'IOC',
              'ITC', 'JSWSTEEL', 'KOTAKBANK', 'LT', 'LTIM', 'LUPIN', 'M&M', 'NESTLEIND',
              'ONGC', 'POWERGRID', 'SBILIFE', 'SHRIRAMFIN', 'SIEMENS', 'TATAMOTORS',
              'TATAPOWER', 'TATASTEEL', 'TECHM', 'TIINDIA', 'TITAN', 'TRENT', 'UPL', 'VBL'],
    'BANKNIFTY': ['AUBANK', 'AXISBANK', 'BANDHANBNK', 'FEDERALBNK', 'HDFCBANK', 'IDFCBANK',
                  'ICICIBANK', 'INDUSIND', 'KOTAK', 'KOTAKBANK', 'PNB', 'SBIN', 'YES'],
    'FINNIFTY': ['BAJAJFINSV', 'HDFC', 'HDFCBANK', 'ICICIBANK', 'KOTAKBANK', 'SBIN', 'AXISBANK']
}

class TradeFinderV2:
    def __init__(self):
        self.nse_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self.session = requests.Session()
        self.ist = pytz.timezone('Asia/Kolkata')
        
    def get_stock_data(self, symbol: str) -> Dict:
        """Fetch individual stock data from NSE"""
        try:
            url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
            response = self.session.get(url, headers=self.nse_headers, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"Error fetching stock {symbol}: {e}")
            return None

    def calculate_r_factor(self, stock_data: Dict) -> float:
        """R Factor = Volume Score + Momentum Score"""
        try:
            if not stock_data:
                return 0
            
            priceInfo = stock_data.get('priceInfo', {})
            volume = priceInfo.get('totalTradedVolume', 0)
            volume_score = min(volume / 1000000, 10)
            
            pct_change = abs(priceInfo.get('pctChange', 0))
            momentum_score = min(pct_change, 10)
            
            r_factor = (volume_score * 0.5) + (momentum_score * 0.5)
            return round(r_factor, 2)
        except Exception as e:
            logger.error(f"Error calculating R factor: {e}")
            return 0

    def check_daily_levels_intact(self, stock_data: Dict) -> Dict:
        """Check if Daily High/Low is intact"""
        try:
            if not stock_data:
                return {'intact': False, 'trend': 'UNKNOWN'}
            
            priceInfo = stock_data.get('priceInfo', {})
            current = priceInfo.get('lastPrice', 0)
            intra_data = priceInfo.get('intraDayHighLow', {})
            daily_high = intra_data.get('high', 0)
            daily_low = intra_data.get('low', 0)
            
            high_buffer = daily_high * 0.995
            low_buffer = daily_low * 1.005
            
            high_touched = current >= high_buffer
            low_touched = current <= low_buffer
            
            if high_touched and low_touched:
                return {'intact': False, 'trend': 'CONSOLIDATION'}
            
            if not high_touched:
                return {
                    'intact': True, 
                    'trend': 'DOWNTREND',
                    'signal': 'SELL',
                    'reason': 'Daily High Intact'
                }
            
            if not low_touched:
                return {
                    'intact': True, 
                    'trend': 'UPTREND',
                    'signal': 'BUY',
                    'reason': 'Daily Low Intact'
                }
            
            return {'intact': False, 'trend': 'UNKNOWN'}
        
        except Exception as e:
            logger.error(f"Error checking daily levels: {e}")
            return {'intact': False, 'trend': 'UNKNOWN'}

    def check_breakout_beacon(self, stock_data: Dict) -> bool:
        """Breakout Beacon: High volume + Momentum"""
        try:
            if not stock_data:
                return False
            
            priceInfo = stock_data.get('priceInfo', {})
            volume = priceInfo.get('totalTradedVolume', 0)
            pct_change = abs(priceInfo.get('pctChange', 0))
            
            return volume > 500000 and pct_change > 0.3
        except Exception as e:
            logger.error(f"Error checking breakout: {e}")
            return False

    def is_consolidation(self, stock_data: Dict) -> bool:
        """Detect consolidation (sideways movement)"""
        try:
            if not stock_data:
                return True
            
            priceInfo = stock_data.get('priceInfo', {})
            intra_data = priceInfo.get('intraDayHighLow', {})
            daily_high = intra_data.get('high', 0)
            daily_low = intra_data.get('low', 0)
            
            if daily_high == 0 or daily_low == 0:
                return True
            
            range_pct = ((daily_high - daily_low) / daily_low) * 100
            return range_pct < 1
        except Exception as e:
            logger.error(f"Error checking consolidation: {e}")
            return True

    def screen_stocks(self, symbols: List[str]) -> List[Dict]:
        """Screen F&O stocks"""
        results = []
        
        for symbol in symbols:
            try:
                stock_data = self.get_stock_data(symbol)
                
                if not stock_data:
                    continue
                
                if self.is_consolidation(stock_data):
                    continue
                
                levels = self.check_daily_levels_intact(stock_data)
                
                if not levels['intact'] or levels['trend'] == 'CONSOLIDATION':
                    continue
                
                if not self.check_breakout_beacon(stock_data):
                    continue
                
                r_factor = self.calculate_r_factor(stock_data)
                
                priceInfo = stock_data.get('priceInfo', {})
                
                results.append({
                    'symbol': symbol,
                    'price': round(priceInfo.get('lastPrice', 0), 2),
                    'change': round(priceInfo.get('pctChange', 0), 2),
                    'volume': int(priceInfo.get('totalTradedVolume', 0)),
                    'r_factor': r_factor,
                    'trend': levels['trend'],
                    'signal': levels['signal'],
                    'high': round(priceInfo.get('intraDayHighLow', {}).get('high', 0), 2),
                    'low': round(priceInfo.get('intraDayHighLow', {}).get('low', 0), 2),
                })
            except Exception as e:
                logger.error(f"Error screening {symbol}: {e}")
                continue
        
        results.sort(key=lambda x: x['r_factor'], reverse=True)
        return results

    def format_message(self, results: List[Dict]) -> str:
        """Format screening results"""
        ist_time = datetime.now(self.ist).strftime('%H:%M IST')
        
        if not results:
            message = f"🔍 *No Setup Found*\n"
            message += f"⏰ Time: {ist_time}\n"
            message += f"Status: Market scanning... Waiting for momentum\n\n"
            message += "Keep monitoring! 📊"
            return message
        
        message = f"🎯 *TRADEFINDER - F&O ALERT*\n"
        message += "=" * 50 + "\n"
        message += f"⏰ Time: {ist_time}\n"
        message += f"📊 Stocks Found: {len(results)}\n"
        message += "=" * 50 + "\n\n"
        
        for i, stock in enumerate(results[:7], 1):
            message += f"*{i}. {stock['symbol']}*\n"
            message += f"   Price: ₹{stock['price']}\n"
            message += f"   Change: {stock['change']:+.2f}%\n"
            message += f"   Volume: {stock['volume']:,}\n"
            message += f"   R-Factor: {stock['r_factor']:.1f}⭐\n"
            message += f"   Daily: H₹{stock['high']} | L₹{stock['low']}\n"
            message += f"   Trend: {stock['trend']}\n"
            message += f"   🎯 Action: *{stock['signal']}* (Options)\n"
            message += "\n"
        
        message += "-" * 50 + "\n"
        message += "*Entry Rules:*\n"
        message += "✓ Daily High/Low intact\n"
        message += "✓ Breakout + High Volume\n"
        message += "✓ No Consolidation\n"
        message += "✓ Top R-Factor stocks\n\n"
        message += "⚠️ Risk Management Always! 🚀"
        
        return message

    def run_screener(self):
        """Main screening function"""
        try:
            ist_time = datetime.now(self.ist)
            
            market_open = time(9, 15)
            market_close = time(15, 30)
            
            if not (market_open <= ist_time.time() <= market_close):
                logger.info(f"Market closed. Current time: {ist_time.strftime('%H:%M IST')}")
                return
            
            logger.info(f"Starting screener at {ist_time.strftime('%H:%M IST')}")
            
            all_stocks = []
            for category, stocks in FO_STOCKS.items():
                all_stocks.extend(stocks)
            
            all_stocks = list(set(all_stocks))
            
            results = self.screen_stocks(all_stocks)
            
            message = self.format_message(results)
            
            try:
                BOT.send_message(
                    chat_id=TELEGRAM_USER_ID,
                    text=message,
                    parse_mode='Markdown'
                )
                logger.info(f"Alert sent. Stocks found: {len(results)}")
            except TelegramError as e:
                logger.error(f"Telegram error: {e}")
        
        except Exception as e:
            logger.error(f"Screener error: {e}")

def schedule_screener():
    """Schedule screener every 15 minutes"""
    finder = TradeFinderV2()
    
    schedule.every(15).minutes.do(finder.run_screener)
    
    logger.info("Screener scheduled - Every 15 minutes (9:15 AM - 3:30 PM IST)")
    
    while True:
        schedule.run_pending()
        time_module.sleep(60)

if __name__ == "__main__":
    schedule_screener()
