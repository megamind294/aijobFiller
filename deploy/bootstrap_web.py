"""Run as root on Ubuntu; generates credentials without printing them."""
import argparse
import json
import os
import pwd
import re
import secrets
from pathlib import Path

from werkzeug.security import generate_password_hash


def private_write(path, content, uid, gid):
    temporary = path.with_suffix(path.suffix + '.new')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(content)
    os.chown(temporary, uid, gid)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hostname', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,250}[a-z0-9]', args.hostname):
        raise SystemExit('Invalid hostname')
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    account = pwd.getpwnam('jobpilot')
    home = Path('/var/lib/jobpilot')
    home.mkdir(mode=0o700, exist_ok=True)
    os.chown(home, account.pw_uid, account.pw_gid)
    auth_path = home / 'web-auth.json'
    if not auth_path.exists():
        password = secrets.token_urlsafe(24)
        record = {'username': 'admin', 'password_hash': generate_password_hash(password),
                  'version': secrets.token_hex(24)}
        private_write(auth_path, json.dumps(record), account.pw_uid, account.pw_gid)
        handoff = f'JobPilot\nURL: https://{args.hostname}\nUsername: admin\nInitial password: {password}\n\nChange the password in Account settings after signing in.\n'
        private_write(home / 'initial-web-login.txt', handoff, account.pw_uid, account.pw_gid)
    environment_path = Path('/etc/jobpilot-web.env')
    existing = {}
    if environment_path.exists():
        for line in environment_path.read_text().splitlines():
            if '=' in line:
                name, value = line.split('=', 1)
                existing[name] = value
    secret = existing.get('JOBPILOT_WEB_SECRET') or secrets.token_urlsafe(48)
    content = f'JOBPILOT_HOME=/var/lib/jobpilot\nJOBPILOT_WEB_SECRET={secret}\nJOBPILOT_WEB_HOST={args.hostname}\n'
    private_write(environment_path, content, 0, 0)
    print('Private web credentials and service configuration initialized.')


if __name__ == '__main__':
    main()
