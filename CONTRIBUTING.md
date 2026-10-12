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
python -m pip install "nox>=2024.4.15"
nox -s js quality tests
```

`pytest.ini` skips `@pytest.mark.slow` packaging tests. CI sets `AIRD_CI=1` and runs the full suite through the same Nox sessions.

## Branches

| Pull request | Push publishes |
|--------------|----------------|
| feature → `dev` | `dev` → `0.x.Y.devN` |
| `dev` → `pre-release` | `pre-release` → `0.x.YrcN` |
| `pre-release` → `main` | `main` → `0.x.Y` |

Required checks on those pull requests are `frontend`, `tests`, and `promote`. Dependabot targets `dev` and is auto-merged after those checks pass. Repo settings that must be on: Allow auto-merge, and Allow GitHub Actions to create and approve pull requests. Branch protection on `dev` should require the three checks above.

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
