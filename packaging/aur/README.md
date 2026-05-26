# AUR packaging

This directory contains the PKGBUILD + .SRCINFO for publishing
`wxextract` to the Arch User Repository.

## Local test build

```sh
cd packaging/aur
# fetch the source tarball + compute checksum
updpkgsums
# build the package
makepkg -si
# verify
wxextract --version
```

## Publish to AUR (first time)

```sh
# create the AUR repo (SSH key registered at aur.archlinux.org)
git clone ssh://aur@aur.archlinux.org/wxextract.git aur-wxextract
cd aur-wxextract
cp ../packaging/aur/PKGBUILD ../packaging/aur/.SRCINFO .
git add PKGBUILD .SRCINFO
git commit -m "Initial import: wxextract 0.1.0"
git push
```

## Bump for a new release

```sh
# in packaging/aur/PKGBUILD: edit pkgver=NEW, pkgrel=1
updpkgsums
makepkg --printsrcinfo > .SRCINFO
# copy to the aur-wxextract repo, commit, push
```

## Notes

- `arch=('any')` because the package is pure Python.
- `depends` includes `wechat-bin` so the AUR helper installs the WeChat
  client too. Drop it if you'd rather make that a soft dependency
  (e.g. `optdepends`).
- `sha256sums=('SKIP')` is a placeholder. Run `updpkgsums` before
  publishing each release to compute the real hash.
