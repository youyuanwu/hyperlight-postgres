.NOTPARALLEL:
.PHONY: setup preflight build-preflight fetch runtime-build process-test-native process-test-native-built process-test-guest process-test check pg-kernel pg-native pg-guest pg-package sql-test ram-demo storage-build storage-package storage-init storage-config storage-test persistent-demo recovery-test benchmark-build benchmark read-benchmark validate validate-built

SHARED_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

setup:
	$(MAKE) preflight fetch pg-native pg-guest storage-build storage-package storage-init check

preflight build-preflight fetch:
	python3 "$(SHARED_DIR)/runtime.py" $@

runtime-build:
	python3 "$(SHARED_DIR)/runtime.py" build

process-test-native:
	python3 "$(SHARED_DIR)/runtime.py" test-native

process-test-native-built:
	python3 "$(SHARED_DIR)/runtime.py" test-native-built

process-test-guest:
	python3 "$(SHARED_DIR)/runtime.py" test-guest

check:
	cd "$(SHARED_DIR)" && python3 -m unittest discover -s tests -p 'test_*.py'

process-test:
	$(MAKE) preflight fetch runtime-build check process-test-native process-test-guest

pg-kernel:
	python3 "$(SHARED_DIR)/postgres.py" kernel

pg-native:
	python3 "$(SHARED_DIR)/postgres.py" build-native

pg-guest:
	python3 "$(SHARED_DIR)/postgres.py" build-guest

pg-package:
	python3 "$(SHARED_DIR)/postgres.py" package

sql-test:
	python3 "$(SHARED_DIR)/postgres.py" test

ram-demo:
	$(MAKE) preflight fetch
	$(MAKE) pg-kernel pg-native pg-guest pg-package check sql-test

storage-build:
	python3 "$(SHARED_DIR)/storage.py" build

storage-package:
	python3 "$(SHARED_DIR)/storage.py" package

storage-init:
	python3 "$(SHARED_DIR)/storage.py" init

storage-config:
	python3 "$(SHARED_DIR)/storage.py" configure

storage-test:
	python3 "$(SHARED_DIR)/storage.py" test

persistent-demo:
	$(MAKE) preflight fetch storage-build storage-package storage-config check storage-test

recovery-test:
	python3 "$(SHARED_DIR)/recovery.py" test

benchmark-build:
	python3 "$(SHARED_DIR)/evaluate.py" build

benchmark:
	python3 "$(SHARED_DIR)/evaluate.py" test

read-benchmark:
	python3 "$(SHARED_DIR)/read_benchmark.py" test

validate:
	$(MAKE) process-test ram-demo persistent-demo recovery-test benchmark-build benchmark read-benchmark

validate-built:
	$(MAKE) check process-test-native-built process-test-guest pg-package storage-package storage-init sql-test storage-test recovery-test benchmark read-benchmark
