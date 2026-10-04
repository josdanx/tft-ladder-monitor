"""Compete TFT rank monitor. Python 3.10+, no third-party packages."""
import argparse
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config.json'
STATE = ROOT / 'state.json'
PLAYER = 'JosDanX#EUW'
TOURNAMENT = '116912881529684867'
PAGE = f'https://competetft.com/en-GB/competetft/tournament/{TOURNAMENT}/qualification/tac/ladder?shard=EUW1'
OPERATION = 'GetCompeteTournamentLadderQualification'
HASH = 'af9f1dd75f86c26e31ec4e68ed1d86b7c2f028a74eb3557edd7a27c21b575ea9'


def request_json(url, payload=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'TFT-Rank-Monitor/1.0',
                                          'apollographql-client-name': 'Esports Web',
                                          'apollographql-client-version': '1.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        # Do not log the URL: a Discord URL contains a secret token.
        raise RuntimeError(f'HTTP {e.code}; request failed') from None
    except urllib.error.URLError:
        raise RuntimeError('Network request failed; check your connection') from None


def read_player(data):
    if data.get('errors'):
        raise RuntimeError('Ladder API error: ' + '; '.join(e.get('message', 'Unknown error') for e in data['errors']))
    rows = data['data']['tftLadderByShardAndTournament']['entries']
    matches = [r for r in rows if f"{r['displayName']}#{r['tagLine']}".casefold() == PLAYER.casefold()]
    if len(matches) != 1:
        raise RuntimeError(f'Expected one {PLAYER} row, found {len(matches)}. Previous rank preserved.')
    row = matches[0]
    rank = row.get('position')
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        raise RuntimeError('Invalid ladder position; previous rank preserved')
    return row


def fetch_player():
    params = {'operationName': OPERATION,
              'variables': json.dumps({'esportsTournamentId': TOURNAMENT, 'shard': 'EUW1', 'hl': 'en-GB'}),
              'extensions': json.dumps({'persistedQuery': {'version': 1, 'sha256Hash': HASH}})}
    return read_player(request_json('https://competetft.com/api/gql?' + urllib.parse.urlencode(params)))


def notify(webhook, message):
    # wait=true requires Discord to acknowledge that it created the message.
    url = webhook + ('&' if '?' in webhook else '?') + 'wait=true'
    request_json(url, {'content': message, 'allowed_mentions': {'parse': []}})


def save_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def load_config():
    if CONFIG.exists():
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
    else:
        config = {}
    webhook = os.environ.get('DISCORD_WEBHOOK_URL') or config.get('webhook_url')
    if not webhook:
        print('Paste your Discord webhook URL below (input is hidden).')
        webhook = getpass.getpass('Webhook URL: ').strip()
    if not re.fullmatch(r'https://(?:discord\.com|discordapp\.com)/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_-]+', webhook):
        raise RuntimeError('Invalid Discord webhook URL')
    interval = int(config.get('check_seconds', 120))
    if interval < 60:
        raise RuntimeError('check_seconds must be at least 60')
    if not os.environ.get('DISCORD_WEBHOOK_URL'):
        save_json(CONFIG, {'webhook_url': webhook, 'check_seconds': interval})
    return webhook, interval


def check_once(webhook):
    row = fetch_player()
    previous = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else None
    if previous and previous.get('player') != PLAYER:
        raise RuntimeError('Saved state belongs to another player')
    rank = row['position']
    if previous is None:
        notify(webhook, f'✅ TFT monitor started for **{PLAYER}**.\nCurrent position: **#{rank}** | LP: **{row.get("lp", "?")}**\nAlerts are sent when your position gets worse.\n<{PAGE}>')
    elif rank > previous['rank']:
        notify(webhook, f'🔻 **Leaderboard drop**\n{PLAYER}: **#{previous["rank"]} → #{rank}**\nLost {rank - previous["rank"]} position(s). LP: **{row.get("lp", "?")}**\n<{PAGE}>')
    # Update only after successful delivery. A failed alert is retried next check.
    save_json(STATE, {'player': PLAYER, 'rank': rank, 'lp': row.get('lp'),
                      'checked_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    print(f'{dt.datetime.now():%H:%M:%S}  {PLAYER}: #{rank}, LP {row.get("lp", "?")}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Read current rank without webhook or state changes')
    parser.add_argument('--test', action='store_true', help='Send one Discord test message')
    args = parser.parse_args()
    if args.check:
        print(json.dumps(fetch_player(), indent=2))
        return
    webhook, interval = load_config()
    if args.test:
        notify(webhook, f'✅ Discord test successful. Ready to monitor **{PLAYER}**.')
        print('Test message sent.')
        return
    print(f'Monitoring {PLAYER} every {interval}s. Keep this window open. Ctrl+C stops it.', flush=True)
    while True:
        try:
            check_once(webhook)
        except Exception as e:
            print(f'Check failed: {type(e).__name__}: {e}. Retrying in {interval}s.', flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nMonitor stopped.')
    except Exception as e:
        print(f'{type(e).__name__}: {e}')
        raise SystemExit(1)
