# Contributing to NODO AI

Thanks for looking. NODO is a local-first memory for meetings and documents, and it gets better
when people try it, break it and say what failed.

## Good places to start

- Issues labelled [good first issue](https://github.com/PierfrancescoLijoi/nodo-ai/labels/good%20first%20issue)
  are small and self-contained.
- Issues labelled [help wanted](https://github.com/PierfrancescoLijoi/nodo-ai/labels/help%20wanted)
  are bigger and open to anyone.
- The "What is not met or not done" list in the [README](README.md#current-status) is the honest
  backlog. Retrieval quality and speaker attribution are the hardest open problems.
- Questions and ideas belong in [Discussions](https://github.com/PierfrancescoLijoi/nodo-ai/discussions).

## Set up

You need Python 3.13, Node.js 22+ and `ffmpeg` on `PATH`. The full steps are in the
[Quick start](README.md#quick-start). You do not need the real models to work on most of the code:
the deterministic test engine runs the interface against fake models and sample data.

```powershell
# API tests
cd services/api
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,azure]"
.venv\Scripts\python -m pytest

# Web tests and checks
cd apps/web
npm ci
npm test
npm run lint
npx tsc --noEmit
npx prettier --check .
```

## Making a change

1. Open an issue first for anything bigger than a small fix, so we agree on the direction.
2. Branch from `main`, keep the change focused, and add or update a test that fails without it.
3. Run the checks above. CI runs the same ones plus Docker image scanning and Terraform validation.
4. Open a pull request and fill in the template. Say what you ran and what you did not.

Commit messages use a short type prefix: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.

## Rules the project holds to

- Answers must cite the exact sentences they used. No evidence, no answer.
- Inferred decisions stay proposals until a person approves them.
- Nothing leaves the user's machine in local mode. A change that adds network calls needs a clear
  reason and an opt-in.
- Do not weaken a failing quality gate to make a number pass. If a gate fails, the docs say so.
- Models are opt-in and verified against a SHA-256 hash before use.

## License

By contributing you agree that your work is released under the [MIT License](LICENSE).
