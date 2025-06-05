# SunAgent

[![Checks](https://github.com/TronNova/SunAgent/actions/workflows/checks.yml/badge.svg)](https://github.com/TronNova/SunAgent/actions/workflows/checks.yml)


## Development


**Install dependency**
```
sudo apt-get install libavif16
```

**TL;DR**, run all checks with:

```sh
uv sync --all-extras
source .venv/bin/activate
```

**Build package**, run the following command
```
uv build --package sunagent_core --out-dir dist/
```

### Setup

`uv` is a package manager that assists in creating the necessary environment and installing packages to run SunAgent.

- [Install `uv`](https://docs.astral.sh/uv/getting-started/installation/).

### Virtual Environment

During development, you may need to test changes made to any of the packages.\
To do so, create a virtual environment where the SunAgent packages are installed based on the current state of the directory.\
Run the following commands at the root level of the Python directory:

```sh
uv sync --all-extras
source .venv/bin/activate
```

- `uv sync --all-extras` will create a `.venv` directory at the current level and install packages from the current directory along with any other dependencies. The `all-extras` flag adds optional dependencies.
- `source .venv/bin/activate` activates the virtual environment.

## use case

- twitter ai bot
- web3 token bot

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

### Use the Starter token bot

```bash
git clone https://github.com/sun-protocol/SunAgent.git
cd SunAgent

uv sync --all-extras
source .venv/bin/activate
cd samples/twitter_robot
cp .env.token.example .env
python token_launch_app.py
```
