import uuid

from kinappserver import app, config
from kinappserver.utils import InvalidUsage, OS_ANDROID, increment_metric

PLEASE_UPGRADE_COOLDOWN_SECONDS = 60
COUNTRY_NOT_SUPPORTED_PUSH_COOLDOWN_SECONDS = 60 * 60 * 8


def generate_push_id():
    return uuid.uuid4().hex


def engagement_payload_apns(push_type):
    push_id = generate_push_id()
    # TODO report push_id
    if push_type in ('engage-recent', 'engage-old'):
        return apns_payload("", "Let's earn some Kin!", push_type, push_id)


def engagement_payload_gcm(push_type):
    push_id = generate_push_id()
    # TODO report push_id
    if push_type in ('engage-recent', 'engage-old'):
        return gcm_payload(push_type, push_id, {'title': '', 'body': "Let's earn some Kin!"})
    else:
        raise InvalidUsage('no such push type: %s' % push_type)


def auth_push_apns(push_id, auth_token, user_id):
    payload_dict = {'aps': {"content-available": 1, "sound": ""}, 'kin': {'push_type': 'auth', 'push_id': push_id, 'auth_data': {'auth_token': auth_token, 'user_id': user_id}}}
    print('the apns payload for auth token: %s' % payload_dict)
    return payload_dict


def tx_completed_push_apns(push_id, tx_hash, user_id, task_id, kin_amount, memo):
    payload_dict = {'aps': {"content-available": 1, "sound": ""}, 'kin': {'push_type': 'tx_completed', 'push_id': push_id, 'tx_data': {'tx_hash': tx_hash, 'user_id': user_id, 'task_id': task_id, 'kin': kin_amount, 'memo': memo}}}
    print('the apns payload for tx_completed: %s' % payload_dict)
    return payload_dict


def register_push_apns(push_id):
    payload_dict = {'aps': {"content-available": 1, "sound": ""}, 'kin': {'push_type': 'register', 'push_id': push_id}}
    return payload_dict


def compensated_payload_apns(amount, task_title):
    push_id = generate_push_id()
    # TODO report push_id
    return apns_payload("", "You have been awarded %s KIN for completing task \"%s\"" % (amount, task_title), 'engage-recent', push_id)


def compensated_payload_gcm(amount, task_title):
    push_id = generate_push_id()
    return gcm_payload('engage-recent', push_id, {'title': '', 'body': "You have been awarded %s KIN for completing task \"%s\"" % (amount, task_title)})


def send_p2p_push(user_id, amount, tx_dict):
    """sends a push to the given userid to inform of p2p tx"""
    push_id = generate_push_id()
    push_type = 'engage-recent'
    from kinappserver.models import get_user_push_data
    os_type, token, push_env = get_user_push_data(user_id)
    if token:
        if os_type == OS_ANDROID:
            increment_metric('p2p-tx-push-gcm')
            print('sending p2p-tx push message to GCM user %s' % user_id)
            push_send_gcm(token, gcm_payload(push_type, push_id, {'title': '', 'body': "A friend just sent you %sKIN!" % amount}), push_env)
        else:
            increment_metric('p2p-tx-push-ios')
            print('sending p2p-tx push message to APNS user %s' % user_id)
            push_send_apns(token, apns_payload("", "A friend just sent you %sKIN!" % amount, 'p2p_received', push_id, 'default', {'tx': tx_dict}), push_env)
    else:
        print('not sending p2p-tx push to user_id %s: no token' % user_id)
    return


def send_country_IS_supported(user_id):
    """sends a push to the given userid to tell them their country isnt supported"""
    #  add cooldown with redis to this function.
    if not (app.redis.set('countryis:%s' % str(user_id), '', ex=COUNTRY_NOT_SUPPORTED_PUSH_COOLDOWN_SECONDS, nx=True)):
        # returns None if already exists
        return

    push_id = generate_push_id()
    push_type = 'country_is_supported'
    from kinappserver.models import get_user_push_data
    os_type, token, push_env = get_user_push_data(user_id)
    if token:
        if os_type == OS_ANDROID:
            increment_metric('country_is_supported-android')
            # return  # not supported yet
            print('sending country_is_supported push message to GCM user %s' % user_id)
            push_send_gcm(token, gcm_payload(push_type, push_id, {'title': 'Business as Usual', 'body': "Kinit is back on track and is now available again."}), push_env)

        else:
            increment_metric('country_is_supported-ios')
            print('sending country_is_supported push message to APNS user %s' % user_id)
            push_send_apns(token, apns_payload("Business as Usual", "Kinit is back on track and is now available again.", push_type, push_id), push_env)
    else:
        print('not sending country_is_supported push to user_id %s: no token' % user_id)
    return


def send_country_not_supported(user_id):
    """sends a push to the given userid to tell them their country isnt supported"""
    #  add cooldown with redis to this function.
    if not (app.redis.set('countrynot:%s' % str(user_id), '', ex=COUNTRY_NOT_SUPPORTED_PUSH_COOLDOWN_SECONDS, nx=True)):
        # returns None if already exists
        return

    push_id = generate_push_id()
    push_type = 'country_not_supported'
    from kinappserver.models import get_user_push_data
    os_type, token, push_env = get_user_push_data(user_id)
    if token:
        if os_type == OS_ANDROID:
            increment_metric('country_not_supported-android')
            # return  # not supported yet
            print('sending country_not_supported push message to GCM user %s' % user_id)
            push_send_gcm(token, gcm_payload(push_type, push_id, {'title': 'Oh no!', 'body': "Kinit is currently not available in your country. We are continuing to grow, so check back again soon."}), push_env)

        else:
            increment_metric('country_not_supported-ios')
            print('sending country_not_supported push message to APNS user %s' % user_id)
            push_send_apns(token, apns_payload("Oh no!", "Kinit is currently not available in your country. We are continuing to grow, so check back again soon.", push_type, push_id), push_env)
    else:
        print('not sending country_not_supported push to user_id %s: no token' % user_id)
    return


def send_please_upgrade_push(user_id):
    """sends a push to the given userid to please upgrade"""
    #  add cooldown with redis to this function.
    if not (app.redis.set('plsupgr:%s' % str(user_id), '', ex=PLEASE_UPGRADE_COOLDOWN_SECONDS, nx=True)):
        # returns None if already exists
        return

    push_id = generate_push_id()
    push_type = 'please_upgrade'
    from kinappserver.models import get_user_push_data
    os_type, token, push_env = get_user_push_data(user_id)
    if token:
        if os_type == OS_ANDROID:
            increment_metric('pleaseupgrade-android')
            #return  # not supported yet
            print('sending please-upgrade push message to GCM user %s' % user_id)
            push_send_gcm(token, gcm_payload(push_type, push_id, {'title': '', 'body': "Please upgrade the app to get the next task"}),push_env)

        else:
            increment_metric('pleaseupgrade-ios')
            print('sending please-upgrade push message to APNS user %s' % user_id)
            push_send_apns(token, apns_payload("", "Please upgrade the app to get the next task", push_type, push_id), push_env)
    else:
        print('not sending please-upgrade push to user_id %s: no token' % user_id)
    return


def send_please_upgrade_push_2(user_ids):
    for user_id in user_ids:
        send_please_upgrade_push_2_inner(user_id)


def send_please_upgrade_push_2_inner(user_id):
    """sends a push to the given userid to please upgrade"""
    push_id = generate_push_id()
    push_type = 'please_upgrade'
    from kinappserver.models import get_user_push_data
    os_type, token, push_env = get_user_push_data(user_id)
    if token:
        if os_type == OS_ANDROID:
            increment_metric('pleaseupgrade-android')
            print('sending please-upgrade push message to GCM user %s' % user_id)
            push_send_gcm(token, gcm_payload('engage-recent', push_id, {'title': '', 'body': "Your current version of Kinit is no longer supported. Please download the newest version from Google Play"}), push_env)
        else:
            increment_metric('pleaseupgrade-ios')
            print('sending please-upgrade push message to APNS user %s' % user_id)
            push_send_apns(token, apns_payload("", "Your current version of Kinit is no longer supported. Please download the newest version from the App Store", push_type, push_id), push_env)
    else:
        print('not sending please-upgrade push to user_id %s: no token' % user_id)
    return


def apns_payload(title, body, push_type, push_id, sound='default', extra_payload_dict=None):
    """generate an apns payload"""
    payload_dict = {'aps': {'alert': {'title': title, 'body': body}, 'sound': sound}, 'kin': {'push_type': push_type, 'push_id': push_id}}
    if extra_payload_dict:
        payload_dict['kin'].update(extra_payload_dict)

    print('the apns payload: %s' % payload_dict)
    return payload_dict


def gcm_payload(push_type, push_id, message_dict):
    payload = {
            'push_type': push_type,
            'push_id': push_id,
            'message': message_dict
        }
    return payload


def push_send_gcm(token, payload, push_env):
    if config.DEPLOYMENT_ENV == 'test':
        print('skipping push on test env')
        return

    if push_env != 'beta':
        print('error: cant send gcm over push env: %s. only beta is currently supported' % push_env)
        return

    app.amqp_publisher_beta.send_gcm("eshu-key-beta", payload, [token], False, config.PUSH_TTL_SECS)


def push_send_apns(token, payload, push_env):
    if config.DEPLOYMENT_ENV == 'test':
        print('skipping push on test env')
        return
    if push_env == 'beta':
        print('pushing on apns beta channel')
        app.amqp_publisher_beta.send_apns("eshu-key-beta", payload, [token])
    else:
        print('pushing on apns release channel')
        app.amqp_publisher_release.send_apns("eshu-key-release", payload, [token])
