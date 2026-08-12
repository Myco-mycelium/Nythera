# How to Run the Linux Backend Tests

*Applies to: verifying the current implementation works, without taking
its own status document on faith.*

## When you need this

You're about to rely on the Linux Backend (`source/nyhal-linux-backend/`)
— reading its code, extending it, or citing its conformance state in a
document. The repository's own discipline is "independently verified, not
assumed" (see `REPOSITORY_STATE.md`); this guide is how you do that.

## Steps

### 1. Install the dependencies

```bash
cd source/nyhal-linux-backend
python3 -m pip install -r requirements.txt
```

Requires Python 3.12+. If `pip` refuses due to a system-managed
environment, use a virtual environment (or your distribution's supported
equivalent) rather than overriding it.

### 2. Run the suite

```bash
python3 -m pytest test_backend.py
```

Expect **20/20 passing** as of the current status documents. If you see
failures, that's a real signal — the status documents in this repository
(`IMPLEMENTATION_STATUS.md`, `REPOSITORY_STATE.md`) are only as accurate
as the last person who ran this command.

### 3. Read what the tests actually cover

Passing tests are not the same as conformance. The backend's own status
document (`IMPLEMENTATION_STATUS.md`) self-rates as **not yet conformant**
to NPS-017 §5 — capability enforcement is tracked state without
seccomp/LSM wiring, and the NyFS FUSE integration is structural only.
After the tests pass, spend two minutes confirming that self-assessment
against the code: grep for where capabilities are checked (the
control-plane checks) and notice there is no syscall filtering.

## Checklist

- [ ] `pip install -r requirements.txt` succeeds
- [ ] `pytest test_backend.py` passes 20/20
- [ ] You understand what the tests cover *and* what they don't (per
      NPS-017 §5.1, partial conformance must be documented, not assumed)
