PG ?= 16
SUPPORTED_PG := $(patsubst pg%/sources.toml,%,$(wildcard pg*/sources.toml))
ifeq ($(filter $(PG),$(SUPPORTED_PG)),)
$(error Unsupported PostgreSQL version "$(PG)"; available: $(SUPPORTED_PG))
endif

.NOTPARALLEL:
.DEFAULT_GOAL := check
.PHONY: check

check:
	$(MAKE) -C pg$(PG) check

.DEFAULT:
	$(MAKE) -C pg$(PG) $@
