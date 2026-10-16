# Contributing to PuffPAI

Thank you for your interest in contributing to PuffPAI! This document provides guidelines for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Pull Request Process](#pull-request-process)
- [Community](#community)

---

## Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inspiring community for all. Please be respectful and constructive.

### Our Standards

**Positive behavior:**
- Using welcoming and inclusive language
- Being respectful of differing viewpoints
- Gracefully accepting constructive criticism
- Focusing on what is best for the community

**Unacceptable behavior:**
- Harassment of any kind
- Trolling or insulting comments
- Publishing others' private information
- Other conduct which could reasonably be considered inappropriate

---

## How Can I Contribute?

### Reporting Bugs and Suggesting Features

Use the [issue tracker](https://github.com/CypherSisters/PuffPAIBuildBot/issues).
[docs/ISSUES.md](docs/ISSUES.md) explains which form to pick (bug report,
feature request, help me build a bot for..., wizard gap), what to include,
where the logs are on each computer, and how to take private things out first.
Never attach your vault, a key or a token. Check the [FAQ](docs/FAQ.md) and
existing issues first.

### Contributing Code

We welcome pull requests for:

- 🐛 Bug fixes
- ✨ New agent tools
- 📚 Documentation improvements
- 🌐 Translations
- ♿ Accessibility improvements
- ⚡ Performance optimizations

---

## Development Setup

### Prerequisites

- Python 3.11+
- Git
- Ollama with a local instruct model
- 16GB RAM minimum

### Setup Steps

1. **Fork the repository**
   ```bash
   # Click "Fork" on GitHub
   ```

2. **Clone your fork**
   ```bash
   git clone https://github.com/<your-github-username>/PuffPAIBuildBot.git
   cd PuffPAIBuildBot
   ```

3. **Add upstream remote**
   ```bash
   git remote add upstream https://github.com/CypherSisters/PuffPAIBuildBot.git
   ```

4. **Create virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   pip install -e ".[dev]"  # Install with dev dependencies
   ```

5. **Create a branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

### Running Tests

These are the exact commands CI runs (`.github/workflows/ci.yml`). On
Windows use `py -3` where the others use `python`; everything after that is
identical.

```bash
# Run all tests -- Linux / macOS
python -m pytest -q

# Run with coverage
python -m pytest --cov=src/puffpai

# Run specific test
python -m pytest tests/test_wizard_proposal.py
```

```powershell
# Run all tests -- Windows
py -3 -m pytest -q

# Run with coverage
py -3 -m pytest --cov=src/puffpai

# Run specific test
py -3 -m pytest tests/test_wizard_proposal.py
```

### Code Quality

```bash
# Format code
black src/puffpai

# Lint code
flake8 src/puffpai

# Type checking -- Linux / macOS (what CI runs)
MYPYPATH=src python -m mypy src
```

```powershell
# Type checking -- Windows
$env:MYPYPATH = "src"; py -3 -m mypy src
```

---

## Coding Standards

### Python Style

- Follow **PEP 8** style guide
- Use **Black** for formatting (line length: 100)
- Use **type hints** for function signatures
- Write **docstrings** for all public functions

**Example:**
```python
def calculate_water_needs(
    people: int,
    days: int,
    gallons_per_person_per_day: float = 1.0
) -> float:
    """
    Calculate total water needs for emergency preparedness.

    Args:
        people: Number of people
        days: Number of days
        gallons_per_person_per_day: Water consumption rate

    Returns:
        Total gallons needed

    Example:
        >>> calculate_water_needs(4, 14)
        56.0
    """
    return people * days * gallons_per_person_per_day
```

### Git Commit Messages

Follow conventional commits format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Code style (formatting, no logic change)
- `refactor`: Code restructuring
- `test`: Adding tests
- `chore`: Maintenance tasks

**Examples:**
```
feat(agents): add media playback tool for Steward

Implements a Steward tool to control media playback
from internet access while maintaining local network connectivity.

Closes #123
```

```
fix(rag): handle empty search results gracefully

Previously crashed when no results found. Now returns helpful
message suggesting alternative search terms.

Fixes #456
```

### Documentation

- Update README.md for major features
- Add docstrings to all functions
- Include examples in docstrings
- Update relevant docs/ files
- Add comments for complex logic

---

## Pull Request Process

### Before Submitting

- [ ] Code follows style guidelines
- [ ] Tests pass locally (`python -m pytest -q`, or `py -3 -m pytest -q` on Windows)
- [ ] Code is formatted (`black`, `flake8`)
- [ ] Documentation is updated
- [ ] Commit messages are clear
- [ ] Branch is up to date with main

### PR Description Template

```markdown
## Description
[Clear description of what this PR does]

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Performance improvement

## Testing
[How was this tested?]

## Screenshots (if applicable)
[Add screenshots]

## Checklist
- [ ] Code follows style guidelines
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No breaking changes
- [ ] Tested on at least one of Windows / Linux / macOS (say which)
```

### Review Process

1. **Automated checks** must pass (GitHub Actions)
2. **Code review** by maintainers
3. **Testing** on multiple platforms if needed
4. **Approval** from at least one maintainer
5. **Merge** by maintainers

### After PR is Merged

```bash
# Sync your fork
git checkout main
git pull upstream main
git push origin main

# Delete your feature branch
git branch -d feature/your-feature-name
git push origin --delete feature/your-feature-name
```

---

## Community

### Communication Channels

- **GitHub Issues**: Bug reports, feature requests
- **GitHub Discussions**: Questions, ideas, general discussion
- **Pull Requests**: Code contributions

### Recognition

Contributors will be:
- Listed in CONTRIBUTORS.md
- Mentioned in release notes
- Credited in project documentation

### Priority Areas

We especially need help with:

1. **Testing** on platforms and hardware we cannot cover ourselves (Windows,
   Linux, macOS; various GPUs and accelerators)
2. **Documentation** improvements and tutorials
3. **Translations** for international users
4. **Accessibility** features
5. **New agent tools** (media control, calendar, etc.)

---

## Questions?

- **General questions**: [GitHub Discussions](https://github.com/CypherSisters/PuffPAIBuildBot/discussions)
- **Bug reports**: [GitHub Issues](https://github.com/CypherSisters/PuffPAIBuildBot/issues)
- **Security issues**: See [SECURITY.md](SECURITY.md). Use GitHub private vulnerability reporting, not a public issue or email

---

Thank you for contributing to PuffPAI! 🎉
