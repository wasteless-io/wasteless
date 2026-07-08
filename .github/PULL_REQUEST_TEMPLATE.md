<!-- One paragraph: what changes and why. Link the issue if there is one. -->

## Review checklist

<!-- Solo-maintainer note: this checklist is the second pair of eyes.
     Go through it after stepping away from the diff for a while,
     not right after writing it. -->

- [ ] New or changed behavior has a test that **fails without the change**
- [ ] `pytest` green locally; `cd ui && python run_tests.py` if `ui/` is touched
- [ ] `black`, `ruff`, and the scoped `mypy` command pass (see CONTRIBUTING)
- [ ] No AWS action path changed without its safeguard: the 7 checks in
      `src/core/safeguards.py` keep their order and none is bypassed
- [ ] Any new `recommendation_type` is declared in `ui/utils/action_registry.py`
      (the guard test fails otherwise) and wired into `wasteless.sh` or a job
- [ ] Money amounts carry pricing provenance (`stamp_pricing`) and use the
      right word: detected waste / potential / realized / verified savings
- [ ] Dry-run stays the default for any new or modified action
- [ ] No credentials, account IDs, or secrets in the diff
