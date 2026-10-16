"""One-process ASGI broker entrypoint; secrets are read only from a file."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from broker.app import BrokerSettings, create_broker_app


def settings_from_environment():
    try:
        provider = os.environ.get('REMOTE_RELAY_PROVIDER') or 'coturn'
        required = ('REMOTE_ALLOWED_ORIGINS', 'REMOTE_STORE_PATH', 'REMOTE_MAX_ACTIVE_STREAMS', 'REMOTE_STUN_URLS')
        required += {'coturn': ('REMOTE_TURN_SECRET_FILE', 'REMOTE_TURN_URLS'),
                     'cloudflare': ('REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE', 'REMOTE_CLOUDFLARE_TURN_TOKEN_FILE')}[provider]
        if any(not os.environ.get(key) for key in required):
            raise ValueError()
        origins = json.loads(os.environ['REMOTE_ALLOWED_ORIGINS'])
        if not isinstance(origins, list) or not origins:
            raise ValueError()
        for origin in origins:
            parsed = urlsplit(origin)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError()
        if provider == 'cloudflare':
            relay = dict(
                relay_provider='cloudflare',
                cloudflare_turn_key_id=Path(os.environ['REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE']).read_text().strip(),
                cloudflare_turn_api_token=Path(os.environ['REMOTE_CLOUDFLARE_TURN_TOKEN_FILE']).read_text().strip(),
            )
        else:
            relay = dict(
                turn_shared_secret=Path(os.environ['REMOTE_TURN_SECRET_FILE']).read_text().strip(),
                turn_urls=json.loads(os.environ['REMOTE_TURN_URLS']),
            )
        return BrokerSettings(
            storage_path=os.environ['REMOTE_STORE_PATH'],
            allowed_origins=origins,
            max_active_streams=int(os.environ['REMOTE_MAX_ACTIVE_STREAMS']),
            stun_urls=json.loads(os.environ['REMOTE_STUN_URLS']),
            **relay,
            relay_available=os.environ.get('REMOTE_RELAY_AVAILABLE', 'true') == 'true',
            license_path=os.environ.get('REMOTE_LICENSE_PATH') or None,
        )
    except (KeyError, ValueError, OSError):
        raise RuntimeError('Invalid or missing broker deployment settings') from None


app = create_broker_app(settings_from_environment())
