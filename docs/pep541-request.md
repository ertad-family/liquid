# PEP 541 — Request to Transfer PyPI Package Name `liquid`

## How to Submit

File an issue at: https://github.com/pypi/support/issues/new?template=pep541-702.yml

## Request Details

**Package name:** `liquid`

**PyPI URL:** https://pypi.org/project/liquid/

**Your PyPI username:** *(your username)*

**Your package URL:** https://pypi.org/project/liquid-api/

**Your GitHub repo:** https://github.com/ertad-family/liquid

### Justification

The `liquid` package on PyPI meets PEP 541 criteria for an abandoned project:

1. **No meaningful releases**: Only version 0.0.1, published September 26, 2013 (over 12 years ago)
2. **No project description**: "The author of this package has not provided a project description"
3. **Planning status**: Classified as "Development Status :: 1 - Planning" — never progressed beyond planning
4. **No activity**: The associated GitHub repository (https://github.com/refnode/liquid) has had no commits or activity in years
5. **No dependencies or functionality**: The package contains no meaningful code

### Our Project

**liquid-api** (https://pypi.org/project/liquid-api/) is an actively developed Python library for programmatic API discovery and adapter generation. We are currently publishing under `liquid-api` but our import name is `liquid`, which creates a confusing mismatch:

```bash
pip install liquid-api    # install command
from liquid import Liquid  # import name
```

Transferring the `liquid` name would:
- Eliminate the install-vs-import name mismatch
- Give the name to an actively maintained project with 210 tests, full documentation, and regular releases
- Serve the Python community better than an abandoned placeholder

### Contact Attempts

*(Before submitting, attempt to contact the current owner `refnode` via GitHub or email. Document your attempt here, even if there's no response after 2-4 weeks.)*

**Steps to take:**
1. Open an issue at https://github.com/refnode/liquid asking to transfer the PyPI name
2. Wait 4 weeks for a response
3. If no response, submit the PEP 541 request with proof of the contact attempt
