SHELL := /usr/bin/env bash
.DEFAULT_GOAL := build
UE_ROOT ?= /home/louis/src/unreal-engine/engine
PROJECT := $(CURDIR)/LiveTerrain.uproject
CESIUM := $(CURDIR)/Plugins/CesiumForUnreal
CESIUM_COMMIT := 7792b53600262735361532a67f8fb8f3b3bb0f16
JOBS ?= 4
UE_CONFIG ?= Development
NATIVE_CONFIG ?= Release
SDK ?= $(firstword $(wildcard $(UE_ROOT)/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v26_clang-*/x86_64-unknown-linux-gnu))
# Use the Nix FHS wrapper on NixOS; UE_WRAPPER= disables it elsewhere.
UE_WRAPPER ?= $(if $(wildcard /etc/NIXOS),nix run $(CURDIR)\#ue-shell --,)
export UNREAL_ENGINE_ROOT := $(UE_ROOT)
export UNREAL_ENGINE_COMPILER_DIR := $(SDK)
DATA ?= geodata-zone48_5911-2_3376-5km
OUTPUT ?= Prepared/zone-48_5911-2_3376-tree-meshes
FEATURE_BASE ?= Prepared/zone-48_5911-2_3376-features
FEATURES ?= Prepared/scene-features-48_5911-2_3376.geojson
HEIGHT_OFFSET ?=

.PHONY: setup check-engine cesium native build run editor clean clean-native rebuild prepare trees test validate doctor
check-engine:
	@test -x "$(UE_ROOT)/Engine/Binaries/Linux/UnrealEditor" || { echo 'Set UE_ROOT to the UE 5.8 Linux installation'; exit 1; }
	@test -x "$(SDK)/bin/clang++" || { echo 'Missing UE SDK: set SDK to the bundled x86_64 toolchain'; exit 1; }
	@python -c 'import json; v=json.load(open("$(UE_ROOT)/Engine/Build/Build.version")); assert (v["MajorVersion"],v["MinorVersion"]) == (5,8), "UE 5.8 required"'

$(CESIUM)/.git:
	git clone --branch v2.31.0 --depth 1 --recursive --shallow-submodules https://github.com/CesiumGS/cesium-unreal.git "$(CESIUM)"

setup: check-engine $(CESIUM)/.git
	@test "$$(git -C "$(CESIUM)" rev-parse HEAD)" = "$(CESIUM_COMMIT)" || { echo 'Unexpected Cesium revision; preserve local changes and restore v2.31.0'; exit 1; }
	git -C "$(CESIUM)" submodule update --init --recursive
	python tools/configure_cesium.py --engine "$(UE_ROOT)" --plugin "$(CESIUM)"

native: setup
	@mkdir -p ".build/native-$(NATIVE_CONFIG)"
	@if [[ ! -f ".build/native-$(NATIVE_CONFIG)/CMakeCache.txt" || Makefile -nt ".build/native-$(NATIVE_CONFIG)/configuration" || "$$(cat ".build/native-$(NATIVE_CONFIG)/configuration" 2>/dev/null)" != "$(UE_ROOT)|$(SDK)|$(NATIVE_CONFIG)|$(CESIUM_COMMIT)" ]]; then \
	  $(UE_WRAPPER) cmake -S "$(CESIUM)/extern" -B "$(CURDIR)/.build/native-$(NATIVE_CONFIG)" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$(CESIUM)/extern/unreal-linux-toolchain.cmake" -DCMAKE_BUILD_TYPE=$(NATIVE_CONFIG) -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCESIUM_TESTS_ENABLED=ON -Wno-dev && \
	  printf '%s' '$(UE_ROOT)|$(SDK)|$(NATIVE_CONFIG)|$(CESIUM_COMMIT)' > ".build/native-$(NATIVE_CONFIG)/configuration"; \
	fi
	$(UE_WRAPPER) cmake --build "$(CURDIR)/.build/native-$(NATIVE_CONFIG)" --target install --parallel $(JOBS)

# UBT builds Cesium's C++ modules as project plugins, then the application.
# Native CMake and UBT both retain their incremental dependency graphs.
cesium: native
	$(UE_WRAPPER) bash "$(UE_ROOT)/Engine/Build/BatchFiles/RunUBT.sh" LiveTerrainEditor Linux $(UE_CONFIG) -Project="$(PROJECT)" -NoHotReloadFromIDE $(ARGS)

build: cesium

run: build
	$(UE_WRAPPER) "$(UE_ROOT)/Engine/Binaries/Linux/UnrealEditor" "$(PROJECT)" /Engine/Maps/Entry -DDC=Offline -game -log -GeoData="$(abspath $(OUTPUT))" $(RUN_ARGS)

editor: build
	$(UE_WRAPPER) "$(UE_ROOT)/Engine/Binaries/Linux/UnrealEditor" "$(PROJECT)" /Engine/Maps/Entry -DDC=Offline -log -GeoData="$(abspath $(OUTPUT))" $(RUN_ARGS)

prepare:
	@test -n "$(HEIGHT_OFFSET)" || { echo 'Specify HEIGHT_OFFSET in metres (0 explicitly selects approximate vertical alignment)'; exit 1; }
	python tools/geodata/prepare.py --input "$(DATA)" --output "$(OUTPUT)-baked" --height-offset "$(HEIGHT_OFFSET)" $(if $(wildcard $(FEATURES)),--features "$(FEATURES)",) $(PREPARE_ARGS)
	python tools/geodata/native_trees.py --base "$(OUTPUT)-baked" --output "$(OUTPUT)"

trees:
	python tools/geodata/native_trees.py --base "$(FEATURE_BASE)" --output "$(OUTPUT)"

validate:
	python tools/geodata/validate.py "$(OUTPUT)"

test:
	python -m unittest discover -s tests -v

doctor: check-engine
	@command -v git cmake ninja python
	@git -C "$(CESIUM)" rev-parse HEAD
	@python -c 'import rasterio, pyproj, numpy, shapely; print("Geodata dependencies OK")'

clean:
	rm -rf Binaries Intermediate

clean-native:
	rm -rf .build/native-* "$(CESIUM)/Source/ThirdParty/lib" "$(CESIUM)/Binaries" "$(CESIUM)/Intermediate"

rebuild:
	$(MAKE) clean
	$(MAKE) clean-native
	$(MAKE) build
