# Contributing

## Workflow

1. **Fork** the repository.
2. **Create the branch from `develop`**, one branch per feature or fix:
   ```
   git checkout develop
   git pull upstream develop
   git checkout -b feature/my-feature
   ```
3. Do the work and run the tests (see [Tests](#tests)).
4. **Open the pull request against `develop` of the main repository**, not against your fork.
5. Keep the PR focused. Describe what changed and why.

## Commits

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <short summary>
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

Write **descriptive** commits: say what changed and why. One logical change per commit, imperative mood, no vague messages like `update` or `fix stuff`.

```
feat(gui): add button diagram for AYC
fix(protocol): reject replies with a mismatched address
docs: document the SHIFT layer record layout
```

## Tests

```
python test_bwmc.py
```
