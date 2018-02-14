# tester config file. should be overwritten by ansible in prod/stage.

DEBUG = True
DB_CONNSTR = "this gets overwritten by the tester code. it acutally uses a temp postgress db on the local disc"
REDIS_ENDPOINT = 'localhost'
REDIS_PORT = 6379

STELLAR_TIMEOUT_SEC = 10 
STELLAR_INITIAL_ACCOUNT_BALANCE = 10

ESHU_USERNAME = ''
ESHU_PASSWORD = ''
ESHU_HEARTBEAT = ''
ESHU_APPID = ''
ESHU_VIRTUAL_HOST = ''
ESHU_EXCHANGE = ''
ESHU_QUEUE = ''
ESHU_RABBIT_ADDRESS = ''

STELLAR_BASE_SEED = 'SDEMLLRTVT3AORRSHKAIRYOMW6UE2GKNUYMAVRLZ3EDML4JTADF4BEBO'
STELLAR_HORIZON_URL = 'https://horizon-testnet.stellar.org/'
STELLAR_NETWORK = 'TESTNET'
STELLAR_CHANNEL_SEEDS = ['SB7FTL22P4LCTNRI43EOLXTLPGNQ6OX6FQZ6A5P6T4S7YYX5YVFNQTRG']
STELLAR_KIN_ISSUER_ADDRESS = 'GCKG5WGBIJP74UDNRIRDFGENNIH5Y3KBI5IHREFAJKV4MQXLELT7EX6V'
