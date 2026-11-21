# PostgreSQL on Hyperlight-Unikraft

The PostgreSQL 16 implementation lives in [pg16/](pg16/).
See its [build and validation documentation](pg16/README.md) and
[performance report](pg16/RESULTS.md).

Run the sample from that directory:

```sh
cd pg16
make setup
make persistent-demo
```

Run the helper tests from the repository root with `make -C pg16 check`.
