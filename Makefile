# Makefile for GPU-Manager CLI

PYTHON := python3
BIN_NAME := gpu-manager
INSTALL_DIR := /usr/local/bin
COMPLETION_DIR := /etc/bash_completion.d

.PHONY: all build install uninstall clean help

all: build

build:
	@echo "==> Building standalone $(BIN_NAME) binary..."
	@pip3 install pyinstaller argcomplete >/dev/null 2>&1 || true
	@$(PYTHON) -m PyInstaller --onefile --name $(BIN_NAME) \
		--add-data "gpu-manager/formatters:formatters" \
		--add-data "gpu-manager/modules:modules" \
		gpu-manager/main.py
	@echo "==> Build complete: dist/$(BIN_NAME)"

install: build
	@echo "==> Installing $(BIN_NAME) to $(INSTALL_DIR)..."
	@cp dist/$(BIN_NAME) $(INSTALL_DIR)/$(BIN_NAME)
	@chmod +x $(INSTALL_DIR)/$(BIN_NAME)
	@if [ -d "$(INSTALL_DIR)" ]; then cp dist/$(BIN_NAME) /usr/bin/$(BIN_NAME) 2>/dev/null || true; fi
	@echo "==> Installing Bash autocompletion..."
	@if [ -d "$(COMPLETION_DIR)" ]; then \
		$(INSTALL_DIR)/$(BIN_NAME) completion bash > $(COMPLETION_DIR)/$(BIN_NAME) 2>/dev/null || true; \
	fi
	@echo "==> Installation finished! You can now run '$(BIN_NAME)' anywhere."

uninstall:
	@echo "==> Uninstalling $(BIN_NAME)..."
	@rm -f $(INSTALL_DIR)/$(BIN_NAME) /usr/bin/$(BIN_NAME) $(COMPLETION_DIR)/$(BIN_NAME)
	@echo "==> Uninstalled."

clean:
	@echo "==> Cleaning build artifacts..."
	@rm -rf build dist *.spec

help:
	@echo "Usage:"
	@echo "  make build      Build single-file $(BIN_NAME) binary into dist/"
	@echo "  make install    Install $(BIN_NAME) binary to $(INSTALL_DIR) and system PATH"
	@echo "  make uninstall  Remove $(BIN_NAME) binary from system"
	@echo "  make clean      Remove build temporary files"
