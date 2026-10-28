# Releasing

1. Make sure `main` is green in CI.
2. Update the version in `src/blastradius/__init__.py` and move the "unreleased" section
   of `CHANGELOG.md` under the new version with today's date.
3. Check the package builds and installs cleanly:

   ```bash
   python -m pip install build
   python -m build                                   # dist/*.whl and dist/*.tar.gz
   python -m venv /tmp/br-check && /tmp/br-check/bin/pip install dist/*.whl
   /tmp/br-check/bin/blastradius --version
   ```

4. Merge the release pull request, then tag the merge commit and push the tag:

   ```bash
   git switch main && git pull
   git tag -a v0.2.0 -m "BlastRadius 0.2.0"
   git push origin v0.2.0
   ```

5. On GitHub, create a release from the tag (Releases → Draft a new release), paste the
   changelog section, and attach the files from `dist/`.

Users of the GitHub Action pin `@v0.2.0` (or the tag's commit SHA), so a tag is what makes
a version available to them.
