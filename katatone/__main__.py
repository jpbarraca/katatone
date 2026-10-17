"""Allow `python -m katatone` to invoke the CLI."""
from .cli import main

raise SystemExit(main())
