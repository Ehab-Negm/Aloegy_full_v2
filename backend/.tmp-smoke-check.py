import json
from urllib import request, error

base = 'http://127.0.0.1:8012'

def req(method, path, data=None, headers=None):
    payload = None if data is None else json.dumps(data).encode('utf-8')
    req = request.Request(base + path, data=payload, headers=headers or {}, method=method)
    if payload is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode('utf-8')
            try:
                return resp.status, json.loads(text)
            except Exception:
                return resp.status, text
    except error.HTTPError as exc:
        text = exc.read().decode('utf-8')
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text

for otp in ('000000', '123456'):
    pass

status, body = req('POST', '/auth/send-otp', {'phone': '+201094321642'})
print('send_otp', status, json.dumps(body, ensure_ascii=False))
status, body = req('POST', '/auth/verify-otp', {'phone': '+201094321642', 'otp': '000000'})
print('verify_bad', status, json.dumps(body, ensure_ascii=False))
status, body = req('POST', '/auth/verify-otp', {'phone': '+201094321642', 'otp': '123456'})
print('verify_bypass', status, json.dumps(body, ensure_ascii=False))
status, body = req('POST', '/demo/livekit-session', {'restaurantId': 'demo-restaurant', 'participantName': 'Smoke Test'})
print('demo_session', status, json.dumps(body, ensure_ascii=False))
