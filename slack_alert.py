import json, os, urllib.request
from dotenv import load_dotenv
load_dotenv()

def send_slack(text):
    url = os.getenv('SLACK_WEBHOOK_URL', '').strip()
    if not url:
        print('Slack: SLACK_WEBHOOK_URL not found in .env, skipping alert.')
        return False
    try:
        data = json.dumps({'text': text}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as error:
        print('Slack: could not send alert (' + type(error).__name__ + ').')
        return False

if __name__ == '__main__':
    ok = send_slack('Test message from The Retention Squad. If you see this, Slack is connected.')
    print('Sent!' if ok else 'Failed. Check the line in .env.')
