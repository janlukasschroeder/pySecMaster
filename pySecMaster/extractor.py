import time
from datetime import datetime, timedelta
import pandas as pd
import sqlite3
from multiprocessing import Pool
import csv

from download import download_quandl_codes, download_quandl_data, \
    download_google_data

__author__ = 'Josh Schertz'
__copyright__ = 'Copyright (C) 2015 Josh Schertz'
__description__ = 'An automated system to store and maintain financial data.'
__email__ = 'josh[AT]joshschertz[DOT]com'
__license__ = 'GNU AGPLv3'
__maintainer__ = 'Josh Schertz'
__status__ = 'Development'
__url__ = 'https://joshschertz.com/'
__version__ = '1.0'

'''
    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as
    published by the Free Software Foundation, either version 3 of the
    License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''


def multithread(function, items, threads=4, *args):
    """ Takes the main function to run in parallel, inputs the variable(s)
    and returns the results.

    :param function: The main function to process in parallel.
    :param items: A list of strings that are passed into the function for
    each thread.
    :param *args: Additional variables that can be passed into the function.
    :param threads: The number of threads to use. The default is 4, but
    the threads are not CPU core bound.
    :return: The results of the function passed into this function.
    """

    # latest_prices = args

    """The async variant, which submits all processes at once and
    retrieve the results as soon as they are done."""
    pool = Pool(threads)
    output = [pool.apply_async(function, args=(item,))
              for item in items]
    results = [p.get() for p in output]
    pool.close()
    pool.join()

    return results


def dt_from_iso(row, column):
    """
    Changes the UTC ISO 8601 date string to a datetime object
    """
    iso = row[column]
    try:
        return datetime.strptime(iso, '%Y-%m-%dT%H:%M:%S.%f')
    except ValueError:
        return datetime.strptime(iso, '%Y-%m-%dT%H:%M:%S')
    except TypeError:
        return 'NaN'


def df_to_sql(df, db_location, sql_table, exists, item):

    # print('Entering the data for %s into the SQL database.' % (item,))
    conn = sqlite3.connect(db_location)

    # Try and except block writes the new data to the SQL Database.
    try:
        df.to_sql(sql_table, conn, if_exists=exists, index=False)
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.execute("PRAGMA busy_timeout = 60000")
        # print('Successfully entered the Quandl Codes into the SQL Database')
    except conn.Error:
        conn.rollback()
        # df.to_csv('error_logs/' + item + '_' +
        #           str(datetime.now().strftime('%Y%m%d%H%M%S')) + '.csv')
        print("Failed to insert the DataFrame into the Database for %s" %
              (item,))
    except conn.OperationalError:
        raise ValueError('Unable to connect to the SQL Database in df_to_sql. '
                         'Make sure the database address/name are correct.')
    except Exception as e:
        print('Error: Unknown issue when adding DF to SQL for %s' % (item,))
        print(e)


def delete_sql_table_rows(db_location, query, table, q_code):

    # print('Deleting all rows in %s that fit the provided criteria' % (table,))
    conn = sqlite3.connect(db_location)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(query)
        return 'success'
    except sqlite3.Error as e:
        conn.rollback()
        print(e)
        print('Error: Not able to delete the overlapping rows for %s in '
              'the %s table.' % (q_code, table))
        return 'failure'
    except conn.OperationalError:
        print('Unable to connect to the SQL Database in delete_sql_table_rows. '
              'Make sure the database address/name are correct.')
        return 'failure'
    except Exception as e:
        print('Error: Unknown issue when trying to delete overlapping rows for'
              '%s in the %s table.' % (q_code, table))
        print(e)
        return 'failure'


class QuandlCodeExtract(object):

    def __init__(self, db_location, quandl_token, database_list, database_url,
                 update_range):
        self.db_location = db_location
        self.quandl_token = quandl_token
        self.db_list = database_list
        self.db_url = database_url
        self.update_range = update_range
        self.main()

    def main(self):

        extractor_start = time.time()

        # Determine if any Quandl data sets never finished downloading all of
        #   their Quandl Codes. A complete data set has a page_num of -2 for all
        #   its Quandl Codes. Otherwise, a real number will exist for the items.
        data_sets = self.query_last_download_pg()

        # The quandl_codes table is empty, so all codes should be downloaded
        if len(data_sets) == 0:
            print('Downloading all Quandl Codes for the following databases: %s'
                  % (", ".join([db for db in self.db_list])))

            for db_name in self.db_list:
                self.extractor(db_name)

            print('Finished downloading Quandl Codes from the %i databases, '
                  'taking %0.1f seconds' %
                  # '{:,}'.format(len(q_code_df.index))
                  (len(self.db_list), time.time() - extractor_start))

        # Codes already exists in the quandl_codes table, so this will determine
        #   whether the codes need to be updated or if the download was
        #   incomplete.
        else:

            data_sets['updated_date'] = data_sets.apply(dt_from_iso, axis=1,
                                                        args=('updated_date',))

            # This for loop only provides program info; doesn't format anything
            for row in range(len(data_sets)):
                existing_vendor = data_sets.loc[row, 'database_code']
                # existing_page_num = data_sets.loc[row, 'page_num']
                existing_updated_date = data_sets.loc[row, 'updated_date']
                if existing_vendor not in self.db_list:
                    print('FLAG: Did not update the %s data set because it was '
                          'not included in the database_list variable. The '
                          'last update was on %s' %
                          (existing_vendor,
                           existing_updated_date.strftime('%Y-%m-%d')))

            for data_vendor in self.db_list:

                # Does the data vendor already exists? If not go to extractor.
                vendor_exist = data_sets.loc[data_sets['database_code'] ==
                                             data_vendor]
                if len(vendor_exist) == 0:
                    self.extractor(data_vendor)

                # Data vendor already exist. Check for other criteria
                else:
                    page_num = data_sets.loc[data_sets['database_code'] ==
                                             data_vendor, 'page_num']
                    page_num = page_num.iloc[0]
                    updated_date = data_sets.loc[data_sets['database_code'] ==
                                                 data_vendor, 'updated_date']
                    updated_date = updated_date.iloc[0]

                    beg_date_obj = (datetime.now() - timedelta(
                                    days=self.update_range))
                    # beg_date = beg_date_obj.strftime('%Y-%m-%d')

                    # Were the Quandl Codes downloaded within the update_range
                    #   and is the highest page_num for that data set greater
                    #   than zero?
                    if (updated_date > beg_date_obj) and page_num > 0:
                        print('The %s data set needs to finish downloading. '
                              'Will continue downloading codes starting from '
                              'page %i' % (data_vendor, page_num + 1))
                        self.extractor(data_vendor, page_num + 1)

                    # Quandl Codes have been updated in more days than update
                    #   range, thus the entire data set's codes should be
                    #   refreshed.
                    elif updated_date < beg_date_obj:
                        print('The Quandl Codes for %s were downloaded more '
                              'than %i days ago, the maximum update range. '
                              'New codes will now be downloaded to replace the '
                              'old codes' % (data_vendor, self.update_range))
                        query = ("""DELETE FROM quandl_codes
                                    WHERE database_code='%s'"""
                                 % (data_vendor,))
                        del_success = delete_sql_table_rows(self.db_location,
                                                            query,
                                                            'quandl_codes',
                                                            'UNUSED')
                        if del_success == 'success':
                            self.extractor(data_vendor)
                        elif del_success == 'failure':
                            continue
                    else:
                        print('All of the Quandl Codes for %s are up to date.'
                              % (data_vendor,))

    def query_last_download_pg(self):
        """ Find Quandl data sets that did not finished downloading all of their
        Quandl Codes. It is assumed that if only part of the data set was
        downloaded, the remainder of the download should continue with the
        last downloaded page. Uses the page_num column to mark this.

        :return: A DataFrame with the Quandl data set and the max page_num.
        """

        try:
            conn = sqlite3.connect(self.db_location)
            with conn:
                df = pd.read_sql("SELECT database_code, "
                                 "MAX(page_num) AS page_num, updated_date "
                                 "FROM  quandl_codes "
                                 "GROUP BY database_code ", conn)
                return df
        except sqlite3.Error as e:
            print('Error when trying to connect to the database quandl_codes '
                  'table in query_latest_codes in QuandlCodeExtract.')
            print(e)

    def extractor(self, db_name, page_num=1):
        """ For every database passed through, each page number will be
        incremented through, saving the downloaded data to the SQL table. If no
        data is returned from the download function, then all tables have been
        downloaded for that particular database.

        :param db_name: A string of the name that Quandl uses for the database
        :param page_num: An optional integer to indicate the page number to
        start on. If no page_num is provided, it is assumed that the entire
        data set needs to be downloaded. Otherwise, it is assumed that the
        data set download was interrupted and will continue downloading codes.
        :return: Nothing. All data is saved to the SQL quandl_codes table.
        """

        dl_csv_start_time = time.time()
        next_page = True
        while next_page:
            db_pg_df = download_quandl_codes(self.quandl_token, self.db_url,
                                             db_name, page_num)
            if len(db_pg_df.index) == 0:  # finished downloading all pages
                next_page = False
            else:
                try:
                    db_pg_df.insert(1, 'ticker_id', 0)
                    db_pg_df.insert(2, 'data', 'Unknown')
                    db_pg_df.insert(3, 'component', 'Unknown')
                    db_pg_df.insert(4, 'period', 'Unknown')
                except Exception as e:
                    print('The columns for component, period, document and '
                          'data_vendor are already created.')
                    print(e)

                if db_name in ('EIA', 'JODI', 'ZFA', 'ZFB', 'RAYMOND', 'SEC'):
                    clean_df = self.process_3_item_q_codes(db_pg_df)
                elif db_name in ('GOOG', 'YAHOO', 'FINRA'):
                    clean_df = self.process_2_item_q_codes(db_pg_df)
                else:       # 'WIKI', 'EIA', 'ZEP', 'EOD', 'CURRFX'
                    clean_df = self.process_1_item_q_codes(db_pg_df)

                df_to_sql(clean_df, self.db_location, 'quandl_codes', 'append',
                          db_name)

                if page_num % 100 == 0:
                    print('Still downloading %s codes. Just finished page '
                          '%i...' % (db_name, page_num))
                page_num += 1

        # Remove duplicate q_codes
        conn = sqlite3.connect(self.db_location)
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("""DELETE FROM quandl_codes
                               WHERE rowid NOT IN
                               (SELECT min(rowid)
                               FROM quandl_codes
                               GROUP BY quandl_id)""")
                print('Successfully removed all duplicate q_codes from '
                      'quandl_codes')
        except sqlite3.Error as e:
            conn.rollback()
            print(e)
            print('Error: Not able to remove duplicate q_codes in the %s data '
                  'set while running the extractor.' % (db_name,))
        except conn.OperationalError:
            print('Unable to connect to the SQL Database in extractor. Make '
                  'sure the database address/name are correct.')
        except Exception as e:
            print('Error: An unknown issue occurred when removing all '
                  'duplicate q_codes in the %s data set.' % (db_name,))
            print(e)

        # Change the data set page_num variable to -2, indicating it finished
        conn = sqlite3.connect(self.db_location)
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("""UPDATE quandl_codes
                               SET page_num=-2
                               WHERE database_code=?""", (db_name,))
                print('Successfully updated %s codes with final page_num '
                      'variable.' % (db_name,))
        except sqlite3.Error as e:
            conn.rollback()
            print(e)
            print('Error: Not able to update the page_num rows for codes in '
                  'the %s data set while running the extractor.' % (db_name,))
        except conn.OperationalError:
            print('Unable to connect to the SQL Database in extractor. Make '
                  'sure the database address/name are correct.')
        except Exception as e:
            print('Error: An unknown issue occurred when changing the page_num'
                  'rows for codes in the %s data set.' % (db_name,))
            print(e)

        print('The %s database took %0.1f seconds to download'
              % (db_name, time.time() - dl_csv_start_time))

    @staticmethod
    def process_3_item_q_codes(df):

        # Each EIA code structure: EIA/[document]_[component]_[period]
        #   NOTE: EIA/IES database does not follow this structure
        # JODI code structure: JODI/[type]_[product][flow][unit]_[country]

        def strip_q_code(row, column):
            code = row['dataset_code']
            if column == 'data':
                # ToDo: Find a way to include items with an underscore in name
                # Example: 'EIA/AEO_2014_{Component}_A' --> 'AEO_2014'
                # If block handles 1 item codes that are in 3 item data sets
                if code.find('_') != -1:
                    return code[:code.find('_')]
                else:
                    return 'Unknown'
            elif column == 'component':
                # If block handles 1 item codes that are in 3 item data sets
                if code.find('_') != -1:
                    return code[code.find('_') + 1:code.rfind('_')]
                else:
                    return code
            elif column == 'period':
                # If block handles 1 item codes that are in 3 item data sets
                if code.find('_') != -1:
                    return code[code.rfind('_') + 1:]
                else:
                    return 'Unknown'
            else:
                print('Error: Unknown column [%s] passed in to strip_q_code in '
                      'process_3_item_q_codes' % (column,))

        df['data'] = df.apply(strip_q_code, axis=1, args=('data',))
        df['component'] = df.apply(strip_q_code, axis=1, args=('component',))
        df['period'] = df.apply(strip_q_code, axis=1, args=('period',))
        return df

    @staticmethod
    def process_2_item_q_codes(df):

        def strip_q_code(row, column):
            code = row['dataset_code']
            if column == 'data':
                # data -> exchange
                # If block handles 1 item codes that are in 2 item data sets
                if code.find('_') != -1:
                    return code[:code.find('_')]
                else:
                    return 'Unknown'
            elif column == 'component':
                # component -> ticker
                # If block handles 1 item codes that are in 2 item data sets
                if code.find('_') != -1:
                    return code[code.find('_') + 1:]
                else:
                    return code
            else:
                print('Error: Unknown column [%s] passed in to strip_q_code in '
                      'process_2_item_q_codes' % (column,))

        df['data'] = df.apply(strip_q_code, axis=1, args=('data',))
        df['component'] = df.apply(strip_q_code, axis=1, args=('component',))
        return df

    @staticmethod
    def process_1_item_q_codes(df):

        def strip_q_code(row, column):
            code = row['dataset_code']
            if column == 'component':
                return code
            else:
                print('Error: Unknown column [%s] passed in to strip_q_code in '
                      'process_1_item_q_codes' % (column,))

        df['component'] = df.apply(strip_q_code, axis=1, args=('component',))
        return df


class QuandlDataExtraction(object):

    def __init__(self, db_location, quandl_token, db_url, download_selection,
                 redownload_time, data_process, days_back):
        self.database_location = db_location
        self.quandl_token = quandl_token
        self.db_url = db_url
        self.download_selection = download_selection
        self.redownload_time = redownload_time
        self.data_process = data_process
        self.days_back = days_back

        print('Retrieving dates of the last price per ticker...')
        # Creates a DataFrame with the last price for each security
        self.latest_prices = self.query_last_price()

        self.main()

    def main(self):
        """
        The main QuandlDataExtraction method is used to execute subsequent
        methods in the correct order.
        """

        start_time = time.time()

        print('Analyzing the Quandl Codes that will be downloaded...')
        # Create a list of securities to download
        q_code_df = self.query_q_codes(self.download_selection)
        # Get DF of selected codes plus when (if ever) they were last updated
        q_codes_df = pd.merge(q_code_df, self.latest_prices,
                              left_on='q_code_id', right_index=True, how='left')
        # Sort the DF with un-downloaded items first, then based on last update
        q_codes_df.sort('updated_date', ascending=True, na_position='first',
                        inplace=True)

        # The cut-off time for when code data can be re-downloaded
        beg_date_obj = (datetime.utcnow() -
                        timedelta(seconds=self.redownload_time))
        # For the final download list, only include new and non-recent codes
        q_codes_final = q_codes_df[(q_codes_df['updated_date'] < beg_date_obj) |
                                   (q_codes_df['updated_date'].isnull())]

        # Change the DF to a list
        q_code_list = q_codes_final['q_code_id'].values.flatten()

        # Inform the user how many codes will be updated
        dl_codes = len(q_codes_final.index)
        total_codes = len(q_codes_df.index)
        print('%s Quandl Codes out of %s requested codes will be downloaded.\n'
              '%s codes were last updated within the %s second limit.'
              % ('{:,}'.format(dl_codes), '{:,}'.format(total_codes),
                 '{:,}'.format(total_codes - dl_codes),
                 '{:,}'.format(self.redownload_time)))

        """ This runs the program with no multiprocessing or threading.
        To run, make sure to comment out all pool processes.
        Takes about 55 seconds for 10 tickers; 5.5 seconds per ticker. """
        # [self.extractor(q_code) for q_code in q_code_list]

        """ This runs the program with multiprocessing or threading.
        Comment and uncomment the type of multiprocessing in the
        multi-thread function above to change the type. Change the number
        of threads below to alter the speed of the downloads. If the
        query runs out of items, try lowering the number of threads.
        22 threads -> 932.65 seconds"""
        multithread(self.extractor, q_code_list, threads=8)

        print('The price extraction took %0.2f seconds to complete' %
              (time.time() - start_time))

    def query_q_codes(self, download_selection):
        """
        Builds a list of Quandl Codes from a SQL query. These codes are the
        items that will have their data downloaded. 

        With more databases, it may be necessary to have the user
        write custom queries if they only want certain items downloaded.
        Perhaps the best way will be to have some predefined queries, and if
        those don't work for the user, they write a custom query.

        :download_selection: String that matches an if condition below
        :return: List with each item being a Quandl Codes as a string
        """

        try:
            conn = sqlite3.connect(self.database_location)
            with conn:
                cur = conn.cursor()

                # ToDo: MED: Will need to create queries for additional items

                # Retrieve all q_codes
                if download_selection == 'all':
                    cur.execute("""SELECT q_code_id FROM quandl_codes""")

                # Retrieve q_codes traded in any exchange located in the US
                elif download_selection == 'us_only':
                    cur.execute("""SELECT q_code_id
                                    FROM quandl_codes
                                    WHERE
                                    exchange IN(
                                        SELECT abbrev_goog
                                        FROM exchange
                                        WHERE country='United States')""")

                # Retrieve q_codes that are in these main US exchanges
                elif download_selection == 'us_main_goog':
                    # NASDAQ - 3173 items
                    # NYSE - 4453 items
                    # NYSEARCA - ETFs; 1572 items
                    # NYSEMKT - Former AMEX; Small caps; 506 items
                    cur.execute("""SELECT q_code_id
                                   FROM quandl_codes
                                   WHERE data IN (
                                       SELECT abbrev_goog
                                       FROM exchange
                                       WHERE abbrev IN ('NASDAQ','NYSE'))
                                   AND database_code='GOOG'""")

                elif download_selection == 'wiki':
                    cur.execute("""SELECT q_code_id
                                   FROM quandl_codes
                                   WHERE database_code='WIKI'""")

                else:
                    raise TypeError('Error: In query_q_codes, improper '
                                    'download_selection was provided. If this '
                                    'is a new query, ensure the SQL is proper')
                
                data = cur.fetchall()
                if data:
                    df = pd.DataFrame(data, columns=['q_code_id'])
                    # ticker_list = df.values.flatten()
                    # df.to_csv('query_q_code.csv')
                    return df
                else:
                    raise TypeError('Not able to determine the q_codes from '
                                    'the SQL query in query_q_codes')
        except sqlite3.Error as e:
            print(e)
            raise TypeError('Error when trying to connect to the database '
                            'in query_q_codes')

    def query_last_price(self):
        """ Queries the pricing database to find the latest dates for each item
        in the database, regardless of whether it is in the q_code_list.
        
        :return: Returns a DataFrame with the Quandl Code and the date of the 
        latest data point for all tickers in the database.
        """

        try:
            conn = sqlite3.connect(self.database_location)
            with conn:
                df = pd.read_sql("SELECT q_code_id, MAX(date) as date, "
                                 "updated_date "
                                 "FROM  daily_prices "
                                 "GROUP BY q_code_id", conn,
                                 index_col='q_code_id')
                if len(df.index) == 0:
                    return df
                df['date'] = df.apply(dt_from_iso, axis=1, args=('date',))
                df['updated_date'] = df.apply(dt_from_iso, axis=1,
                                              args=('updated_date',))
                # df.to_csv('query_last_price.csv')
                return df
        except sqlite3.Error as e:
            print(e)
            raise TypeError('Error when trying to connect to the database '
                            'in query_last_price')

    def extractor(self, q_code):
        """ Takes the Quandl ticker quote, downloads the historical data,
        and then saves the data into the SQLite database.

        :param q_code: String of the Quandl code
        :return: Nothing. It saves the price data in the SQLite Database.
        """

        main_time_start = time.time()

        # The ticker has no prior price; add all the downloaded data
        if q_code not in self.latest_prices.index:
            clean_data = download_quandl_data(self.quandl_token, self.db_url,
                                              q_code)

            # There is not new data, so do nothing to the database
            if len(clean_data.index) == 0:
                print('No update for %s | %0.1f seconds' %
                      (q_code, time.time() - main_time_start))
            # There is new data to add to the database
            else:
                # Find the data vendor of the q_code; add it to the DataFrame
                data_vendor = self.retrieve_data_vendor_id(q_code)
                clean_data.insert(0, 'data_vendor_id', data_vendor)

                df_to_sql(clean_data, self.database_location, 'daily_prices',
                          'append', q_code)
                print('Updated %s | %0.1f seconds' %
                      (q_code, time.time() - main_time_start))

        # The pricing database has prior values; append/replace new data points
        else:
            try:
                last_date = self.latest_prices.loc[q_code, 'date']

                # This will only download the data for the past x days.
                if self.data_process == 'replace' and self.days_back:
                    beg_date_obj = (last_date - timedelta(days=self.days_back))
                    # YYYY-MM-DD format needed for Quandl API download
                    beg_date = beg_date_obj.strftime('%Y-%m-%d')
                    clean_data = download_quandl_data(self.quandl_token,
                                                      self.db_url, q_code,
                                                      beg_date)

                # This will download the entire data set, but only keep new
                #   data after the latest existing data point.
                else:
                    raw_data = download_quandl_data(self.quandl_token,
                                                    self.db_url, q_code)
                    # DataFrame of only the new data
                    clean_data = raw_data[raw_data.date > last_date.isoformat()]
            except Exception as e:
                print('Failed to determine what data is new for %s in extractor'
                      % q_code)
                print(e)
                return

            # There is not new data, so do nothing to the database
            if len(clean_data.index) == 0:
                print('No update for %s | %0.1f seconds' %
                      (q_code, time.time() - main_time_start))
            # There is new data to add to the database
            else:
                # Find the data vendor of the q_code; add it to the DataFrame
                data_vendor = self.retrieve_data_vendor_id(q_code)
                clean_data.insert(0, 'data_vendor_id', data_vendor)

                # If replacing existing data, delete the overlapping data points
                if self.data_process == 'replace' and self.days_back:
                    # Data should be newest to oldest; gets the oldest date, as
                    #   any date between that and the latest date need to be
                    #   deleted before the new data can be added.
                    first_date_iso = clean_data['date'].min()
                    query = ("""DELETE FROM daily_prices
                                WHERE q_code_id='%s'
                                AND date>='%s'""" % (q_code, first_date_iso))
                    del_success = delete_sql_table_rows(self.database_location,
                                                        query, 'daily_prices',
                                                        q_code)
                    # Not able to delete existing data, so skip ticker for now
                    if del_success == 'failure':
                        return

                # Append the new data to the end, regardless of replacement
                df_to_sql(clean_data, self.database_location, 'daily_prices',
                          'append', q_code)
                print('Updated %s | %0.1f seconds' %
                      (q_code, time.time() - main_time_start))

    def retrieve_data_vendor_id(self, q_code):
        """ Takes the Quandl Code and tries to find data vendor from the
        data_vendor table in the database. If nothing is returned in the
        query, then 'Unknown' is used.

        :param q_code: A string that has the Quandl Code
        :return: A string with the data vendor's id number, or 'Unknown'
        """

        try:
            conn = sqlite3.connect(self.database_location)
            with conn:
                cur = conn.cursor()
                cur.execute("""SELECT data_vendor_id
                               FROM data_vendor
                               WHERE name=?
                               LIMIT 1""", 
                            ('Quandl_' + q_code[:q_code.find('/')],))
                data = cur.fetchone()
                if data:    # A q_code was found
                    data = data[0]
                else:
                    data = 'Unknown'
                    print('Not able to determine the data_vendor_id for %s'
                          % q_code)
                return data
        except sqlite3.Error as e:
            print('Error when trying to retrieve data from database in '
                  'retrieve_data_vendor_id')
            print(e)


class GoogleFinanceDataExtraction(object):

    def __init__(self, db_location, db_url, dwnld_selection, redownload_time,
                 data_process, days_back):
        self.db_location = db_location
        self.db_url = db_url
        self.dwnld_selection = dwnld_selection
        self.redownload_time = redownload_time
        self.data_process = data_process
        self.days_back = days_back

        print('Retrieving dates of the last price per ticker...')
        # Creates a DataFrame with the last price for each security
        self.latest_prices = self.query_last_price()

        self.main()

    def main(self):
        """
        The main GoogleFinanceDataExtraction method is used to execute
        subsequent methods in the correct order.
        """

        start_time = time.time()

        print('Analyzing the Quandl Codes that will be downloaded...')
        # Create a list of securities to download
        q_code_df = self.query_q_codes(self.dwnld_selection)
        # Get DF of selected codes plus when (if ever) they were last updated
        q_codes_df = pd.merge(q_code_df, self.latest_prices,
                              left_on='q_code_id', right_index=True, how='left')
        # Sort the DF with un-downloaded items first, then based on last update
        q_codes_df.sort('updated_date', ascending=True, na_position='first',
                        inplace=True)

        try:
            # Load the codes that did not have data from the last extractor run
            codes_wo_data_df = pd.read_csv('load_tables/goog_min_codes_wo_data_'
                                           'v3.csv', index_col=False)
            # Exclude these codes that are within the 15 day re-download period
            beg_date_obj_wo_data = (datetime.utcnow() - timedelta(days=15))
            exclude_codes_df = codes_wo_data_df[codes_wo_data_df['date_tried'] >
                                                beg_date_obj_wo_data.isoformat()]
            # Change DF to a list of only the q_codes
            list_to_exclude = exclude_codes_df['q_code_id'].values.flatten()
            # Create a temp DF from q_codes_df with only the codes to exclude
            q_codes_to_exclude = q_codes_df['q_code_id'].isin(list_to_exclude)
            # From the main DF, remove any of the codes that are in the temp DF
            # NOTE: Might be able to just use exclude_codes_df instead
            q_codes_df = q_codes_df[~q_codes_to_exclude]
        except IOError:
            # The CSV file doesn't exist; create a file that will be appended to
            with open('load_tables/goog_min_codes_wo_data_v3.csv', 'a',
                      newline='') as f:
                writer = csv.writer(f, delimiter=',')
                writer.writerow(('q_code_id', 'date_tried'))
            pass

        # The cut-off time for when code data can be re-downloaded
        beg_date_obj = (datetime.utcnow() -
                        timedelta(seconds=self.redownload_time))
        # For the final download list, include both new and non-recent codes
        q_codes_final = q_codes_df[(q_codes_df['updated_date'] < beg_date_obj) |
                                   (q_codes_df['updated_date'].isnull())]

        # Change the DF to a list
        q_code_list = q_codes_final['q_code_id'].values.flatten()

        # Inform the user how many codes will be updated
        dl_codes = len(q_codes_final.index)
        total_codes = len(q_codes_df.index)
        print('%s Quandl Codes out of %s requested codes will be downloaded.\n'
              '%s codes were last updated within the %s second limit.'
              % ('{:,}'.format(dl_codes), '{:,}'.format(total_codes),
                 '{:,}'.format(total_codes - dl_codes),
                 '{:,}'.format(self.redownload_time)))

        """ This runs the program with no multiprocessing or threading.
        To run, make sure to comment out all pool processes.
        Takes about 55 seconds for 10 tickers; 5.5 seconds per ticker. """
        # [self.extractor(q_code) for q_code in q_code_list]

        """ This runs the program with multiprocessing or threading.
        Comment and uncomment the type of multiprocessing in the
        multi-thread function above to change the type. Change the number
        of threads below to alter the speed of the downloads. If the
        query runs out of items, try lowering the number of threads."""
        multithread(self.extractor, q_code_list, threads=4)

        print('The price extraction took %0.2f seconds to complete' %
              (time.time() - start_time))

    def query_q_codes(self, download_selection):
        """
        Builds a list of Quandl Codes from a SQL query. These codes are the
        items that will have their data downloaded. NOTE: Only return GOOG
        codes.

        With more databases, it may be necessary to have the user
        write custom queries if they only want certain items downloaded.
        Perhaps the best way will be to have some predefined queries, and if
        those don't work for the user, they write a custom query.

        :download_selection: String that matches an if condition below
        :return: List with each item being a Quandl Codes as a string
        """

        try:
            conn = sqlite3.connect(self.db_location)
            with conn:
                cur = conn.cursor()

                # ToDo: MED: Will need to create queries for additional items

                # Retrieve all GOOG q_codes
                if download_selection == 'all':
                    cur.execute("""SELECT q_code_id
                                   FROM quandl_codes
                                   WHERE database_code='GOOG'""")
                # Retrieve GOOG q_codes traded in any exchange located in the US
                elif download_selection == 'us_only':
                    cur.execute("""SELECT q_code_id
                                    FROM quandl_codes
                                    WHERE
                                    exchange IN(
                                        SELECT abbrev_goog
                                        FROM exchange
                                        WHERE country='United States')
                                    AND database_code='GOOG'""")
                # Retrieve GOOG q_codes that are in these main US exchanges
                elif download_selection == 'us_main_goog':
                    # Restricts codes that have had updates in the past 45 days
                    #   since Google only provides min data with 15 day history
                    beg_date = (datetime.utcnow() - timedelta(days=45))
                    # NASDAQ - 3173 items
                    # NYSE - 4453 items
                    # NYSEARCA - ETFs; 1572 items
                    # NYSEMKT - Former AMEX; Small caps; 506 items
                    cur.execute("""SELECT q_code_id
                                   FROM quandl_codes
                                   WHERE data IN (
                                       SELECT abbrev_goog
                                       FROM exchange
                                       WHERE abbrev IN ('NASDAQ','NYSE'))
                                   AND database_code='GOOG'
                                   AND newest_available_date>?""",
                                (beg_date.isoformat(),))
                else:
                    raise TypeError('Error: In query_q_codes, improper '
                                    'download_selection was provided. If this '
                                    'is a new query, ensure the SQL has proper '
                                    'syntax.')

                data = cur.fetchall()
                if data:
                    df = pd.DataFrame(data, columns=['q_code_id'])
                    # ticker_list = df.values.flatten()
                    # df.to_csv('query_q_code.csv')
                    return df
                else:
                    raise TypeError('Not able to determine the q_codes from '
                                    'the SQL query in query_q_codes.')
        except sqlite3.Error as e:
            print(e)
            raise TypeError('Error when trying to connect to the database '
                            'in query_q_codes')

    def query_last_price(self):
        """ Queries the pricing database to find the latest dates for each item
        in the database, regardless of whether it is in the q_code_list.

        :return: Returns a DataFrame with the Quandl Code and the date of the
        latest data point for all tickers in the database.
        """

        try:
            conn = sqlite3.connect(self.db_location)
            with conn:
                df = pd.read_sql("SELECT q_code_id, MAX(date) as date, "
                                 "updated_date "
                                 "FROM  minute_prices "
                                 "GROUP BY q_code_id", conn,
                                 index_col='q_code_id')
                if len(df.index) == 0:
                    return df
                df['date'] = df.apply(dt_from_iso, axis=1, args=('date',))
                df['updated_date'] = df.apply(dt_from_iso, axis=1,
                                              args=('updated_date',))
                # df.to_csv('query_last_min_price.csv')
                return df
        except sqlite3.Error as e:
            print(e)
            raise TypeError('Error when trying to connect to the database '
                            'in query_last_price')

    def extractor(self, q_code):
        """ Takes the Quandl ticker quote, downloads the historical data,
        and then saves the data into the SQLite database.

        :param q_code: String of the Quandl code
        :return: Nothing. It saves the price data in the SQLite Database.
        """

        main_time_start = time.time()

        # The ticker has no prior price; add all the downloaded data
        if q_code not in self.latest_prices.index:
            clean_data = download_google_data(self.db_url, q_code)

            # There is no new data, so do nothing to the database
            if len(clean_data.index) == 0:
                print('No data for %s | %0.1f seconds' %
                      (q_code, time.time() - main_time_start))
            # There is new data to add to the database
            else:
                # Find the data vendor of the q_code; add it to the DataFrame
                data_vendor = self.retrieve_data_vendor_id('Google_Finance')
                clean_data.insert(0, 'data_vendor_id', data_vendor)

                df_to_sql(clean_data, self.db_location, 'minute_prices',
                          'append', q_code)
                print('Updated %s | %0.1f seconds' %
                      (q_code, time.time() - main_time_start))

        # The pricing database has prior values; append/replace new data points
        else:
            try:
                last_date = self.latest_prices.loc[q_code, 'date']
                raw_data = download_google_data(self.db_url, q_code)

                # Only keep data that is after the days_back period
                if self.data_process == 'replace' and self.days_back:
                    beg_date = (last_date - timedelta(days=self.days_back))
                    clean_data = raw_data[raw_data.date > beg_date.isoformat()]

                # Only keep data that is after the latest existing data point
                else:
                    clean_data = raw_data[raw_data.date > last_date.isoformat()]
            except Exception as e:
                print('Failed to determine what data is new for %s in extractor'
                      % q_code)
                print(e)
                return

            # There is not new data, so do nothing to the database
            if len(clean_data.index) == 0:
                print('No update for %s | %0.1f seconds' %
                      (q_code, time.time() - main_time_start))
            # There is new data to add to the database
            else:
                # Find the data vendor of the q_code; add it to the DataFrame
                data_vendor = self.retrieve_data_vendor_id('Google_Finance')
                clean_data.insert(0, 'data_vendor_id', data_vendor)

                # If replacing existing data, delete the overlapping data points
                if self.data_process == 'replace' and self.days_back:
                    # Data should be oldest to newest; gets the oldest date, as
                    #   any date between that and the latest date need to be
                    #   deleted before the new data can be added.
                    first_date_iso = clean_data['date'].min()

                    query = ("""DELETE FROM minute_prices
                                WHERE q_code_id='%s'
                                AND date>='%s'""" % (q_code, first_date_iso))
                    del_success = delete_sql_table_rows(self.db_location,
                                                        query, 'minute_prices',
                                                        q_code)
                    # If unable to delete existing data, skip ticker
                    if del_success == 'failure':
                        return

                # Append the new data to the end, regardless of replacement
                df_to_sql(clean_data, self.db_location, 'minute_prices',
                          'append', q_code)
                print('Updated %s | %0.1f seconds' %
                      (q_code, time.time() - main_time_start))

    def retrieve_data_vendor_id(self, name):
        """ Takes the name provided and tries to find data vendor from the
        data_vendor table in the database. If nothing is returned in the
        query, then 'Unknown' is used.

        :param name: A string that has the database name
        :return: A string with the data vendor's id number, or 'Unknown'
        """

        try:
            conn = sqlite3.connect(self.db_location)
            with conn:
                cur = conn.cursor()
                cur.execute("""SELECT data_vendor_id
                               FROM data_vendor
                               WHERE name=?
                               LIMIT 1""",
                            (name,))
                data = cur.fetchone()
                if data:    # A q_code was found
                    data = data[0]
                else:
                    data = 'Unknown'
                    print('Not able to determine the data_vendor_id for %s'
                          % name)
                return data
        except sqlite3.Error as e:
            print('Error when trying to retrieve data from database in '
                  'retrieve_data_vendor_id')
            print(e)
