# Publish the source to GitHub

## Current status

The source package and local Git history were prepared in the authoring environment. **No remote GitHub repository was created or pushed in that session.** The available connected GitHub actions could read the account but exposed no repository-creation or content-write action; the environment had no authenticated GitHub CLI. This is a tooling limitation, not a claim that your GitHub account lacks permission.

The intended new repository is `xattribution/signage-portal`, **private** by default. Its existence is not assumed. The publishing helper refuses to silently reuse an existing repository or upload to a public one.

## Publish from a clean extraction

Install Git, Python 3.10+ and the [GitHub CLI](https://cli.github.com/). Extract the supplied source archive into a new folder **outside any existing Git checkout**. Do not run the initial-publish helper from a configured deployment or after editing the package.

Sign into the intended GitHub account on your own machine; do not paste a token into ChatGPT, source files or `.env`:

```sh
gh auth login --hostname github.com --git-protocol https --web
```

From the extracted project's root:

```sh
python scripts/publish-github.py --repo xattribution/signage-portal
```

The script validates the exact files and SHA-256 hashes listed in `MANIFEST.sha256`, checks the authenticated account, creates a new private repo, verifies privacy before uploading, makes an initial `main` commit and pushes it. It does not deploy the application. It uses GitHub CLI credentials per Git command rather than changing your global Git credential configuration.

The script supports Windows, macOS and Linux where Git, Python and `gh` are on PATH. It has been checked locally with filesystem/unit tests, **not with a real GitHub create/push**. Existing global signing or network policies can require local configuration. Its file allowlist and digest verification are not a comprehensive secret scanner or a cryptographic signature.

To verify the package without GitHub access or writes:

```sh
python scripts/publish-github.py --check
```

## Failure and retry

Creation failure stops the script; it never deletes repositories or force-pushes. A network/commit failure can leave an empty remote or local initial-publish checkout. After correcting the cause, `--resume` permits this helper's interrupted initial import:

```sh
python scripts/publish-github.py --repo xattribution/signage-portal --resume
```

Resume requires the intended private repo and either no remote refs or an exact already-published `main` commit. Unrelated branches/history are a hard stop. A repository-name collision is not an invitation to overwrite it; choose a different name explicitly after checking your account.

Normal development after the first publish uses ordinary Git commits and pull requests, not this package-import helper. Update `MANIFEST.sha256` when packaging a new source archive; do not bypass validation to publish an unknown working tree.

## Repository configuration

The package includes a CI workflow for clean Python 3.12 dependency installation, syntax and regression checks, SHA-pinned GitHub Actions, read-only workflow permissions, Dependabot configuration and issue templates. It contains no deployment credentials, auto-deployment, registry-publishing tokens or UniFi credentials. Enable your preferred branch review rules and security reporting settings once the repo exists; these settings have not been changed remotely here.

CI has not run on GitHub yet. A successful initial push does not mean the release pins/images or office deployment passed acceptance. The first workflow result must be reviewed.

GitHub hosts the code and documentation. Install the running service on the office host using [INSTALL.md](INSTALL.md). [GitHub Pages is static hosting](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages), not an execution environment for this portal's Python services and NAS mounts.

References: [gh repo create](https://cli.github.com/manual/gh_repo_create), [gh auth login](https://cli.github.com/manual/gh_auth_login).
