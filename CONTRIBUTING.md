# Contributing

## Development Setup

1. Clone the repo
2. Run `make build` to install dependencies
3. Run `make test` to verify everything works
4. Run `make lint` and `make typecheck` for code quality

## Running Tests

```bash
uv run pytest tests/ -v
```

## Code Quality

- Type hints required for all new functions
- Ruff linter must pass
- MyPy type checking must pass
- All new code must have tests

## Security

- Never commit secrets, API keys, or tokens
- Use environment variables for configuration
- All webhook endpoints must verify signatures
- Database queries must use parameterized statements

## Pull Requests

- Keep PRs focused on a single change
- Include tests for new functionality
- Update documentation as needed
- Ensure all checks pass before requesting review
