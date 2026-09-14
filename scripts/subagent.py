#!/usr/bin/env python3
"""Local Task Router client. submit -> get -> review; no credential handling."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.parse
import urllib.request


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:3459')
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('accounts')
    commands.add_parser('probe')
    submit = commands.add_parser('submit')
    submit.add_argument('--request-file', type=Path, required=True)
    submit.add_argument('--idempotency-key', help='8–200 characters; reuse after an uncertain response')
    submit.add_argument('--new-run', action='store_true', help='create a new run even when the request file is unchanged')
    for name in ('get', 'cancel', 'review'):
        child = commands.add_parser(name); child.add_argument('task_id')
        if name == 'review':
            child.add_argument('--passed', choices=['yes', 'no'], required=True)
            child.add_argument('--note', default='')
    args = p.parse_args()
    url = urllib.parse.urlsplit(args.url)
    if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost'} or url.username or url.password:
        p.error('Only a local HTTP Task Router URL is supported')
    body = None
    if args.command == 'accounts': path = '/accounts'
    elif args.command == 'probe': path, body = '/accounts/workbuddy/probe', {}
    elif args.command == 'submit':
        path, body = '/subagents', json.loads(args.request_file.read_text())
    else:
        path = '/subagents/' + urllib.parse.quote(args.task_id, safe='')
        if args.command == 'cancel': path += '/cancel'; body = {}
        if args.command == 'review': path += '/review'; body = {'passed':args.passed == 'yes', 'note':args.note}
    headers = {'Content-Type':'application/json', 'X-TaskRouter-Local':'1'}
    if args.command == 'submit':
        if args.idempotency_key and args.new_run:
            p.error('--idempotency-key and --new-run cannot be used together')
        headers['Idempotency-Key'] = args.idempotency_key or (
            'run:' + secrets.token_hex(12) if args.new_run else
            'file-sha256:' + hashlib.sha256(args.request_file.read_bytes()).hexdigest()
        )
    request = urllib.request.Request(args.url.rstrip('/') + '/api' + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=75) as response: data = json.load(response)
    except urllib.error.HTTPError as e:
        p.exit(1, e.read().decode() + '\n')
    except urllib.error.URLError as e:
        p.exit(1, 'Local Task Router unavailable: ' + str(e.reason) + '\n')
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
