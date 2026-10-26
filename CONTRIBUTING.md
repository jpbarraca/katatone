# Contributing

Thanks for your interest in KataTone.  Bug reports, patches and ideas are all
welcome.

## Reporting a bug

Open an [issue](https://github.com/jpbarraca/katatone/issues) with:

- what you did, what you expected, and what happened instead
- your Linux distribution, Python version and how you installed KataTone
  (pipx, Flatpak, from source)
- the amp model and firmware version — KataTone is developed and tested with a
  Katana 50 Gen 3 only, so reports from other models are especially useful
- the log, if there is one: run `katatone-gui -v` (or `katatone-web -v`) for
  debug logging

## Suggesting a feature

Open an issue describing the use case before starting on a large change, so we
can agree on the approach first.

## Making a change

1. Fork the repository and create a branch from `master`.
2. Set up a development environment and learn the project layout:
   see [Development](docs/development.md).
3. Make your change, and add or update tests in `tests/`.
4. Run the test suite — it needs no amp or MIDI hardware:

   ```bash
   python -m pytest -q
   ```

5. If the change touches how KataTone talks to the amp, try it on a real amp
   too (the `dev/` scripts help), and say in the pull request which amp you
   tested with.
6. Update the docs in `docs/` or the README if behaviour changes.
7. Open a pull request against `master` explaining what the change does and why.

## Style

- Match the surrounding code: naming, comments and structure.
- Keep pull requests focused: one change per pull request.
- Write commit messages as a short imperative sentence, e.g.
  "Check string intonation in the tuner".

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the same license as the project.
