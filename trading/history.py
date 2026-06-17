import numpy as np
import pandas as pd
from polygon import RESTClient
from dateutil import tz
import math
from datetime import datetime, timedelta, date

def aggregates_to_df(resp):
    epoch = datetime(1601, 1, 1)
    df = pd.DataFrame(resp.results)
#     tz_ = tz.gettz('America/New_York')
    tz_ = tz.gettz('UTC')

    df.set_index(df['t'].apply(lambda x: datetime.fromtimestamp(x / 1000, tz=tz_)), inplace=True)
    df.rename(columns={"v": "volume", "o": "open", "c": "close", "h": "high", "l": "low"}, inplace=True)
    if 't' in df:
        df.drop(['t'], axis=1, inplace=True)
    if 'n' in df:
        df.drop(['n'], axis=1, inplace=True)

    del df.index.name

    return df

def history(symbol, start_date, timespan = 'day', multiplier = 1):
    date_format = "%Y-%m-%d"
    
    end_date = date.today() + timedelta(days = 7) # Polygon is exclusive on dates and must extend for a week
    client = RESTClient('WeYC_RTF_nZg3S_UKGI68lvNZ__8l6YM09p_Rg')

    
    try:
        resp = client.stocks_equities_aggregates(symbol, multiplier, timespan, start_date.strftime(date_format), end_date.strftime(date_format))
    except Exception as e:
        print("[history_aggregate()]: Polygon connection error: %s" % (str(e)))

    if resp.status != 'OK':
        raise ValueError("Polygon history_aggregate response not OK: %s" % (resp['status']))
        
    if resp.results == None or len(resp.results) == 0:
        raise ValueError("Polygon history_aggregate results are empty")
        
    df = aggregates_to_df(resp)
    
    return df

from datetime import datetime
from dateutil import tz
import pandas_market_calendars as mcal
import time
class Polygon():
    date_format = "%Y-%m-%d"
    approximate_holes_active = False
    def __init__(self):
        self.mcal = mcal.get_calendar('NYSE')
        self.client = RESTClient('WeYC_RTF_nZg3S_UKGI68lvNZ__8l6YM09p_Rg')
    
    def history_aggregate(self, symbol, bar_count, frequency, start = False, end = False, after_hours = False):
        max_timespan = 5000
        converts = {
            '1m': {
                'timespan': 'minute',
                'multiplier': 1,
                'per_day': 390.0,
                'max_days': math.floor(max_timespan / 390.0),
                'granularity_minutes': 1
            },
            '5m': {
                'timespan': 'minute',
                'multiplier': 5,
                'per_day': 390 / 5.0,
                'max_days': math.floor(max_timespan / (390.0 / 5.0)),
                'granularity_minutes': 5
            },
            '20m': {
                'timespan': 'minute',
                'multiplier': 20,
                'per_day': 390 / 20.0,
                'max_days': math.floor(max_timespan / (390.0)),
                'granularity_minutes': 20
            },
            '30m': {
                'timespan': 'minute',
                'multiplier': 30,
                'per_day': 390 / 30.0,
                'max_days': math.floor(max_timespan / (390.0)),
                'granularity_minutes': 30
            },
            '1h': {
                'timespan': 'hour',
                'multiplier': 1,
                'per_day': 6.5,
                'max_days': math.floor(max_timespan / 24),
                'granularity_minutes': 60
            },
            '1d': {
                'timespan': 'day',
                'multiplier': 1,
                'per_day': 1.0,
                'max_days': max_timespan,
                'granularity_minutes': 24 * 60
            },
            '1w': {
                'timespan': 'week',
                'multiplier': 1,
                'per_day': 1 / 5.0,
                'max_days': math.floor(max_timespan * 7),
                'granularity_minutes': 24 * 60 * 7
            },
        }

        convert = converts[frequency]
        multiplier = convert['multiplier']
        timespan = convert['timespan']
        max_days = convert['max_days']
        granularity_minutes = convert['granularity_minutes']

        incomplete = False

        if end is False:
            end = self.get_datetime()

        if start is False:
            # See how many days we span
            days = (bar_count / convert['per_day']) + 1.0 # Make sure we go over
            days = days * 1.15

            start = self.mcal.get_days_ago(end, int(days))

        wanted_times_all = self.wanted_times(start, end, granularity_minutes, timespan)
        wanted_times_last = wanted_times_all[-1]

        # Polygon is exclusive dates
        end = end + timedelta(days=1)
        need_data = True
        start_ = datetime(start.year, start.month, start.day, tzinfo=self.timezone())
        df_out = False
        attempts = 0

        # Pdb().set_trace()

        while need_data:
            if attempts == 0:
                end_ = min(end, (start_ + timedelta(days=max_days)))
            try:
                # print("[%s]: Calling start: %s end: %s Attempt: %d" % (symbol, str(start_), str(end_), attempts))
                resp = self.client.stocks_equities_aggregates(symbol, multiplier, timespan, start_.strftime(self.date_format), end_.strftime(self.date_format))
            except Exception as e:
                print("[history_aggregate()]: Polygon connection error: %s" % (str(e)))
                time.sleep(3)
                continue

            if resp.status != 'OK':
                raise ValueError("Polygon history_aggregate response not OK: %s" % (resp['status']))

            if resp.resultsCount == 0 or resp.results == None or len(resp.results) == 0:
                #print("Empty Results: Start: %s End: %s" % (start_, end_))
                start_ = end_

                # This only happens if there is no data
                if start_ >= self.get_datetime():
                    # print("[%s]: No Data at all" % (symbol))
                    return df_out

                continue

            df = self.aggregates_to_df(resp)

            df_orig = df
            # print(str(df))
            if after_hours is False:
                # Pdb().set_trace()
                df = self.after_hours_remove(df, timespan)

            if df_out is False:
                df_out = df
            else:
                df_out = pd.concat([df_out, df], sort=True)
            df_out = df_out.loc[~df_out.index.duplicated(keep='first')]
            df_out = df_out.sort_index()

            wanted_times = self.wanted_times(start_, end_, granularity_minutes, timespan)

            missing = set(wanted_times).difference(df.index.to_list())
            if len(missing) > 0 and attempts < 1:
                first = min(missing)
                if start_ == first.to_pydatetime():

                    print("[%s] Missing[%d]: %s :: %s - %s :: Attempt: %d" % (symbol, len(missing), str(first), str(start_), str(end_), attempts))

                    # Try breaking the cache of polygon to get a different answer
                    #end_ = min(end, (start_ + timedelta(days=random.randint(1, 3))))
                    # start_ = first.to_pydatetime() - timedelta(days=random.randint(1, 3))
                    # print("[%s] Trying: %s - %s" % (symbol, str(start_), str(end_)))

                    attempts += 1
                    time.sleep(0.5 * attempts)
                else:
                    start_ = first.to_pydatetime()
                continue
            if len(missing) > 0 and attempts >= 1:
                incomplete = True
            if len(missing) > 0 and attempts >= 1 and self.approximate_holes_active is True:
                df_out = self.approximate_holes(df_out, missing, symbol)

            attempts = 0

            start_ = df_orig.iloc[-1].name.to_pydatetime()

            # print("%s :: %s" % (str(wanted_times_last.strftime(self.date_format)), str(start_.strftime(self.date_format))))
            # print("%s :: %s" % (str((end - timedelta(days=1)).strftime(self.date_format)), str(start_.strftime(self.date_format))))
            # if (end - timedelta(days=1)).strftime(self.date_format) == start_.strftime(self.date_format):
            if wanted_times_last.strftime(self.date_format) <= start_.strftime(self.date_format):
                need_data = False

        # Drop duplicates
        df_out = df_out.loc[~df_out.index.duplicated(keep='first')]

        missing_all = set(wanted_times_all).difference(df_out.index.to_list())
        if len(missing_all) > 0:
            # print("[%s] Missing Data: %s" % (symbol, str(len(missing_all))))
            pass

        if len(df_out) < bar_count:
            print("Polygon history_aggregate does not have enough data. Want: %d Have: %d DF: %s" % (bar_count, len(df_out), str(df_out)))
            # Pdb().set_trace()
            # raise ValueError("Polygon history_aggregate does not have enough data. Want: %d Have: %d DF: %s" % (bar_count, len(df), str(df)))

        return df_out.sort_index().iloc[-bar_count:,].dropna(how='all')


    def aggregates_to_df(self, resp):
        epoch = datetime(1601, 1, 1)
        df = pd.DataFrame(resp.results)
        tz = self.timezone()
        df.set_index(df['t'].apply(lambda x: datetime.fromtimestamp(x / 1000, tz=tz)), inplace=True)
        df.rename(columns={"v": "volume", "o": "open", "c": "close", "h": "high", "l": "low"}, inplace=True)
        if 't' in df:
            df.drop(['t'], axis=1, inplace=True)
        if 'n' in df:
            df.drop(['n'], axis=1, inplace=True)

#         del df.index.name

        return df
    
    def timezone(self):
        return tz.gettz('America/New_York')
    def get_datetime(self):
        return datetime.now(self.timezone())
    
    def wanted_times(self, start, end, delta, timespan):
        mcal = self.mcal
        out = []

        if end > self.get_datetime():
            end = self.get_datetime()

        convert = {
            'minute': 'min',
            'hour': 'hour',
            'day': 'D',
            'week': 'W'
        }

        dti_timespan = convert[timespan]

        schedule = mcal.schedule(start, end)
        schedule['market_open'] = schedule['market_open'].dt.tz_convert(self.timezone())
        schedule['market_close'] = schedule['market_close'].dt.tz_convert(self.timezone())

        for index, row in schedule.iterrows():

            day_start = row['market_open']
            if timespan in ['day', 'week']:
                day_start = day_start.replace(hour = 0, minute = 0, second = 0, microsecond = 0)
                if timespan == 'day':
                    td = timedelta(days=delta)
                else:
                    td = timedelta(days=(delta*7))
            else:
                td = timedelta(minutes=delta)
                day_start = row['market_open']
                if timespan == 'hour':
                    day_start = day_start.replace(hour = 9, minute = 0, second = 0, microsecond = 0)
                else:
                    minute = (30 % delta) + 30
                    day_start = day_start.replace(hour = 9, minute = minute, second = 0, microsecond = 0)

            if row['market_close'] > self.get_datetime():
                day_end = self.get_datetime() - timedelta(minutes = 1)
            else:
                day_end = row['market_close']

            dts = [pd.Timestamp(dt) for dt in self.datetime_range(day_start, day_end, td)]

            out = out + dts

        return out
    
    
    def datetime_range(self, start, end, delta):
        current = start
        while current < end:
            yield current
            current += delta
            
    def after_hours_remove(self, df, timespan):
        if timespan in ['day', 'week']:
            return df
        tz = self.timezone()
        if timespan == 'hour':
            df = df.between_time('9:00', '16:00', include_end = False)
        else:
            df = df.between_time('9:30', '16:00', include_start = True, include_end = False)

        if len(df) == 0:
            return pd.DataFrame()

        start_date = df.iloc[0].name
        end_date = df.iloc[-1].name
        delta = end_date - start_date
        days_between = delta.days
        assert days_between >= 0, "Unexpected order in after_hours_remove: %d" % (days_between)

        sched = self.mcal.schedule(start_date, end_date)
        early_closes = self.mcal.early_closes(sched)

        for index, row in early_closes.iterrows():
            # Premarket drops
            to_drop = df[df.index.date == index.date()].between_time('00:00', row['market_open'].astimezone(tz).to_pydatetime().replace(tzinfo=tz).time(), include_end = False)
            if len(to_drop):
                df = df.drop(to_drop.index)

            # After Hours
            to_drop = df[df.index.date == index.date()].between_time(row['market_close'].astimezone(tz).to_pydatetime().replace(tzinfo=tz).time(), '23:59:59', include_start = False)
            if len(to_drop):
                df = df.drop(to_drop.index)

        return df

