# =============================================================================
#  STM32 CMake Project Makefile — portable wrapper
#  Copy this file and .clangd to any STM32CubeMX CMake project root.
#  Only the block marked "CONFIGURE" may need manual adjustment.
# =============================================================================

-include tools/toolchain/env.mk
-include tools/jlink/env.mk
-include tools/openocd/env.mk

# -----------------------------------------------------------------------------
#  HOST PLATFORM
# -----------------------------------------------------------------------------
PYTHON ?= python

ifeq ($(OS),Windows_NT)
NULL_DEV := nul
DEFAULT_SERIAL_PORT := COM3
DEFAULT_JLINK := JLink
else
NULL_DEV := /dev/null
DEFAULT_SERIAL_PORT := /dev/ttyACM0
DEFAULT_JLINK := JLinkExe
endif

# -----------------------------------------------------------------------------
#  CONFIGURE – the only section you should ever touch
# -----------------------------------------------------------------------------
PRESET      ?= Debug
PREFIX      ?= arm-none-eabi-
FLASH_TOOL  ?= pyocd
SERIAL_PORT ?= $(DEFAULT_SERIAL_PORT)
SERIAL_BAUD ?= 115200
PROXY       ?=
TOOLCHAIN_MIRROR ?=

ifneq ($(strip $(PROXY)),)
export HTTP_PROXY := $(PROXY)
export HTTPS_PROXY := $(PROXY)
endif

# Override auto-detected values here when detection fails:
OPENOCD_CFG  ?= -f interface/cmsis-dap.cfg -f target/stm32f3x.cfg
PYOCD_TARGET ?= stm32f334r8tx

TOOL_PROXY_ARGS      := $(if $(strip $(PROXY)),--proxy $(PROXY),)
TOOLCHAIN_MIRROR_ARG := $(if $(strip $(TOOLCHAIN_MIRROR)),--mirror $(TOOLCHAIN_MIRROR),)

# -----------------------------------------------------------------------------
#  AUTO-DETECTED — derived from CMakeLists.txt / linker script / build output
# -----------------------------------------------------------------------------

# Project name: read from project(...) in CMakeLists.txt
# chr(40)='('  chr(41)=')' – avoids raw parens that confuse make's paren counter
PROJECT := $(or $(shell $(PYTHON) -c "import re;t=open('CMakeLists.txt').read();m=re.search('CMAKE_PROJECT_NAME\\s+(\\w+)',t,re.I);print(m.group(1) if m else '')" 2>$(NULL_DEV)),$(notdir $(CURDIR)))

# Linker script: first *.ld file in project root
LDSCRIPT := $(firstword $(wildcard *.ld))
_LDNAME  := $(basename $(notdir $(LDSCRIPT)))

# MCU family string extracted from linker script name
#   STM32F334XX_FLASH.ld -> F3  ->  stm32f3x.cfg  /  stm32f334xx
_MCU_FAMILY := $(shell $(PYTHON) -c \
  "import re; m=re.match(r'STM32([A-Z]\d)','$(_LDNAME)',re.I); print(m.group(1).lower() if m else 'f3')" \
  2>$(NULL_DEV))
_MCU_NAME := $(shell $(PYTHON) -c \
  "import re; m=re.match(r'(STM32\w+?)_','$(_LDNAME)',re.I); print(m.group(1).upper() if m else '$(_LDNAME)')" \
  2>$(NULL_DEV))

# Flash / RAM sizes parsed from linker script MEMORY block via helper script.
# Using a script avoids shell-escaping and 2>nul vs 2>/dev/null portability
# issues that caused the one-liner approach to silently fall back on Windows.
_MEM_PY    := $(shell $(PYTHON) tools/scripts/parse_ldscript.py 2>$(NULL_DEV))
FLASH_SIZE := $(or $(filter-out 0,$(word 1,$(_MEM_PY))),65536)
RAM_SIZE   := $(or $(filter-out 0,$(word 2,$(_MEM_PY))),12288)

# OpenOCD config: auto-derived from MCU family; override in CONFIGURE if wrong
OPENOCD_CFG ?= -f interface/stlink.cfg -f target/stm32$(_MCU_FAMILY)x.cfg

# J-Link device name: read Mcu.CPN from the CubeMX .ioc file, strip package+temp
# suffix (e.g. STM32F334R8T6 -> STM32F334R8).  Falls back to _MCU_NAME.
_IOC_FILE := $(firstword $(wildcard *.ioc))
_JLINK_DEVICE_AUTO := $(or $(if $(_IOC_FILE),$(shell $(PYTHON) -c \
  "import re; t=open('$(_IOC_FILE)').read(); m=re.search('Mcu.CPN=(\w+)',t); print(re.sub(r'[A-Z]\d+$$','',m.group(1)) if m else '')" \
  2>$(NULL_DEV))),$(_MCU_NAME))
JLINK_DEVICE ?= $(_JLINK_DEVICE_AUTO)

# pyOCD target: auto-detected from compile_commands.json, falls back to LD name
_CCDB        := $(wildcard $(BUILD_ROOT)/$(PRESET)/compile_commands.json)
PYOCD_TARGET ?= $(or \
  $(if $(_CCDB),$(shell $(PYTHON) -c \
    "import re,json; cmd=json.load(open('$(_CCDB)'))[0]['command']; m=re.search(r'-DSTM32(\w+)',cmd); print(('stm32'+m.group(1)).lower() if m else '')" \
    2>$(NULL_DEV))),\
  $(shell $(PYTHON) -c \
    "import re; m=re.match(r'(STM32\w+?)_','$(_LDNAME)',re.I); print(m.group(1).lower() if m else 'stm32')" \
    2>$(NULL_DEV)))

# --- Toolchain binaries -------------------------------------------------------
CC      := $(PREFIX)gcc
OBJCOPY ?= $(PREFIX)objcopy
OBJDUMP ?= $(PREFIX)objdump
SIZE    ?= $(PREFIX)size
NM      ?= $(PREFIX)nm
GDB     ?= $(PREFIX)gdb

# --- Toolchain versions (resolved once at parse time) ------------------------
CC_VER    := $(shell $(CC) --version 2>$(NULL_DEV) | $(PYTHON) -c "import sys; print(sys.stdin.readline().strip())" 2>$(NULL_DEV))
CMAKE_VER := $(shell cmake --version 2>$(NULL_DEV) | $(PYTHON) -c "import sys; print(sys.stdin.readline().strip())" 2>$(NULL_DEV))
NINJA_VER := $(shell ninja --version 2>$(NULL_DEV))
CC_PATH   := $(shell $(PYTHON) -c "import shutil,sys; print(shutil.which(sys.argv[1]) or sys.argv[1])" "$(CC)" 2>$(NULL_DEV))
GDB_PATH  := $(shell $(PYTHON) -c "import shutil,sys; print(shutil.which(sys.argv[1]) or sys.argv[1])" "$(GDB)" 2>$(NULL_DEV))

# --- ANSI colors --------------------------------------------------------------
ESC    := $(shell $(PYTHON) -c "print(chr(27),end='')" 2>$(NULL_DEV))
BOLD   := $(ESC)[1m
DIM    := $(ESC)[2m
GREEN  := $(ESC)[1;32m
CYAN   := $(ESC)[1;36m
YELLOW := $(ESC)[1;33m
RED    := $(ESC)[1;31m
RESET  := $(ESC)[0m

# --- Flash tools --------------------------------------------------------------
OPENOCD   ?= openocd
CUBEPROG  ?= STM32_Programmer_CLI
CUBEPROG_CONN ?= -c port=SWD freq=4000 reset=HWrst
PYOCD     ?= $(PYTHON) -m pyocd
JLINK         ?= $(DEFAULT_JLINK)
JLINK_IF      ?= SWD
JLINK_SPEED   ?= 4000

# --- Paths --------------------------------------------------------------------
BUILD_ROOT ?= build
BUILD_DIR  := $(BUILD_ROOT)/$(PRESET)
ELF := $(BUILD_DIR)/$(PROJECT).elf
HEX := $(BUILD_DIR)/$(PROJECT).hex
BIN := $(BUILD_DIR)/$(PROJECT).bin
LSS := $(BUILD_DIR)/$(PROJECT).lss
HAS_LDSCRIPT := $(strip $(wildcard *.ld))
HAS_CMAKE_PRESETS := $(strip $(wildcard CMakePresets.json))
SETUP_JOBS ?= 4
JLINK_SETUP_VERSION ?= V8.40
UNINSTALL_PYTHON_TOOLS ?=
UNINSTALL_DRY_RUN ?=
SETUP_BOOTSTRAP_TARGETS := setup-toolchain setup-openocd setup-python-tools setup-jlink $(if $(HAS_LDSCRIPT),gen-openocd-cfg setup-pyocd,)
SETUP_FINALIZE_TARGETS := $(if $(HAS_CMAKE_PRESETS),setup-clangd,)
UNINSTALL_ARGS := $(if $(filter 1 yes true TRUE YES,$(UNINSTALL_PYTHON_TOOLS)),--python-tools,) $(if $(filter 1 yes true TRUE YES,$(UNINSTALL_DRY_RUN)),--dry-run,)

# =============================================================================
.DEFAULT_GOAL := all

.PHONY: all build artifacts elf hex bin configure \
        size nm lss disasm symbols summary \
        clean distclean rebuild uninstall \
        flash flash-openocd flash-cubeprog flash-pyocd flash-jlink \
        erase reset gdbserver gdb debug serial \
        toolchain check-tools gen-openocd-cfg \
        setup setup-bootstrap setup-finalize \
        setup-toolchain setup-openocd setup-python-tools setup-jlink setup-pyocd setup-clangd \
        debug-preset release relwithdebinfo minsizerel \
        list-presets new-project help

# =============================================================================
#  Build
# =============================================================================

all: artifacts summary

build: all

artifacts: $(ELF) $(HEX) $(BIN)

elf: $(ELF)
hex: $(HEX)
bin: $(BIN)

configure:
	@$(PYTHON) -c "from pathlib import Path; p=Path('CMakeLists.txt'); t=p.read_text() if p.exists() else ''; p.open('a').write('\ninclude(user/user_sources.cmake OPTIONAL)\n') if t and 'user/user_sources.cmake' not in t else None"
	$(info $(CYAN)[CMake]$(RESET)  Configure preset: $(BOLD)$(PRESET)$(RESET))
	cmake --preset $(PRESET)

# After a successful link, copy compile_commands.json to the project root
# so clangd finds it automatically (CompilationDatabase: . in .clangd).
$(ELF): configure
	$(info $(CYAN)[Build]$(RESET)  Compiling preset: $(BOLD)$(PRESET)$(RESET))
	cmake --build --preset $(PRESET) --parallel
	cmake -E copy $(BUILD_DIR)/compile_commands.json compile_commands.json
	$(info $(GREEN)[Done]$(RESET)   ELF -> $(BOLD)$(ELF)$(RESET))

$(HEX): $(ELF)
	$(info $(CYAN)[HEX]$(RESET)    Generating $(BOLD)$@$(RESET))
	$(OBJCOPY) -O ihex $< $@

$(BIN): $(ELF)
	$(info $(CYAN)[BIN]$(RESET)    Generating $(BOLD)$@$(RESET))
	$(OBJCOPY) -O binary $< $@

# =============================================================================
#  Analysis
# =============================================================================

size: $(ELF)
	$(info $(CYAN)[Size]$(RESET)   Firmware memory usage:)
	$(SIZE) --format=berkeley $(ELF)

# =============================================================================
#  Build summary
# =============================================================================

summary: $(ELF) $(HEX) $(BIN)
	@$(PYTHON) tools/scripts/build_summary.py \
		--project "$(PROJECT)" --mcu "$(or $(_MCU_NAME),$(PROJECT))" \
		--preset "$(PRESET)" --elf "$(ELF)" --hex "$(HEX)" --bin "$(BIN)" \
		--size-tool "$(SIZE)" --flash-size "$(FLASH_SIZE)" --ram-size "$(RAM_SIZE)" \
		--gcc-ver "$(CC_VER)" --cmake-ver "$(CMAKE_VER)" --ninja-ver "$(NINJA_VER)" \
		--prefix "$(PREFIX)"

nm: $(ELF)
	$(info $(CYAN)[NM]$(RESET)     Top 40 symbols by size:)
	$(NM) --print-size --size-sort --reverse-sort $(ELF) | head -40

symbols: $(ELF)
	$(NM) --print-size --size-sort --reverse-sort $(ELF) > $(BUILD_DIR)/$(PROJECT).sym
	$(info $(GREEN)[Done]$(RESET)   Symbol table: $(BOLD)$(BUILD_DIR)/$(PROJECT).sym$(RESET))

lss: $(ELF)
	$(info $(CYAN)[LSS]$(RESET)    Generating disassembly listing...)
	$(OBJDUMP) -S -d $(ELF) > $(LSS)
	$(info $(GREEN)[Done]$(RESET)   Listing: $(BOLD)$(LSS)$(RESET))

disasm: $(ELF)
	$(OBJDUMP) -S -d $(ELF)

# =============================================================================
#  Clean
# =============================================================================

clean:
	$(info $(YELLOW)[Clean]$(RESET)   Removing $(BOLD)$(BUILD_DIR)$(RESET))
	cmake -E remove_directory $(BUILD_DIR)

distclean:
	$(info $(YELLOW)[Clean]$(RESET)   Removing $(BOLD)$(BUILD_ROOT)/$(RESET) and compile_commands.json)
	cmake -E remove_directory $(BUILD_ROOT)
	cmake -E rm -f compile_commands.json

rebuild: clean all

uninstall:
	$(info $(YELLOW)[Uninstall]$(RESET) Removing downloaded tools and generated setup artifacts)
	$(PYTHON) tools/scripts/uninstall.py $(UNINSTALL_ARGS)

# =============================================================================
#  One-time setup
#  Run once after cloning / creating the project.  Safe to re-run — each step
#  skips work that has already been done.
# =============================================================================

setup:
	@echo $(CYAN)[Setup]$(RESET) Phase 1/2: bootstrap tools with $(BOLD)$(SETUP_JOBS)$(RESET) parallel jobs
	@$(MAKE) --no-print-directory -j $(SETUP_JOBS) setup-bootstrap
	@$(if $(strip $(SETUP_FINALIZE_TARGETS)),echo $(CYAN)[Setup]$(RESET) Phase 2/2: finalize project-specific setup,echo $(CYAN)[Setup]$(RESET) Phase 2/2: no project-specific finalize step)
	@$(if $(strip $(SETUP_FINALIZE_TARGETS)),$(MAKE) --no-print-directory -j $(SETUP_JOBS) setup-finalize,:)
	@$(PYTHON) -c "print()"
	@$(if $(HAS_LDSCRIPT),:,echo $(YELLOW)[Setup]$(RESET) Skipping project-specific debug setup: no *.ld file found.)
	@$(if $(HAS_CMAKE_PRESETS),:,echo $(YELLOW)[Setup]$(RESET) Skipping compile_commands generation: no CMakePresets.json found.)
	@echo $(GREEN)[Setup complete!]$(RESET)
	@echo Press $(BOLD)Ctrl+Shift+B$(RESET) to build, $(BOLD)F5$(RESET) to debug.
	@$(PYTHON) -c "print()"
	@$(MAKE) --no-print-directory toolchain
	@$(PYTHON) -c "print()"

setup-bootstrap: $(SETUP_BOOTSTRAP_TARGETS)

setup-finalize: $(SETUP_FINALIZE_TARGETS)

setup-toolchain:
	$(info $(CYAN)[Setup 1/6]$(RESET) ARM GNU Toolchain)
	$(PYTHON) tools/scripts/get_toolchain.py --latest --prefer-system $(TOOLCHAIN_MIRROR_ARG) $(TOOL_PROXY_ARGS)

setup-openocd:
	$(info $(CYAN)[Setup 2/6]$(RESET) xPack OpenOCD)
	$(PYTHON) tools/scripts/get_openocd.py --latest $(TOOL_PROXY_ARGS)

setup-python-tools:
	$(info $(CYAN)[Setup 3/6]$(RESET) Python tools (pyOCD + pyserial))
	$(PYTHON) tools/scripts/setup_python_tools.py

setup-jlink:
	$(info $(CYAN)[Setup 4/6]$(RESET) J-Link Software Pack)
	$(PYTHON) tools/scripts/get_jlink.py --version $(JLINK_SETUP_VERSION) $(TOOL_PROXY_ARGS)

setup-pyocd: setup-python-tools
	$(info $(CYAN)[Setup 5/6]$(RESET) pyOCD target pack ($(PYOCD_TARGET)))
	$(PYTHON) tools/scripts/setup_pyocd.py $(PYOCD_TARGET)

# Generate .openocd/target.cfg + download SVD + update launch.json svdFile.
# Also called by the "build + gen openocd cfg (Debug)" VS Code task.
setup-clangd:
	$(info $(CYAN)[Setup 6/6]$(RESET) CMake configure (generates compile_commands.json))
	cmake --preset $(PRESET)
	-cmake -E copy $(BUILD_DIR)/compile_commands.json compile_commands.json

# =============================================================================
#  New project
#  Creates a sibling directory that shares this template's downloaded tools.
#  Usage: make new-project NAME=my_blinky
# =============================================================================

new-project:
	$(info $(CYAN)[New project]$(RESET) Creating $(BOLD)$(NAME)$(RESET) next to this template...)
	$(PYTHON) tools/scripts/new_project.py $(NAME)

# =============================================================================
#  OpenOCD config generation
# =============================================================================

gen-openocd-cfg:
	$(info $(CYAN)[OpenOCD]$(RESET) Generating .openocd/target.cfg)
	$(PYTHON) tools/scripts/gen_openocd_cfg.py

# =============================================================================
#  Flash
# =============================================================================

flash: flash-$(FLASH_TOOL)

flash-openocd: $(HEX)
	$(info $(CYAN)[Flash]$(RESET)   OpenOCD -> $(BOLD)$(HEX)$(RESET))
	$(OPENOCD) $(OPENOCD_CFG) -c "program $(HEX) verify reset exit"

flash-cubeprog: $(HEX)
	$(info $(CYAN)[Flash]$(RESET)   STM32CubeProgrammer -> $(BOLD)$(HEX)$(RESET))
	$(CUBEPROG) $(CUBEPROG_CONN) -d $(HEX) -v -g

flash-pyocd: $(BIN)
	$(info $(CYAN)[Flash]$(RESET)   pyOCD ($(PYOCD_TARGET)) -> $(BOLD)$(BIN)$(RESET))
	$(PYOCD) flash -t $(PYOCD_TARGET) $(BIN)

define JLINK_SCRIPT
h
loadfile $(HEX)
r
q
endef

flash-jlink: $(HEX)
	$(info $(CYAN)[Flash]$(RESET)   J-Link ($(JLINK_DEVICE)) -> $(BOLD)$(HEX)$(RESET))
	$(file >$(BUILD_DIR)/_flash.jlink,$(JLINK_SCRIPT))
	$(JLINK) -device $(JLINK_DEVICE) -if $(JLINK_IF) -speed $(JLINK_SPEED) -autoconnect 1 -ExitOnError 1 -CommandFile $(BUILD_DIR)/_flash.jlink

erase:
	$(info $(YELLOW)[Erase]$(RESET)   Mass erase via OpenOCD...)
	$(OPENOCD) $(OPENOCD_CFG) -c "init; reset halt; flash erase_sector 0 0 last; exit"

reset:
	$(info $(CYAN)[Reset]$(RESET)   Resetting MCU via OpenOCD)
	$(OPENOCD) $(OPENOCD_CFG) -c "init; reset run; exit"

# =============================================================================
#  Debug
# =============================================================================

gdbserver:
	$(info $(CYAN)[GDB Server]$(RESET) Starting OpenOCD on port $(BOLD)3333$(RESET)...)
	$(OPENOCD) $(OPENOCD_CFG) -c "init; reset halt"

gdb: $(ELF)
	$(info $(CYAN)[GDB]$(RESET)    Connecting to $(BOLD)localhost:3333$(RESET))
	$(GDB) $(ELF) \
		-ex "target extended-remote localhost:3333" \
		-ex "monitor reset halt" \
		-ex "load" \
		-ex "monitor reset init"

debug:
	$(info $(YELLOW)[Debug]$(RESET)  Step 1: run $(BOLD)make gdbserver$(RESET) in terminal A)
	$(info $(YELLOW)[Debug]$(RESET)  Step 2: run $(BOLD)make gdb$(RESET)        in terminal B)

# =============================================================================
#  Serial
# =============================================================================

serial:
	$(info $(CYAN)[Serial]$(RESET)  Opening $(BOLD)$(SERIAL_PORT)$(RESET) @ $(BOLD)$(SERIAL_BAUD)$(RESET) baud)
	$(PYTHON) -m serial.tools.miniterm $(SERIAL_PORT) $(SERIAL_BAUD) --raw

# =============================================================================
#  Toolchain / diagnostics
# =============================================================================

toolchain:
	$(info )
	$(info $(BOLD)Project$(RESET))
	$(info   $(CYAN)Name$(RESET)      $(PROJECT))
	$(info   $(CYAN)MCU$(RESET)       $(_MCU_NAME))
	$(info   $(CYAN)Linker$(RESET)    $(LDSCRIPT))
	$(info   $(CYAN)Flash$(RESET)     $(FLASH_SIZE) B)
	$(info   $(CYAN)RAM$(RESET)       $(RAM_SIZE) B)
	$(info   $(CYAN)OpenOCD$(RESET)   $(OPENOCD_CFG))
	$(info   $(CYAN)pyOCD$(RESET)     $(PYOCD_TARGET))
	$(info )
	@$(PYTHON) tools/scripts/show_tool_summary.py

check-tools:
	$(info $(CYAN)[Check]$(RESET)  Toolchain binaries:)
	cmake --version
	ninja --version
	$(CC) --version
	$(OBJCOPY) --version
	$(SIZE) --version
	$(GDB) --version

# =============================================================================
#  Preset shortcuts
# =============================================================================

debug-preset:
	$(MAKE) PRESET=Debug all

release:
	$(MAKE) PRESET=Release all

relwithdebinfo:
	$(MAKE) PRESET=RelWithDebInfo all

minsizerel:
	$(MAKE) PRESET=MinSizeRel all

list-presets:
	cmake --list-presets

# =============================================================================
#  Help
# =============================================================================

help: toolchain
	$(info $(BOLD)Usage:$(RESET)  make [target] [PRESET=Debug|Release|RelWithDebInfo|MinSizeRel])
	$(info )
	$(info $(BOLD)--- Build ---------------------------------------------------$(RESET))
	$(info   $(CYAN)make$(RESET)              configure + compile + hex/bin + summary  $(DIM)[default]$(RESET))
	$(info   $(CYAN)make elf$(RESET)          ELF only)
	$(info   $(CYAN)make hex$(RESET)          Intel HEX)
	$(info   $(CYAN)make bin$(RESET)          Binary)
	$(info   $(CYAN)make configure$(RESET)    CMake configure only)
	$(info   $(CYAN)make rebuild$(RESET)      clean then build)
	$(info )
	$(info $(BOLD)--- Clean ---------------------------------------------------$(RESET))
	$(info   $(CYAN)make clean$(RESET)        remove build/$(PRESET)/)
	$(info   $(CYAN)make distclean$(RESET)    remove entire build/ + compile_commands.json)
	$(info   $(CYAN)make uninstall$(RESET)    remove downloaded tools and generated setup state)
	$(info )
	$(info $(BOLD)--- Analysis ------------------------------------------------$(RESET))
	$(info   $(CYAN)make size$(RESET)         Flash/RAM usage)
	$(info   $(CYAN)make nm$(RESET)           top 40 symbols by size)
	$(info   $(CYAN)make symbols$(RESET)      dump full symbol table (.sym))
	$(info   $(CYAN)make lss$(RESET)          disassembly with source (.lss))
	$(info   $(CYAN)make disasm$(RESET)       disassembly to stdout)
	$(info )
	$(info $(BOLD)--- Flash ---------------------------------------------------$(RESET))
	$(info   $(CYAN)make flash$(RESET)        flash  (FLASH_TOOL=$(FLASH_TOOL)))
	$(info   $(CYAN)make flash-openocd$(RESET)    via OpenOCD + ST-Link/CMSIS-DAP)
	$(info   $(CYAN)make flash-cubeprog$(RESET)   via STM32CubeProgrammer)
	$(info   $(CYAN)make flash-pyocd$(RESET)      via pyOCD (target: $(PYOCD_TARGET)))
	$(info   $(CYAN)make flash-jlink$(RESET)      via J-Link (device: $(JLINK_DEVICE)))
	$(info   $(CYAN)make erase$(RESET)        mass erase)
	$(info   $(CYAN)make reset$(RESET)        reset MCU)
	$(info )
	$(info $(BOLD)--- Debug ---------------------------------------------------$(RESET))
	$(info   $(CYAN)make gdbserver$(RESET)    start OpenOCD GDB server :3333)
	$(info   $(CYAN)make gdb$(RESET)          connect GDB to OpenOCD)
	$(info )
	$(info $(BOLD)--- Presets -------------------------------------------------$(RESET))
	$(info   $(CYAN)make debug-preset$(RESET)    PRESET=Debug)
	$(info   $(CYAN)make release$(RESET)         PRESET=Release)
	$(info   $(CYAN)make relwithdebinfo$(RESET)  PRESET=RelWithDebInfo)
	$(info   $(CYAN)make minsizerel$(RESET)      PRESET=MinSizeRel)
	$(info   $(CYAN)make toolchain$(RESET)       show project + development tools summary)
	$(info )
	$(info $(BOLD)--- Setup ---------------------------------------------------$(RESET))
	$(info   $(CYAN)make setup$(RESET)           parallel setup: prefer system toolchain, install debug tools)
	$(info   $(CYAN)make setup-toolchain$(RESET) use system Arm GNU Toolchain if available, otherwise download)
	$(info   $(CYAN)make setup-openocd$(RESET)   download xPack OpenOCD only)
	$(info   $(CYAN)make setup-python-tools$(RESET) install pyOCD + pyserial only)
	$(info   $(CYAN)make setup-jlink$(RESET)     download J-Link Software Pack ($(JLINK_SETUP_VERSION)))
	$(info )
	$(info $(BOLD)--- New project ---------------------------------------------$(RESET))
	$(info   $(CYAN)make new-project NAME=my_blinky$(RESET))
	$(info         Create a sibling project that reuses this template's tools)
	$(info )
	$(info $(BOLD)--- Examples ------------------------------------------------$(RESET))
	$(info   make PRESET=Release)
	$(info   make setup SETUP_JOBS=6)
	$(info   make uninstall UNINSTALL_DRY_RUN=1)
	$(info   make flash FLASH_TOOL=cubeprog)
	$(info   make flash-pyocd PYOCD_TARGET=stm32f334x8)
	$(info   make rebuild PRESET=MinSizeRel)
	$(info   make serial SERIAL_PORT=COM5)
	$(info )
