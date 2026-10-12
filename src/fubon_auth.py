from pathlib import Path

SECRET_NAMES = ('FUBON_PERSONAL_ID', 'FUBON_API_KEY', 'FUBON_CERT_PASSWORD', 'FUBON_CERT_PATH')


def load_secrets(getter):
    values = {}
    for name in SECRET_NAMES:
        try:
            value = getter(name)
        except Exception:
            raise RuntimeError(f'{name}: unavailable') from None
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f'{name}: missing')
        values[name] = value
    for name in SECRET_NAMES[:3]:
        print(f'{name}: configured')
    return values


def validate_certificate(values):
    if not Path(values['FUBON_CERT_PATH']).is_file():
        raise RuntimeError('FUBON_CERT_PATH: file not found')
    print('FUBON_CERT_PATH: exists')


def login(values, sdk_factory=None):
    if sdk_factory is None:
        from fubon_neo.sdk import FubonSDK
        sdk_factory = FubonSDK
    sdk = sdk_factory()
    try:
        result = sdk.apikey_login(values['FUBON_PERSONAL_ID'], values['FUBON_API_KEY'],
                                  values['FUBON_CERT_PATH'], values['FUBON_CERT_PASSWORD'])
    except Exception:
        raise RuntimeError('API Key login failed; credentials and SDK message suppressed') from None
    if not getattr(result, 'is_success', False):
        raise RuntimeError('API Key login unsuccessful; raw response suppressed')
    accounts = getattr(result, 'data', None) or []
    types = []
    for account in accounts:
        kind = str(getattr(account, 'account_type', 'unknown'))
        number = str(getattr(account, 'account', ''))
        # Never reveal a full short identifier.
        print('account_type =', kind, 'account =', '****' + number[-4:] if len(number) > 4 else '****')
        types.append(kind)
    return sdk, {'login_status': 'PASS', 'account_types': types,
                 'futopt_account': any('fut' in t.lower() for t in types)}
