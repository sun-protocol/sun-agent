# SunAgent

* This is a Twitter digital person that can support the following features:
* Proactively post interpretations of the latest Web3 news flashes.
* Reply to user mentions.
* Engage with Key Opinion Leaders (KOLs) that users follow.

### Use the Starter twitter bot

```bash
git clone https://github.com/sun-protocol/SunAgent.git
cd SunAgent

uv sync --all-extras
source .venv/bin/activate

cd samples/twitter_robot
cp .env.twitter.example .env
python twitter_app.py
```
