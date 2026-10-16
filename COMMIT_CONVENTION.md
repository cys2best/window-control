# Commit Convention

This repository follows the [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/#summary) specification.

## Structure

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

## Types

- `feat`: (correlates with `MINOR` in SemVer) A new feature or capability
- `fix`: (correlates with `PATCH` in SemVer) A bug fix
- `docs`: Documentation only changes
- `style`: Changes that do not affect the meaning of the code (white-space, formatting, semi-colons, etc.)
- `refactor`: A code change that neither fixes a bug nor adds a feature
- `perf`: A code change that improves performance
- `test`: Adding missing tests or correcting existing tests
- `build`: Changes that affect the build system or external dependencies
- `ci`: Changes to CI configuration files and scripts
- `chore`: Other changes that don't modify src or test files

## Breaking Changes

Breaking changes (correlating with `MAJOR` in SemVer) MUST be signaled by:
- An exclamation mark (`!`) immediately preceding the colon (e.g., `feat!: drop support for python 3.9` or `fix(api)!: alter response shape`), or
- A `BREAKING CHANGE: <description>` entry in the footer.

## Rules & Best Practices

1. **Imperative Mood**: Use imperative present tense in description (e.g., "add feature", not "added feature" or "adds feature").
2. **Case**: Lowercase type and description.
3. **No Trailing Period**: Do not end the description line with a period.
4. **Line Length**: Keep the header line concise (under 72 characters).
5. **Scope**: Optional noun enclosed in parentheses describing the affected codebase section (e.g., `feat(config): ...`).
6. **Body & Footers**: Optional. When provided, separate the header, body, and footers with a single blank line.
7. **Clean Traceability**:
   - Do NOT include agent identities, plan names, or task numbers in commit messages (traceability belongs in workflow state and agent-mem sessions).
   - Do NOT add "Co-Authored-By", AI-attribution footers, or generator trailers to commits or PRs.
