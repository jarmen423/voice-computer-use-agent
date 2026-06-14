# Private / Personal Documentation

Files in this directory are tracked in the repo but are **excluded from the public
site build**. Use this folder for notes, drafts, or internal documentation that
should not appear on <https://computer-use.agentmemorylabs.com/docs/>.

The deploy workflow temporarily moves this directory aside before running
`mkdocs build`, then restores it, so private pages are never uploaded to
Cloudflare Pages.
