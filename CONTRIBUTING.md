# Contributing to Aird

Primary package path: `aird/`.

## Setup

Python 3.10 or newer. A virtual environment keeps dependencies off the system interpreter.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[chat]"
python -m pip install -r requirements-test.txt
npm ci
```

Unix:

```bash
source .venv/bin/activate
python -m pip install -e ".[chat]"
python -m pip install -r requirements-test.txt
npm ci
```

`requirements-test.txt` is for local pytest. CI installs the hashed lockfile `requirements-ci.txt`.

## Tests and lint

```bash
python -m pytest
npm run lint:js
```

`pytest.ini` skips `@pytest.mark.slow` packaging tests. CI runs the full suite.

UI expectations, ESLint, and Sonar scope: [docs/developer-ui-quality.md](docs/developer-ui-quality.md).

## Coverage for Sonar

```bash
python -m pytest --cov=aird --cov-report=xml:coverage.xml
```

`sonar-project.properties` points Sonar at `coverage.xml` and `tests/`. See [docs/developer-ui-quality.md](docs/developer-ui-quality.md).

## Before a pull request

- `python -m pytest` passes
- `npm run lint:js` passes
- UI changes follow [docs/developer-ui-quality.md](docs/developer-ui-quality.md)
