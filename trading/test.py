
from IPython.core.debugger import Pdb
import numpy as np
import pandas as pd
import sys
sys.path.append('/Users/moilanen/sbx/paragon/lib')
sys.path.append('/Users/moilanen/sbx/paragon/data')
sys.path.append('/Users/moilanen/sbx/paragon/data/provider')
sys.path.append('.')

# Backtester Libraries
import matplotlib.pyplot as plt
import datetime
from talib.abstract import *
import pinkfish as pf

import math
from datetime import datetime, timedelta, date

from support_resistance import sr

# stocks = ['QQQ', 'AMZN']
# stocks = ['IWF', 'MTUM', 'USMV', 'VYM', 'IWD', 'IJR']
# stocks = ['TSLA']
stocks = [
        'AAL',
        #'AAPL',
        'ABBV',
        'ADBE',
        'AMD',
        'AMZN',
        'AVGO',
        'BA',
        'BABA',
        'BAC',
        'BYND',
        'CAT',
        'CGC',
        'CLF',
        'COST',
        'CRM',
        'DBX',
        'DIS',
        'DKNG',
        'DOCU',
        'EDIT',
        'ETSY',
        'F',
        'FB',
        'FXI',
        'GOOGL',
        'HD',
        'HON',
        'IQ',
        'INTC',
        'IWM',
        'JD',
        'LULU',
        'LLY',
        'LYFT',
        'MSFT',
        'MU',
        'NFLX',
        'NIO',
        'NKE',
        'NKLA',
        'NOW',
        'NVDA',
        'ORCL',
        'PENN',
        'PINS',
        'PFE',
        'PM',
        'PTON',
        'PYPL',
        'QCOM',
        'QQQ',
        'ROKU',
        'SBUX',
        'SHOP',
        'SLB',
        'SNAP',
        'SPY',
        'SPCE',
        'SQ',
        'TDOC',
        'TLRY',
        # 'TSLA',
        'TTD',
        'TTWO',
        'TWLO',
        'TWTR',
        'UA',
        'UBER',
        'WMT',
        'WYNN',
        'XLF',
        'ZM',
        'ZNGA'
                  ]

stocks = ['AAL', 'ADBE', 'AMD', 'BABA', 'C', 'AMZN', 'GOOGL', 'MSFT', 'SPCE', 'FB', 'ARKK', 'SLV', 'ZM','TWTR']


num_days = 500
capital = 100000
use_adj = True

start_date = date.today() - timedelta(days = math.ceil((num_days/(5/7))))
end_date = datetime.now()
from history import Polygon

ohlcvs = {}
ohlcvs_long = {}

polygon = Polygon()

if False:
    timeframe_short = '5m'
    periods_day_short = int(390/5)
else:
    timeframe_short = '30m'
    periods_day_short = int(390/30)

    
Pdb().set_trace()
for stock in stocks:
    print("------ %s ------" % (stock))
    ohlcv = polygon.history_aggregate(stock, int(periods_day_short*num_days), timeframe_short, start_date, after_hours = True)
#     ohlcv = history(stock, start_date, timespan = "minute", multiplier = 30)
    ohlcvs[stock] = ohlcv

    ohlcv = ohlcv = polygon.history_aggregate(stock, num_days, '1d', start_date, after_hours = True)
    ohlcvs_long[stock] = ohlcv
