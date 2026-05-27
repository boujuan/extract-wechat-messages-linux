# AUR packaging

This directory contains the PKGBUILD + .SRCINFO for publishing
`wxextract` to the Arch User Repository.

## SSH key setup (one-time, per AUR account)

The AUR uses SSH for git push. You need a dedicated keypair (so it
can be revoked independently of your other SSH keys):

```sh
# 1. Generate a new keypair (no passphrase prompt is fine; the file is
#    only used against aur.archlinux.org)
ssh-keygen -t ed25519 -f ~/.ssh/aur -N ''

# 2. Print the *public* key — copy this entire line to the clipboard
cat ~/.ssh/aur.pub

# 3. Paste it into  https://aur.archlinux.org/account/<your-username>/edit
#    in the "SSH Public Key" field. Save.

# 4. Tell your SSH client to use this key for the AUR host:
cat >> ~/.ssh/config <<'EOF'

Host aur.archlinux.org
  IdentityFile ~/.ssh/aur
  User aur
EOF
chmod 600 ~/.ssh/config

# 5. Verify the SSH login works
ssh aur@aur.archlinux.org help   # → should print AUR's help banner
```

## Local test build (verify before pushing)

```sh
cd packaging/aur
makepkg -si --noconfirm   # downloads source, verifies sha256, builds wheel,
                          # installs deps via pacman, creates .pkg.tar.zst,
                          # installs it system-wide
# AUR deps (python-rich-argparse, wechat-bin) need an AUR helper or
# manual install. With paru: `paru -S python-rich-argparse` first.
wxextract --version       # should print 0.7.1
```

## Publish to AUR (first time, package doesn't exist yet)

```sh
# 1. Clone the empty AUR repo (will warn "empty repository" — expected)
git -c init.defaultBranch=master clone ssh://aur@aur.archlinux.org/wxextract.git aur-wxextract

# 2. Copy the prepared PKGBUILD + .SRCINFO into it
cd aur-wxextract
cp ../packaging/aur/PKGBUILD ../packaging/aur/.SRCINFO .

# 3. Stage, commit, push
git add PKGBUILD .SRCINFO
git commit -m "Initial import: wxextract 0.7.1"
git push -u origin master
```

After the first push the package appears at
`https://aur.archlinux.org/packages/wxextract` and people can install
with their AUR helper: `paru -S wxextract` or `yay -S wxextract`.

## Bump for a new release

```sh
# In packaging/aur/PKGBUILD:
#   - edit pkgver=NEW
#   - reset pkgrel=1
#   - update the sha256 of the new tarball with:
updpkgsums                            # rewrites sha256sums=(...) in place
# OR manually:
curl -sL https://github.com/boujuan/extract-wechat-messages-linux/archive/refs/tags/vNEW.tar.gz \
    | sha256sum

# Regenerate .SRCINFO so the AUR shows the new version
makepkg --printsrcinfo > .SRCINFO

# Copy both files into the aur-wxextract clone, commit, push
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO ../aur-wxextract/
cd ../aur-wxextract
git add PKGBUILD .SRCINFO
git commit -m "Update to NEW"
git push
```

(Bump `pkgrel` instead of `pkgver` if you only changed the PKGBUILD
itself — e.g. a typo or a dependency fix — without a new upstream
release.)

## Notes

- `arch=('any')` — pure Python, no compiled extensions.
- `python-rich-argparse` and `wechat-bin` are AUR-only; AUR helpers
  resolve them recursively. If users are on bare `pacman`, they need
  to install them first.
- `sqlcipher` and `wechat-bin` are `optdepends` — the tool can render
  previously-decrypted data without WeChat installed, and has a
  pure-Python AES fallback when sqlcipher isn't present.
- `LICENSE` lands at `/usr/share/licenses/wxextract/LICENSE` per
  Arch convention.
- The hatchling build backend is in `makedepends` (not `depends`) —
  it's only needed at package build time.
