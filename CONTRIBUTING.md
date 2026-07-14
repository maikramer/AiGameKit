# Contributing to GameDev

Thank you for your interest in contributing to GameDev! This guide will help you set up your development environment and make your first contribution.

## Table of Contents

- [Quick Start](#quick-start)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Code Style](#code-style)
- [Making Changes](#making-changes)
- [Submitting Changes](#submitting-changes)

## Quick Start

```bash
# Clone the repo
git clone https://github.com/maikramer/GameDev.git
cd GameDev

# Install a package in dev mode
cd Shared && pip install -e '.[dev]'

# Run tests
make test-shared
```

## Development Setup

### Prerequisites

- Python 3.10 or higher
- Git
- CUDA (optional, for GPU features)

### Setting Up a Package

Each package has its own virtual environment. For example, to work on Text2D:

```bash
cd Text2D

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install in development mode
pip install -e '.[dev]'
```

### Installing All Packages

```bash
# Install all packages with dev dependencies
for dir in Shared Text2D Text3D Paint3D GameAssets Texture2D Text2Sound Skymap2D Rigging3D; do
  if [ -d "$dir" ]; then
    echo "Installing $dir..."
    cd "$dir"
    pip install -e '.[dev]' 2>/dev/null || pip install -e .
    cd ..
  fi
done
```

## Running Tests

### Run All Tests

```bash
make test
```

### Run Tests for a Specific Package

```bash
make test-shared      # Shared library
make test-text2d     # Text2D
make test-text3d     # Text3D
make test-paint3d    # Paint3D
```

### Run with Coverage

```bash
cd Shared
pip install pytest-cov
pytest --cov=src --cov-report=html
```

### GPU Tests

Some tests require CUDA. They will be skipped automatically if CUDA is not available:

```bash
# These will skip if no GPU
pytest Shared/tests/test_gpu.py
pytest Text3D/tests/test_gpu_exclusive.py
```

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

### Running Linter

```bash
# Check for issues
ruff check .

# Auto-fix issues
ruff check . --fix
```

### Running Formatter

```bash
# Check formatting
ruff format --check .

# Auto-format
ruff format .
```

### Pre-commit Hooks

We recommend using pre-commit hooks to automatically run linting before each commit:

```bash
# Install pre-commit
pip install pre-commit

# Install hooks
pre-commit install

# Run manually
pre-commit run --all-files
```

### Style Rules

- **Line length:** 120 characters max
- **Quotes:** Double quotes (`"..."`)
- **Indentation:** 4 spaces (or run `ruff format .`)
- **Imports:** Sorted, with `isort` style
- **Type hints:** Recommended for new functions

### Docstrings

Use Google-style docstrings:

```python
def generate_image(prompt: str, seed: int | None = None) -> Image.Image:
    """Generate an image from a text prompt.

    Args:
        prompt: The text description of the image.
        seed: Random seed for reproducibility.

    Returns:
        Generated PIL Image.

    Raises:
        ValueError: If prompt is empty.
    """
    if not prompt:
        raise ValueError("Prompt cannot be empty")
    return _generate(prompt, seed)
```

## Making Changes

### 1. Create a Branch

```bash
git checkout main
git pull origin main
git checkout -b feature/my-new-feature
```

### 2. Make Your Changes

- Write your code
- Add tests
- Update documentation
- Follow the code style

### 3. Test Your Changes

```bash
# Run tests for your package
make test-shared

# Run linting
ruff check .
ruff format --check .
```

### 4. Commit Your Changes

```bash
git add .
git commit -m "feat: add new feature description"
```

We use conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style changes (formatting, no logic change)
- `refactor:` Code refactoring
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

## Submitting Changes

### Pull Request Process

1. **Fork the repository** on GitHub
2. **Clone your fork** locally
3. **Create a branch** for your changes
4. **Make your changes** and commit them
5. **Push to your fork** on GitHub
6. **Open a Pull Request** against the `main` branch

### Pull Request Template

```markdown
## Description
Brief description of the changes.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Code refactoring

## Testing
Describe how you tested your changes.

## Checklist
- [ ] My code follows the style guidelines
- [ ] I have performed a self-review of my code
- [ ] I have commented my code where necessary
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix/feature works
```

## Package Structure

```
GameDev/
├── Shared/           # Shared utilities (logging, GPU, HF helpers)
├── Text2D/           # Text to 2D sprite generation
├── Text3D/           # Text to 3D model generation
├── Paint3D/          # 3D texture painting
├── GameAssets/       # CLI for combining all tools
├── Texture2D/        # 2D texture generation
├── Text2Sound/      # Text to sound effects
├── Skymap2D/        # 2D skybox generation
├── Rigging3D/       # 3D rigging (Rust + Python)
└── Materialize/     # Rust crate for Materialize3D
```

## Common Issues

### CUDA Out of Memory

If you get CUDA out of memory errors:
1. Reduce batch size
2. Use `--low-vram` flag (where available)
3. Close other GPU applications

### Model Download Fails

Set your HuggingFace token:
```bash
export HF_TOKEN=your_token_here
```

### Import Errors

Make sure you're in the correct virtual environment:
```bash
source .venv/bin/activate
```

## Questions?

- Open an issue on GitHub
- Check the [troubleshooting guide](troubleshooting.md)

## License

By contributing, you agree that your contributions will be licensed under the project's license.
