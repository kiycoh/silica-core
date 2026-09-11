# silica-agent has been renamed to silica-core

This package exists only to redirect. Installing it installs
[`silica-core`](https://pypi.org/project/silica-core/), which is where
the project now lives, and the `silica` command keeps working.

Install the new name directly:

```
uv tool install 'silica-core[mcp,dense]'      # or: pipx install 'silica-core[mcp,dense]'
```

Source, issues and documentation: https://github.com/kiycoh/silica-core
