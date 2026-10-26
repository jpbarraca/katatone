# Build the Flatpak of the desktop GUI (see docs/flatpak.md).
#
#   make flatpak-deps      install the GNOME runtime and SDK the manifest asks for (once)
#   make flatpak           build katatone-<version>-<arch>.flatpak and its .sha256
#   make flatpak-install   build and install it for this user
#   make flatpak-run       run the installed Flatpak
#
# Everything is built under build/flatpak (ignored by git).  The version comes from
# pyproject.toml and the runtime from the manifest, so nothing is written twice.  Set
# STATE to keep flatpak-builder's download cache somewhere else (it is what makes a
# second build quick):  make flatpak STATE=$$HOME/.cache/katatone-flatpak

APP_ID   := io.github.jpbarraca.katatone
DIR      := packaging/flatpak
MANIFEST := $(APP_ID).yml
RUNTIME  := $(shell sed -n "s/^runtime-version: '\(.*\)'/\1/p" $(DIR)/$(MANIFEST))
VERSION  := $(shell python3 packaging/check_version.py)
ARCH     := $(shell flatpak --default-arch 2>/dev/null || uname -m)

OUT      ?= $(CURDIR)/build/flatpak
STATE    ?= $(OUT)/state
REPO     := $(OUT)/repo
TREE     := $(OUT)/tree
BUNDLE   := $(OUT)/katatone-$(VERSION)-$(ARCH).flatpak

BUILDER  := flatpak-builder --user --force-clean --disable-rofiles-fuse \
            --state-dir="$(STATE)" --repo="$(REPO)"

.DEFAULT_GOAL := help
.PHONY: help check-version flatpak-deps flatpak flatpak-install flatpak-run clean-flatpak

help:
	@sed -n 's/^#   make \(.*\)/make \1/p' $(MAKEFILE_LIST)

check-version:
	@python3 packaging/check_version.py

flatpak-deps:
	flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
	flatpak install --user -y flathub org.gnome.Platform//$(RUNTIME) org.gnome.Sdk//$(RUNTIME)

# The manifest builds the sources next to it (path ../..), so run flatpak-builder from there.
$(BUNDLE): check-version
	mkdir -p "$(OUT)"
	cd $(DIR) && $(BUILDER) "$(TREE)" $(MANIFEST)
	flatpak build-bundle "$(REPO)" "$@" $(APP_ID) master
	cd "$(OUT)" && sha256sum "$(notdir $@)" > "$(notdir $@).sha256"
	@echo "Built $@"

flatpak: $(BUNDLE)

flatpak-install: check-version
	mkdir -p "$(OUT)"
	cd $(DIR) && $(BUILDER) --install "$(TREE)" $(MANIFEST)

flatpak-run:
	flatpak run $(APP_ID)

clean-flatpak:
	rm -rf "$(OUT)"
