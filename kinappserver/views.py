"""
The Kin App Server API is defined here.
"""
from threading import Thread
from uuid import UUID

from flask import request, jsonify, abort
from flask_api import status
import redis_lock
import arrow

from kinappserver import app, config, stellar, utils, ssm
from kinappserver.stellar import create_account, send_kin
from kinappserver.utils import InvalidUsage, InternalError, errors_to_string, increment_metric, MAX_TXS_PER_USER, get_global_config, extract_phone_number_from_firebase_id_token, sqlalchemy_pool_status
from kinappserver.models import create_user, update_user_token, update_user_app_version, \
    store_task_results, add_task, get_tasks_for_user, is_onboarded, \
    set_onboarded, send_push_tx_completed, send_engagement_push, \
    create_tx, get_reward_for_task, add_offer, \
    get_offers_for_user, set_offer_active, create_order, process_order, \
    create_good, list_inventory, release_unclaimed_goods, get_tokens_for_push, \
    list_user_transactions, get_redeemed_items, get_offer_details, get_task_details, set_delay_days,\
    add_p2p_tx, set_user_phone_number, match_phone_number_to_address, user_deactivated, get_pa_for_users,\
    handle_task_results_resubmission, reject_premature_results, find_missing_txs, get_address_by_userid, send_compensated_push,\
    list_p2p_transactions_for_user_id, nuke_user_data, send_push_auth_token, ack_auth_token, is_user_authenticated


def limit_to_local_host():
    """aborts non-local requests for sensitive APIs (nginx specific). allow on DEBUG"""
    if config.DEBUG or request.headers.get('X-Forwarded-For', None) is None:
        pass
    else:
        abort(403)  # Forbidden


@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    # converts exceptions to responses
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


@app.errorhandler(InternalError)
def handle_internal_error(error):
    # converts exceptions to responses
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


def extract_header(request):
    """extracts the user_id from the request header"""
    try:
        return request.headers.get('X-USERID')
    except Exception as e:
        print('cant extract user_id from header')
        raise InvalidUsage('bad header')


@app.route('/health', methods=['GET'])
def get_health():
    """health endpoint"""
    return jsonify(status='ok')


@app.route('/user/app-launch', methods=['POST'])
def app_launch():
    """called whenever the app is launched

        updates the user's last-login time,
        also forwards some config items to the client
    """
    payload = request.get_json(silent=True)
    app_ver, user_id = None, None
    try:
        user_id = extract_header(request)
        app_ver = payload.get('app_ver', None)
    except Exception as e:
        raise InvalidUsage('bad-request')

    update_user_app_version(user_id, app_ver)

    # send auth token if needed
    send_push_auth_token(user_id)

    return jsonify(status='ok', config=get_global_config())


@app.route('/user/contact', methods=['POST'])
def get_address_by_phone_api():
    """tries to match the given contact info against a user"""
    if not config.P2P_TRANSFERS_ENABLED:
        # this api is disabled, clients should not have asked for it
        print('/user/contact api is disabled by server config')
        raise InvalidUsage('api-disabled')

    payload = request.get_json(silent=True)
    try:
        user_id = extract_header(request)
        phone_number = payload.get('phone_number', None)
        if None in (user_id, phone_number):
            raise InvalidUsage('bad-request')
    except Exception as e:
        print(e)
        raise InvalidUsage('bad-request')
    address = match_phone_number_to_address(phone_number, user_id)
    if not address:
        return jsonify(status='error', reason='no_match'), status.HTTP_404_NOT_FOUND
    print('translated contact request into address: %s' % address)
    return jsonify(status='ok', address=address)


@app.route('/user/auth/ack', methods=['POST'])
def ack_auth_token_api():
    """endpoint used by clients to ack the auth-token they received"""
    payload = request.get_json(silent=True)
    try:
        user_id = extract_header(request)
        token = payload.get('token', None)
        if None in (user_id, token):
            raise InvalidUsage('bad-request: invalid input')
    except Exception as e:
        print(e)
        raise InvalidUsage('bad-request')

    if ack_auth_token(user_id, token):
        increment_metric('auth-token-acked')
        return jsonify(status='ok')
    else:
        return jsonify(status='error', reason='wrong-token'), status.HTTP_400_BAD_REQUEST


@app.route('/user/firebase/update-id-token', methods=['POST'])
def set_user_phone_number_api():
    """get the firebase id token and extract the phone number from it"""
    payload = request.get_json(silent=True)
    try:
        user_id = extract_header(request)
        token = payload.get('token', None)
        unverified_phone_number = payload.get('phone_number', None)  # only used in tests
        if None in (user_id, token, unverified_phone_number):
            raise InvalidUsage('bad-request')
    except Exception as e:
        print(e)
        raise InvalidUsage('bad-request')
    if not config.DEBUG:
        print('extracting verified phone number fom firebase id token...')
        verified_number = extract_phone_number_from_firebase_id_token(token)
        if verified_number is None:
            print('bad id-token: %s' % token)
            return jsonify(status='error', reason='bad_token'), status.HTTP_404_NOT_FOUND
        phone = verified_number
    else:
        # for tests, you can use the unverified number
        print('using un-verified phone number')
        phone = unverified_phone_number

    print('updating phone number for user %s' % user_id)
    set_user_phone_number(user_id, phone)

    return jsonify(status='ok')


@app.route('/user/update-token', methods=['POST'])
def update_token_api():
    """updates a user's token in the database """
    payload = request.get_json(silent=True)
    try:
        user_id = extract_header(request)
        token = payload.get('token', None)
        if None in (user_id, token):
            raise InvalidUsage('bad-request')
    except Exception as e:
        print(e)
        raise InvalidUsage('bad-request')

    print('updating token for user %s' % user_id)
    update_user_token(user_id, token)

    # send auth token now that we have push token
    send_push_auth_token(user_id)

    return jsonify(status='ok')


@app.route('/user/push/update-token', methods=['POST'])
def push_update_token_api():
    """updates a user's token in the database """
    payload = request.get_json(silent=True)
    try:
        user_id = extract_header(request)
        token = payload.get('token', None)
        if None in (user_id, token):
            raise InvalidUsage('bad-request')
    except Exception as e:
        print(e)
        raise InvalidUsage('bad-request')

    print('updating token for user %s' % user_id)
    update_user_token(user_id, token)

    # send auth token now that we have push token
    send_push_auth_token(user_id)

    return jsonify(status='ok')


@app.route('/user/task/results', methods=['POST'])
def quest_answers():
    """receive the results for a tasks and pay the user for them"""
    payload = request.get_json(silent=True)
    try:
        user_id = extract_header(request)
        task_id = payload.get('id', None)
        address = payload.get('address', None)
        results = payload.get('results', None)
        send_push = payload.get('send_push', True)
        if None in (user_id, task_id, address, results):
            print('failed input checks on /user/task/results')
            raise InvalidUsage('bad-request')
        # TODO more input checks here
    except Exception as e:
        raise InvalidUsage('bad-request')

    if user_deactivated(user_id):
        return jsonify(status='error', reason='user_deactivated'), status.HTTP_400_BAD_REQUEST

    if reject_premature_results(user_id):
        # should never happen: the client sent the results too soon
        print('rejecting user %s task %s results' % (user_id, task_id))
        increment_metric('premature_task_results')
        return jsonify(status='error', reason='cooldown_enforced'), status.HTTP_400_BAD_REQUEST

    # the following function handles task-results resubmission:

    # there are a few possible scenarios here:
    # the user already submitted these results and did get kins for them.
    # the user already submitted these results *as a different user* and get kins for them:
    # - in both these cases, simply find the memo, and return it to the user.

    # this case isn't handled (yet):
    # another set of cases is where the user DID NOT get compensated for the results.
    # in this case, we want to pay the user, but first to ensure she isn't already in the
    # process of being compensated (perhaps by another server).

    memo, compensated_user_id = handle_task_results_resubmission(user_id, task_id)
    if memo:
        print('detected resubmission of previously payed-for task by user_id: %s. memo:%s' % (compensated_user_id, memo))
        # this task was already submitted - and compensated, so just re-return the memo to the user.
        return jsonify(status='ok', memo=str(memo))

    # this should never fail for application-level reasons:
    if not store_task_results(user_id, task_id, results):
            raise InternalError('cant save results for userid %s' % user_id)
    try:
        memo = utils.generate_memo()
        reward_and_push(address, task_id, send_push, user_id, memo)
    except Exception as e:
        print('exception: %s' % e)
        print('failed to reward task %s at address %s' % (task_id, address))

    increment_metric('task_completed')
    return jsonify(status='ok', memo=str(memo))


@app.route('/task/add', methods=['POST'])
def add_task_api():
    """used to add tasks to the db"""
    if not config.DEBUG:
        limit_to_local_host()
    payload = request.get_json(silent=True)

    try:
        task = payload.get('task', None)
    except Exception as e:
        print('exception: %s' % e)
        raise InvalidUsage('bad-request')
    if add_task(task):
        return jsonify(status='ok')
    else:
        raise InvalidUsage('failed to add task')


@app.route('/pa/populate', methods=['POST'])
def get_pa_api():
    """used to populate user tables with public addresses"""
    # TODO REMOVE ME
    if not config.DEBUG:
        limit_to_local_host()

    get_pa_for_users()

    return jsonify(status='ok')


@app.route('/task/delay_days', methods=['POST'])
def set_delay_days_api():
    """used to set the delay_days on all tasks"""
    if not config.DEBUG:
        limit_to_local_host()
    payload = request.get_json(silent=True)
    try:
        delay_days = payload.get('days', None)
    except Exception as e:
        print('exception: %s' % e)
        raise InvalidUsage('bad-request')

    set_delay_days(delay_days)
    return jsonify(status='ok')


@app.route('/user/tasks', methods=['GET'])
def get_next_task():
    """returns the current task for the user with the given id"""
    user_id = extract_header(request)
    tasks = get_tasks_for_user(user_id)

    if user_deactivated(user_id):
        print('user %s is deactivated. returning empty task array' % user_id)
        return jsonify(tasks=[], reason='user_deactivated')

    try:
        # handle unprintable chars...
        print('tasks returned for user %s: %s' % (user_id, tasks))
    except Exception as e:
        print('cant print returned tasks for user %s' % user_id)
        print(e)
    return jsonify(tasks=tasks)


@app.route('/user/transactions', methods=['GET'])
def get_transactions_api():
    """return a list of the last 50 txs for this user

    each item in the list contains:
        - the tx_hash
        - tx direction (in, out)
        - amount of kins transferred
        - date
        - title and additional details
    """
    detailed_txs = []
    try:
        user_id = extract_header(request)
        server_txs = [{'type': 'server', 'tx_hash': tx.tx_hash, 'amount': tx.amount, 'client_received': not tx.incoming_tx, 'tx_info': tx.tx_info, 'date': arrow.get(tx.update_at).timestamp} for tx in list_user_transactions(user_id, MAX_TXS_PER_USER)]

        # get the offer, task details
        for tx in server_txs:
            details = get_offer_details(tx['tx_info']['offer_id']) if not tx['client_received'] else get_task_details(tx['tx_info']['task_id'])
            detailed_txs.append({**tx, **details})

        # get p2p details
        p2p_txs = [{'title': 'some title', 'description': 'some description', 'provider': 'some provider',
                    'type': 'p2p', 'tx_hash': tx.tx_hash, 'amount': tx.amount, 'client_received': str(tx.receiver_user_id).lower() == str(user_id), 'tx_info': '', 'date': arrow.get(tx.update_at).timestamp} for tx in list_p2p_transactions_for_user_id(user_id, MAX_TXS_PER_USER)]

        # merge txs:
        detailed_txs = detailed_txs + p2p_txs

        # sort by date
        print(detailed_txs)
        detailed_txs = sorted(detailed_txs, key=lambda k: k['date'], reverse=True)
        if len(detailed_txs) > MAX_TXS_PER_USER:
            detailed_txs = detailed_txs[:MAX_TXS_PER_USER]

    except Exception as e:
        print('cant get txs for user')
        print(e)
        return jsonify(status='error', txs=[])

    return jsonify(status='ok', txs=detailed_txs)


@app.route('/user/redeemed', methods=['GET'])
def user_redeemed_api():
    """return the list of offers that were redeemed by this user

    each item in the list contains:
        - the actual redeemed item (i.e. the code
        - localized time
        - info about the offer that was redeemed

        essentially, this is a 3-way join between the good, user and offer tables
        that is implemented iteratively. the implementation can be made more efficient
    """

    redeemed_goods = []
    try:
        user_id = extract_header(request)
        incoming_txs_hashes = [tx.tx_hash for tx in list_user_transactions(user_id) if tx.incoming_tx]
        # get an array of the goods and add details from the offer table:
        for good in get_redeemed_items(incoming_txs_hashes):
            # merge two dicts (python 3.5)
            redeemed_goods.append({**good, **get_offer_details(good['offer_id'])})

    except Exception as e:
        print('cant get redeemed items for user')
        print(e)

    return jsonify(status='ok', redeemed=redeemed_goods)


@app.route('/user/onboard', methods=['POST'])
def onboard_user():
    """creates a wallet for the user and deposits some xlms there"""
    # input sanity
    try:
        user_id = extract_header(request)
        public_address = request.get_json(silent=True).get('public_address', None)
        if None in (public_address, user_id):
            raise InvalidUsage('bad-request')
    except Exception as e:
        raise InvalidUsage('bad-request')

    # ensure the user exists but does not have an account:
    onboarded = is_onboarded(user_id)
    if onboarded is True:
        raise InvalidUsage('user already has an account')
    elif onboarded is None:
        raise InvalidUsage('no such user exists')
    else:
        # create an account, provided none is already being created
        lock = redis_lock.Lock(app.redis, 'address:%s' % public_address)
        if lock.acquire(blocking=False):
            try:
                print('creating account with address %s and amount %s' % (public_address, config.STELLAR_INITIAL_ACCOUNT_BALANCE))
                tx_id = create_account(public_address, config.STELLAR_INITIAL_ACCOUNT_BALANCE)
                if tx_id:
                    set_onboarded(user_id, True, public_address)
                else:
                    raise InternalError('failed to create account at %s' % public_address)
            except Exception as e:
                print('exception trying to create account:%s' % e)
                raise InternalError('unable to create account')
            else:
                print('created account %s with txid %s' % (public_address, tx_id))
            finally:
                lock.release()
        else:
            raise InvalidUsage('already creating account for user_id: %s and address: %s' % (user_id, public_address))

        increment_metric('user_onboarded')
        return jsonify(status='ok')


@app.route('/user/register', methods=['POST'])
def register_api():
    """ register a user to the system
    called once by every client until 200OK is received from the server.
    the payload may contain a optional push token.
    """
    payload = request.get_json(silent=True)
    try:
        # add redis lock here?
        user_id = payload.get('user_id', None)
        os = payload.get('os', None)
        device_model = payload.get('device_model', None)
        token = payload.get('token', None)
        time_zone = payload.get('time_zone', None)
        print('raw time_zone: %s' % time_zone)
        device_id = payload.get('device_id', None)
        app_ver = payload.get('app_ver', None)
        # TODO more input check on the values
        if None in (user_id, os, device_model, time_zone, app_ver):  # token is optional, device-id is required but may be None
            raise InvalidUsage('bad-request')
        if os not in (utils.OS_ANDROID, utils.OS_IOS):
            raise InvalidUsage('bad-request')
        user_id = UUID(user_id)  # throws exception on invalid uuid
    except Exception as e:
        raise InvalidUsage('bad-request')
    else:
        try:
            create_user(user_id, os, device_model, token, time_zone, device_id, app_ver)
        except InvalidUsage as e:
            raise InvalidUsage('duplicate-userid')
        else:
            print('created user with user_id %s' % user_id)
            increment_metric('user_registered')

            return jsonify(status='ok', config=get_global_config())


def reward_and_push(public_address, task_id, send_push, user_id, memo):
    """create a thread to perform this function in the background"""
    Thread(target=reward_address_for_task_internal, args=(public_address, task_id, send_push, user_id, memo)).start()


def reward_address_for_task_internal(public_address, task_id, send_push, user_id, memo):
    """transfer the correct amount of kins for the task to the given address

       this function runs in the background and sends a push message to the client to
       indicate that the money was indeed transferred.
    """
    # get reward amount from db
    amount = get_reward_for_task(task_id)
    if not amount:
        print('could not figure reward amount for task_id: %s' % task_id)
        raise InternalError('cant find reward for task_id %s' % task_id)
    try:
        # send the moneys
        print('calling send_kin: %s, %s' % (public_address, amount))
        tx_hash = send_kin(public_address, amount, memo)
    except Exception as e:
        print('caught exception sending %s kins to %s - exception: %s:' % (amount, public_address, e))
        raise InternalError('failed sending %s kins to %s' % (amount, public_address))
    finally:  # TODO dont do this if we fail with the tx
        create_tx(tx_hash, user_id, public_address, False, amount, {'task_id': task_id, 'memo': memo})
        if send_push:
            send_push_tx_completed(user_id, tx_hash, amount, task_id)


@app.route('/offer/add', methods=['POST'])
def add_offer_api():
    """internal endpoint used to populate the server with offers"""
    if not config.DEBUG:
        limit_to_local_host()
    payload = request.get_json(silent=True)
    try:
        offer = payload.get('offer', None)
        set_active = payload.get('set_active', False)  # optional
    except Exception as e:
        print('exception: %s' % e)
        raise InvalidUsage('bad-request')
    if add_offer(offer, set_active):
        return jsonify(status='ok')
    else:
        raise InvalidUsage('failed to add offer')


@app.route('/offer/set_active', methods=['POST'])
def set_active_api():
    """internal endpoint used to enables/disables an offer"""
    if not config.DEBUG:
        limit_to_local_host()
    payload = request.get_json(silent=True)
    try:
        offer_id = payload.get('id', None)
        is_active = payload.get('is_active', None)
    except Exception as e:
        print('exception: %s' % e)
        raise InvalidUsage('bad-request')
    if set_offer_active(offer_id, is_active):
        return jsonify(status='ok')
    else:
        raise InvalidUsage('failed to set offer status')


@app.route('/offer/book', methods=['POST'])
def book_offer_api():
    """books an offer by a user"""
    payload = request.get_json(silent=True)
    try:
        user_id = extract_header(request)
        offer_id = payload.get('id', None)
        if None in (user_id, offer_id):
            raise InvalidUsage('invalid payload')
    except Exception as e:
        raise InvalidUsage('bad-request')

    if config.AUTH_TOKEN_ENFORCED and not is_user_authenticated(user_id):
        print('user %s is not authenticated. rejecting book request' % user_id)
        increment_metric('book-rejected-on-auth')
        return jsonify(status='error', reason='auth-failed'), status.HTTP_400_BAD_REQUEST

    order_id, error_code = create_order(user_id, offer_id)
    if order_id:
        increment_metric('offers_booked')
        return jsonify(status='ok', order_id=order_id)
    else:
        return jsonify(status='error', reason=errors_to_string(error_code)), status.HTTP_400_BAD_REQUEST


@app.route('/user/offers', methods=['GET'])
def get_offers_api():
    """return the list of availble offers for this user"""
    try:
        user_id = extract_header(request)
        if user_id is None:
            raise InvalidUsage('no user_id')
    except Exception as e:
        print('exception: %s' % e)
        raise InvalidUsage('bad-request')
        #print('offers %s' % get_offers_for_user(user_id))
    return jsonify(offers=get_offers_for_user(user_id))


@app.route('/offer/redeem', methods=['POST'])
def purchase_api():
    """process the given tx_hash and return the payed-for goods"""

    # TODO: at some point we should try to listen in on incoming tx_hashes
    # for our account(s). this should hasten the process of redeeming offers.
    payload = request.get_json(silent=True)
    try:
        user_id = extract_header(request)
        tx_hash = payload.get('tx_hash', None)
        if None in (user_id, tx_hash):
            raise InvalidUsage('invalid param')
    except Exception as e:
        print('exception: %s' % e)
        raise InvalidUsage('bad-request')

    try:
        # process the tx_hash, provided its not already being processed by another server
        lock = redis_lock.Lock(app.redis, 'redeem:%s' % tx_hash)
        if lock.acquire(blocking=False):
            success, goods = process_order(user_id, tx_hash)
            if not success:
                raise InvalidUsage('cant redeem with tx_hash:%s' % tx_hash)
            increment_metric('offers_redeemed')
            print('redeemed order by user_id: %s' % user_id)
            return jsonify(status='ok', goods=goods)
        else:
            return jsonify(status='error', reason='already processing tx_hash')
    finally:
            lock.release()


@app.route('/good/add', methods=['POST'])
def add_good_api():
    """internal endpoint used to populate the server with goods"""
    if not config.DEBUG:
        limit_to_local_host()
    payload = request.get_json(silent=True)
    try:
        offer_id = payload.get('offer_id', None)
        good_type = payload.get('good_type', None)
        value = payload.get('value', None)
        if None in (offer_id, good_type, value):
            raise InvalidUsage('invalid params')
    except Exception as e:
        print('exception: %s' % e)
        raise InvalidUsage('bad-request')
    if create_good(offer_id, good_type, value):
        return jsonify(status='ok')
    else:
        raise InvalidUsage('failed to add good')


@app.route('/good/inventory', methods=['GET'])
def inventory_api():
    """internal endpoint used to list the goods inventory"""
    if not config.DEBUG:
        limit_to_local_host()
    return jsonify(status='ok', inventory=list_inventory())


@app.route('/stats/db', methods=['GET'])
def dbstats_api():
    """internal endpoint used to retrieve the number of db connections"""
    if not config.DEBUG:
        limit_to_local_host()
    return jsonify(status='ok', stats=sqlalchemy_pool_status())


@app.route('/balance', methods=['GET'])
def balance_api():
    """endpoint used to get the current balance of the seed and channels"""
    if not config.DEBUG:
        limit_to_local_host()

    base_seed, channel_seeds = ssm.get_stellar_credentials()
    print('channel seeds: %s' % channel_seeds)

    balance = {'base_seed': {}, 'channel_seeds': {0: {}}}

    from stellar_base.keypair import Keypair
    balance['base_seed']['kin'] = stellar.get_kin_balance(Keypair.from_seed(base_seed).address().decode())
    balance['base_seed']['xlm'] = stellar.get_xlm_balance(Keypair.from_seed(base_seed).address().decode())
    #todo print xlm balance for each seed
    #if channel_seed:
    #    # seeds only need to carry XLMs
    #   balance['channel_seeds'][0]['xlm'] = stellar.get_xlm_balance(Keypair.from_seed(channel_seed).address().decode())

    return jsonify(status='ok', balance=balance)


@app.route('/good/release_unclaimed', methods=['GET'])
def release_unclaimed_api():
    """endpoint used to release goods that were booked but never redeemed"""
    if not config.DEBUG:
        limit_to_local_host()
    released = release_unclaimed_goods()
    increment_metric('unclaimed_released', released)
    return jsonify(status='ok', released=released)


@app.route('/engagement/send', methods=['GET'])
def send_engagemnt_api():
    """endpoint used to send engagement push notifications to users by scheme. password protected"""
    if not config.DEBUG:
        limit_to_local_host()

    args = request.args
    scheme = args.get('scheme')
    if scheme is None:
        raise InvalidUsage('invalid param')

    dry_run = args.get('dryrun', 'True') == 'True'

    tokens = get_tokens_for_push(scheme)
    if tokens is None:
        raise InvalidUsage('invalid scheme')

    print('gathered %d ios tokens and %d gcm tokens for scheme: %s, dry-run:%s' % (len(tokens[utils.OS_IOS]), len(tokens[utils.OS_ANDROID]), scheme, dry_run))

    if dry_run:
        print('send_engagement_api - dry_run - not sending push')
    else:
        print('sending push ios %d tokens' % len(tokens[utils.OS_IOS]))
        for token in tokens[utils.OS_IOS]:
            send_engagement_push(None, scheme, token, utils.OS_IOS)
        print('sending push android %d tokens' % len(tokens[utils.OS_ANDROID]))
        for token in tokens[utils.OS_ANDROID]:
            send_engagement_push(None, scheme, token, utils.OS_ANDROID)

    return jsonify(status='ok')


@app.route('/user/transaction/p2p', methods=['POST'])
def report_p2p_tx_api():
    """endpoint used by the client to report successful p2p txs"""

    if not config.P2P_TRANSFERS_ENABLED:
        # this api is disabled, clients should not have asked for it
        print('/user/transaction/p2p/add api is disabled by server config')
        raise InvalidUsage('api-disabled')

    payload = request.get_json(silent=True)
    try:
        # TODO Should we verify the tx against the blockchain?
        # TODO this api needs to be secured with auth token
        sender_id = extract_header(request)
        tx_hash = payload.get('tx_hash', None)
        destination_address = payload.get('destination_address', None)
        amount = payload.get('amount', None)
        if None in (tx_hash, sender_id, destination_address, amount):
            raise InvalidUsage('invalid params')

    except Exception as e:
        print('exception: %s' % e)
        raise InvalidUsage('bad-request')
    if add_p2p_tx(tx_hash, sender_id, destination_address, amount):
        return jsonify(status='ok')
    else:
        raise InvalidUsage('failed to add p2ptx')


@app.route('/users/missing_txs', methods=['GET'])
def fix_users_api():
    """internal endpoint used to list problems with user data"""
    if not config.DEBUG:
        limit_to_local_host()
    missing_txs = find_missing_txs()
    print('missing_txs: found %s items' % len(missing_txs))
    # sort results by date (4th item in each tuple)
    missing_txs.sort(key=lambda tup: tup[3])
    return jsonify(status='ok', missing_txs=missing_txs)


@app.route('/user/compensate', methods=['POST'])
def compensate_user_api():
    """internal endpoint used to manually compensate users for missing txs"""
    if not config.DEBUG:
        limit_to_local_host()

    # for security reasons, I'm disabling this api.
    # remove the 'return' line to re-enable it.
    return

    payload = request.get_json(silent=True)
    user_id = payload.get('user_id', None)
    kin_amount = int(payload.get('kin_amount', None))
    task_id = payload.get('task_id', None)
    memo = utils.generate_memo(is_manual=True)
    if None in (user_id, kin_amount, task_id):
        raise InvalidUsage('invalid param')
    public_address = get_address_by_userid(user_id)
    if not public_address:
        print('cant compensate user %s - no public address' % user_id)
        return jsonify(status='error', reason='no_public_address')

    user_tx_task_ids = [tx.tx_info.get('task_id', '-1') for tx in list_user_transactions(user_id)]
    if task_id in user_tx_task_ids:
        print('refusing to compensate user %s for task %s - already received funds!' % (user_id, task_id))
        return jsonify(status='error', reason='already_compensated')

    print('calling send_kin: %s, %s' % (public_address, kin_amount))
    try:
        tx_hash = send_kin(public_address, kin_amount, memo)
        create_tx(tx_hash, user_id, public_address, False, kin_amount, {'task_id': task_id, 'memo': memo})
    except Exception as e:
        print('error attempting to compensate user %s for task %s' % (user_id, task_id))
        print(e)
        return jsonify(status='error', reason='internal_error')
    else:
        print('compensated user %s with %s kins for task_id %s' % (user_id, kin_amount, task_id))
        # also send push to the user
        task_title = get_task_details(task_id)['title']
        send_compensated_push(user_id, kin_amount, task_title)

        return jsonify(status='ok', tx_hash=tx_hash)


@app.route('/user/nuke-data', methods=['POST'])
def nuke_user_api():
    """internal endpoint used to nuke a user's task and tx data. use with care"""
    if not config.DEBUG:
        limit_to_local_host()

    try:
        payload = request.get_json(silent=True)
        phone_number = payload.get('phone_number', None)
        nuke_all = payload.get('nuke_all', False) == True
        if None in (phone_number,):
            raise InvalidUsage('bad-request')
    except Exception as e:
        print(e)
        raise InvalidUsage('bad-request')

    user_ids = nuke_user_data(phone_number, nuke_all)
    if user_ids is None:
        print('could not find any user with this number: %s' % phone_number)
        return jsonify(status='error', reason='no_user')
    else:
        print('nuked users with phone number: %s and user_ids %s' % (phone_number, user_ids))
        return jsonify(status='ok', user_id=user_ids)
