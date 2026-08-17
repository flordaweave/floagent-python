# FloAgent Python SDK

[![CI](https://github.com/FlordaWeave/floagent-python/actions/workflows/ci.yml/badge.svg)](https://github.com/FlordaWeave/floagent-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/floagent-sdk.svg)](https://pypi.org/project/floagent-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/floagent-sdk.svg)](https://pypi.org/project/floagent-sdk/)

Python support for [FloAgent](https://github.com/FlordaWeave/floagent)
external apps. The core handoff client has no runtime dependencies, and the
optional Streamlit adapter handles authentication state and safe navigation.

## Installation

```bash
python -m pip install floagent-sdk
```

For Streamlit apps:

```bash
python -m pip install "floagent-sdk[streamlit]"
```

## Streamlit handoff

Construct navigation first so every redirect target is registered, then gate
page execution through `StreamlitHandoff`:

```python
import streamlit as st

from floagent_sdk import HandoffClient
from floagent_sdk.streamlit import StreamlitHandoff

home = st.Page("pages/home.py", title="Home", default=True)
admin = st.Page("pages/admin.py", title="Admin", url_path="admin")
navigation = st.navigation([home, admin])

client = HandoffClient.from_config(st.secrets["floagent"])
auth = StreamlitHandoff(
    client,
    redirect_pages={
        "/": home,
        "/admin": admin,
    },
)
session = auth.require_session()
auth.render_sidebar(session)
navigation.run()
```

Configure the app with a known API base URL:

```toml
[floagent]
api_base_url = "https://api.example.com"
allowed_api_base_urls = ["https://api-alt.example.com"]
request_timeout_seconds = 10
```

Incoming `api_base_url` values and session-discovered endpoints must match the
configured URL or an explicit allowlist entry. This protects server-side apps
from making authentication requests to attacker-controlled destinations.

Register the external app with FloAgent using the Streamlit app root as its
`handoff_url`. Redirect paths must be listed in `redirect_pages`. Redirect
query parameters such as `task_id` are restored after page switching.

Access and refresh tokens are kept in Streamlit Session State and are never
written to browser storage. When the browser session is lost, the user must
reopen or refresh the app through the FloAgent launcher to obtain a new
one-time handoff.

## Core client

Framework integrations can use `HandoffClient` directly:

```python
from floagent_sdk import HandoffClient

client = HandoffClient("https://api.example.com")
session = client.exchange(token, api_base_url=discovered_api_base_url)

if session.needs_refresh():
    session = client.refresh(session)
```

The client exchanges `POST /auth/login/handoff/exchange` tokens and refreshes
sessions through `POST /auth/token/refresh`. Tokens are never included in SDK
error messages.

## Development

```bash
python -m pip install -e ".[test,streamlit]"
python -m pytest
python -m pip wheel --no-deps --wheel-dir dist .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow and
[SECURITY.md](SECURITY.md) for private vulnerability reporting.
